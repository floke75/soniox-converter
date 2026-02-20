"""FastAPI application with transcription API routes and OpenAPI docs.

WHY: External clients (Slack bot, curl, n8n, future tools) need an HTTP
API to submit audio files for transcription, poll for status, and
download output files. FastAPI provides automatic OpenAPI documentation,
request validation, and background task support.

HOW: A single FastAPI app exposes 7 endpoints grouped by tags. The POST
/transcriptions endpoint accepts a multipart file upload with JSON config
fields, creates a job, and runs the full Soniox pipeline in the background.
Other endpoints provide polling, file download, format listing, and health.

RULES:
- All endpoints have OpenAPI descriptions on every parameter and response
- Error responses use a consistent ErrorResponse schema
- Background transcription uses FastAPI BackgroundTasks
- The job store is a singleton created at app startup
- File validation checks extension against SONIOX_SUPPORTED_FORMATS
- Python 3.9+ compatible (no match/case, no PEP 604 unions)
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from soniox_converter.config import SONIOX_SUPPORTED_FORMATS
from soniox_converter.core.context import build_context
from soniox_converter.formatters import FORMATTERS
from soniox_converter.server.jobs import Job, JobStatus, JobStore
from soniox_converter.server.models import (
    ErrorResponse,
    FileInfo,
    FileListResponse,
    FormatInfo,
    HealthResponse,
    JobCreatedResponse,
    JobResponse,
    OutputFormat,
    TranscriptionConfig,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App and store setup
# ---------------------------------------------------------------------------

job_store = JobStore()


async def _periodic_cleanup() -> None:
    """Run job cleanup every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        job_store.cleanup_expired()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start periodic cleanup on startup, cancel on shutdown."""
    task = asyncio.create_task(_periodic_cleanup())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    lifespan=lifespan,
    title="Soniox Transcript Converter API",
    description=(
        "REST API for transcribing audio/video files using Soniox ASR "
        "and producing multiple output formats (Premiere Pro JSON, SRT, "
        "plain text, kinetic word reveal). Submit a file, poll for status, "
        "and download results."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job_to_response(job: Job) -> JobResponse:
    """Convert an internal Job dataclass to a JobResponse Pydantic model."""
    return JobResponse(
        id=job.id,
        status=job.status.value,
        filename=job.filename,
        created_at=job.created_at,
        config=job.config,
        error=job.error,
        output_files=job.output_files if job.output_files else None,
    )


def _validate_file_extension(filename: str) -> None:
    """Raise HTTPException if the file extension is not supported."""
    ext = Path(filename).suffix.lower()
    if ext not in SONIOX_SUPPORTED_FORMATS:
        sorted_formats = sorted(SONIOX_SUPPORTED_FORMATS)
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type '{}'. Supported formats: {}".format(
                ext, ", ".join(sorted_formats)
            ),
        )


async def _run_transcription_pipeline(job_id: str, store: JobStore) -> None:
    """Run the full Soniox transcription pipeline for a job.

    WHY: This is the background task that processes an uploaded file through
    the complete pipeline: upload to Soniox → create transcription → poll →
    fetch tokens → assemble words → run formatters → save output files.

    HOW: Reads the uploaded file from the job's output_dir, runs each pipeline
    step, and updates job status at each stage. On completion, output files
    are saved to the job's output_dir. On failure, the job is marked failed.

    RULES:
    - Updates job status at each pipeline stage
    - Catches all exceptions and marks job as failed
    - Cleans up Soniox resources (file + transcription) in a finally block
    - Output files are saved to the job's output_dir
    """
    from soniox_converter.api.client import SonioxClient
    from soniox_converter.core.assembler import (
        assemble_tokens,
        build_transcript,
        filter_translation_tokens,
    )

    job = store.get_job(job_id)
    if job is None:
        return

    input_path = job.output_dir / job.filename
    config = job.config
    file_id = None
    transcription_id = None

    try:
        # Determine format keys
        format_keys = config.get("output_formats")
        if not format_keys:
            format_keys = list(FORMATTERS.keys())

        # Build language hints
        language_hints = [config.get("primary_language", "sv")]
        secondary = config.get("secondary_language")
        if secondary:
            language_hints.append(secondary)

        enable_diarization = config.get("diarization", True)

        async with SonioxClient() as client:
            # Upload
            store.update_job(job_id, status=JobStatus.UPLOADING)
            file_id = await client.upload_file(input_path)

            # Create transcription (with optional context)
            store.update_job(job_id, status=JobStatus.TRANSCRIBING)
            transcription_id = await client.create_transcription(
                file_id=file_id,
                language_hints=language_hints,
                enable_diarization=enable_diarization,
                enable_language_identification=True,
                script_text=config.get("script_text"),
                terms=config.get("terms"),
                general_context=config.get("general_context"),
            )

            # Poll until complete
            await client.poll_until_complete(transcription_id)

            # Fetch transcript
            store.update_job(job_id, status=JobStatus.CONVERTING)
            tokens = await client.fetch_transcript(transcription_id)

            # Assemble tokens into words
            token_dicts = [
                {
                    "text": t.text,
                    "start_ms": t.start_ms,
                    "end_ms": t.end_ms,
                    "confidence": t.confidence,
                    "speaker": t.speaker,
                    "language": t.language,
                    "translation_status": t.translation_status,
                }
                for t in tokens
            ]
            filtered = filter_translation_tokens(token_dicts)
            words = assemble_tokens(filtered)

            # Build Transcript IR
            transcript = build_transcript(words, job.filename)

            # Run formatters and save output files
            output_filenames = []
            for key in format_keys:
                if key not in FORMATTERS:
                    continue
                formatter = FORMATTERS[key]()
                outputs = formatter.format(transcript)
                for output in outputs:
                    stem = Path(job.filename).stem
                    out_filename = "{}{}".format(stem, output.suffix)
                    out_path = job.output_dir / out_filename
                    if isinstance(output.content, bytes):
                        out_path.write_bytes(output.content)
                    else:
                        out_path.write_text(output.content, encoding="utf-8")
                    output_filenames.append(out_filename)

            store.update_job(
                job_id,
                status=JobStatus.COMPLETED,
                output_files=output_filenames,
            )

            # Cleanup Soniox resources
            await client.cleanup(transcription_id, file_id)

    except Exception as exc:
        logger.exception("Transcription pipeline failed for job %s", job_id)
        store.update_job(job_id, status=JobStatus.FAILED, error=str(exc))

        # Best-effort Soniox cleanup
        if file_id or transcription_id:
            try:
                async with SonioxClient() as cleanup_client:
                    if transcription_id and file_id:
                        await cleanup_client.cleanup(transcription_id, file_id)
            except Exception:
                pass


def _run_transcription_sync(job_id: str, store: JobStore) -> None:
    """Synchronous wrapper for the async transcription pipeline.

    WHY: FastAPI BackgroundTasks run synchronous callables. This wraps
    the async pipeline with asyncio.run().
    """
    asyncio.run(_run_transcription_pipeline(job_id, store))


# ---------------------------------------------------------------------------
# Endpoints: Transcriptions
# ---------------------------------------------------------------------------


@app.post(
    "/transcriptions",
    response_model=JobCreatedResponse,
    status_code=201,
    tags=["transcriptions"],
    summary="Submit a transcription job",
    description=(
        "Upload an audio or video file with transcription configuration. "
        "Returns a job ID immediately. The transcription runs in the background. "
        "Poll GET /transcriptions/{id} for status updates."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Invalid file type or configuration"},
        429: {"model": ErrorResponse, "description": "Too many concurrent jobs"},
    },
)
async def create_transcription(
    background_tasks: BackgroundTasks,
    file: Annotated[
        UploadFile,
        File(description="Audio or video file to transcribe"),
    ],
    primary_language: Annotated[
        str,
        Form(description="Primary language ISO 639-1 code (e.g. 'sv', 'en')."),
    ] = "sv",
    secondary_language: Annotated[
        Optional[str],
        Form(description="Secondary language ISO 639-1 code for code-switching."),
    ] = None,
    diarization: Annotated[
        bool,
        Form(description="Enable speaker diarization."),
    ] = True,
    output_formats: Annotated[
        Optional[str],
        Form(
            description=(
                "Comma-separated output formats. Available: premiere_pro, "
                "plain_text, kinetic_words, srt_captions. Defaults to all."
            )
        ),
    ] = None,
    context_file: Annotated[
        Optional[UploadFile],
        File(description="Optional .txt file containing script/prompter text for context."),
    ] = None,
    terms: Annotated[
        Optional[str],
        Form(
            description=(
                "Comma-separated domain terms to improve transcription accuracy "
                "(e.g. 'Melodifestivalen, SVT, EFN')."
            )
        ),
    ] = None,
    general_context: Annotated[
        Optional[str],
        Form(
            description=(
                "Comma-separated key:value pairs for general context "
                "(e.g. 'domain:Media, topic:Music')."
            )
        ),
    ] = None,
) -> JobCreatedResponse:
    # Sanitize filename to prevent path traversal
    raw_filename = file.filename or "upload"
    filename = Path(raw_filename).name
    _validate_file_extension(filename)

    # Parse and validate output formats
    format_keys = None  # type: Optional[List[str]]
    if output_formats:
        format_keys = [f.strip() for f in output_formats.split(",")]
        for key in format_keys:
            if key not in FORMATTERS:
                available = ", ".join(sorted(FORMATTERS.keys()))
                raise HTTPException(
                    status_code=400,
                    detail="Unknown output format '{}'. Available: {}".format(
                        key, available
                    ),
                )

    # Parse context parameters
    terms_list = None  # type: Optional[List[str]]
    if terms:
        terms_list = [t.strip() for t in terms.split(",") if t.strip()]

    general_list = None  # type: Optional[List[dict]]
    if general_context:
        general_list = []
        for pair in general_context.split(","):
            pair = pair.strip()
            if ":" in pair:
                key, value = pair.split(":", 1)
                general_list.append({"key": key.strip(), "value": value.strip()})

    script_text = None  # type: Optional[str]
    if context_file:
        if not context_file.filename or not context_file.filename.endswith(".txt"):
            raise HTTPException(
                status_code=422,
                detail="Context file must be .txt format",
            )
        content_bytes = await context_file.read()
        script_text = content_bytes.decode("utf-8")

    # Validate context size using build_context from core.context
    try:
        context = build_context(
            script_text=script_text,
            terms=terms_list,
            general=general_list,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Context too large: {}".format(exc))
    if context:
        context_str = json.dumps(context)
        if len(context_str) > 10000:
            raise HTTPException(
                status_code=422,
                detail="Context too large ({} chars, max 10,000)".format(
                    len(context_str)
                ),
            )

    # Build config dict for the job store
    config = {
        "primary_language": primary_language,
        "secondary_language": secondary_language,
        "diarization": diarization,
        "output_formats": format_keys,
        "script_text": script_text,
        "terms": terms_list,
        "general_context": general_list,
    }

    # Create job
    try:
        job = job_store.create_job(filename=filename, config=config)
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc))

    # Save uploaded file to the job's output directory
    input_path = job.output_dir / filename
    content = await file.read()
    input_path.write_bytes(content)

    # Launch background transcription
    background_tasks.add_task(_run_transcription_sync, job.id, job_store)

    return JobCreatedResponse(
        id=job.id,
        status=job.status.value,
        filename=job.filename,
    )


@app.get(
    "/transcriptions/{job_id}",
    response_model=JobResponse,
    tags=["transcriptions"],
    summary="Get transcription job status",
    description=(
        "Poll this endpoint to track the progress of a transcription job. "
        "Returns current status, configuration, and output files when complete."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Job not found"},
    },
)
async def get_transcription(
    job_id: str,
) -> JobResponse:
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found: {}".format(job_id))
    return _job_to_response(job)


@app.get(
    "/transcriptions/{job_id}/files",
    response_model=FileListResponse,
    tags=["transcriptions"],
    summary="List output files for a completed job",
    description=(
        "Returns metadata for all output files produced by a completed "
        "transcription job. Use the filenames to download individual files."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Job not found"},
        409: {"model": ErrorResponse, "description": "Job not yet completed"},
    },
)
async def list_transcription_files(
    job_id: str,
) -> FileListResponse:
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found: {}".format(job_id))

    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="Job is not completed (current status: {}).".format(job.status.value),
        )

    files = []
    for fname in job.output_files:
        fpath = job.output_dir / fname
        if fpath.exists():
            # Infer media type from suffix
            media_type = _infer_media_type(fname)
            files.append(FileInfo(
                filename=fname,
                media_type=media_type,
                size=fpath.stat().st_size,
            ))

    return FileListResponse(job_id=job.id, files=files)


@app.get(
    "/transcriptions/{job_id}/files/{filename}",
    tags=["transcriptions"],
    summary="Download a single output file",
    description=(
        "Download a specific output file from a completed transcription job. "
        "The filename must match one of the files listed in the job's output_files."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Job or file not found"},
        409: {"model": ErrorResponse, "description": "Job not yet completed"},
    },
)
async def download_transcription_file(
    job_id: str,
    filename: str,
) -> Response:
    # Ensure filename doesn't contain path separators
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(
            status_code=400,
            detail="Invalid filename",
        )

    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found: {}".format(job_id))

    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="Job is not completed (current status: {}).".format(job.status.value),
        )

    if filename not in job.output_files:
        raise HTTPException(
            status_code=404,
            detail="File '{}' not found in job output files.".format(filename),
        )

    fpath = job.output_dir / filename
    if not fpath.exists():
        raise HTTPException(
            status_code=404,
            detail="File '{}' not found on disk.".format(filename),
        )

    content = fpath.read_bytes()
    media_type = _infer_media_type(filename)

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": 'attachment; filename="{}"'.format(filename)},
    )


@app.delete(
    "/transcriptions/{job_id}",
    status_code=204,
    tags=["transcriptions"],
    summary="Delete a transcription job",
    description=(
        "Delete a transcription job and all its output files. "
        "Can be used to cancel a pending job or clean up after downloading."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Job not found"},
    },
)
async def delete_transcription(
    job_id: str,
) -> Response:
    deleted = job_store.delete_job(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found: {}".format(job_id))
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Endpoints: Formats
# ---------------------------------------------------------------------------


@app.get(
    "/formats",
    response_model=List[FormatInfo],
    tags=["formats"],
    summary="List available output formats",
    description=(
        "Returns all supported output formats with their identifiers, "
        "human-readable names, and file suffixes."
    ),
)
async def list_formats() -> List[FormatInfo]:
    result = []
    for key, formatter_cls in sorted(FORMATTERS.items()):
        formatter = formatter_cls()
        # Get the first output's suffix as representative
        suffix = ""
        try:
            from soniox_converter.core.ir import Transcript
            # Create a minimal transcript to get the suffix
            dummy = Transcript(
                segments=[],
                speakers=[],
                primary_language="",
                source_filename="dummy.mp3",
                duration_s=0.0,
            )
            outputs = formatter.format(dummy)
            if outputs:
                suffix = outputs[0].suffix
        except Exception:
            suffix = ""

        result.append(FormatInfo(
            key=key,
            name=formatter.name,
            suffix=suffix,
        ))
    return result


# ---------------------------------------------------------------------------
# Endpoints: Health
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["health"],
    summary="Health check",
    description="Liveness and readiness check for load balancers and orchestrators.",
)
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")


# ---------------------------------------------------------------------------
# Helpers (private)
# ---------------------------------------------------------------------------


def run_api():
    """Entry point for the soniox-api console script."""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


def _infer_media_type(filename: str) -> str:
    """Infer MIME type from filename extension.

    RULES:
    - .json → application/json
    - .srt → text/plain
    - .txt → text/plain
    - fallback → application/octet-stream
    """
    ext = Path(filename).suffix.lower()
    mapping = {
        ".json": "application/json",
        ".srt": "text/plain",
        ".txt": "text/plain",
        ".csv": "text/csv",
    }
    return mapping.get(ext, "application/octet-stream")
