"""Pydantic request/response models for the HTTP API.

WHY: The FastAPI endpoints need typed schemas for request validation,
response serialization, and automatic OpenAPI documentation. Pydantic
models enforce field types at runtime and generate JSON Schema that
appears in the /docs UI.

HOW: Each endpoint pair (request + response) has its own model. Enums
represent closed sets like output format names. All models include
Field descriptions for rich OpenAPI docs.

RULES:
- All models use Field(description=...) for OpenAPI documentation
- Enum values match internal constants exactly (format keys)
- Response models never expose internal implementation details
- Python 3.9+ compatible (no PEP 604 unions, use Optional from typing)
- JobStatus is imported from server.jobs (single source of truth)
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OutputFormat(str, Enum):
    """Available output format identifiers.

    WHY: Clients specify desired formats when submitting a job. The enum
    ensures only registered formatter keys are accepted.

    RULES:
    - Values match keys in soniox_converter.formatters.FORMATTERS exactly
    """

    premiere_pro = "premiere_pro"
    plain_text = "plain_text"
    kinetic_words = "kinetic_words"
    srt_captions = "srt_captions"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class TranscriptionConfig(BaseModel):
    """Configuration fields sent alongside the uploaded file.

    WHY: Clients need to control language settings, diarization, and
    output format selection. These fields are sent as form data alongside
    the multipart file upload.

    RULES:
    - primary_language defaults to "sv" (Swedish)
    - secondary_language is optional (for code-switching)
    - diarization defaults to True
    - output_formats defaults to all available formats
    """

    primary_language: str = Field(
        default="sv",
        description="Primary language ISO 639-1 code (e.g. 'sv', 'en').",
    )
    secondary_language: Optional[str] = Field(
        default=None,
        description="Secondary language ISO 639-1 code for code-switching detection.",
    )
    diarization: bool = Field(
        default=True,
        description="Enable speaker diarization.",
    )
    output_formats: Optional[List[OutputFormat]] = Field(
        default=None,
        description="Output formats to generate. Defaults to all available formats.",
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class JobResponse(BaseModel):
    """Transcription job status response.

    WHY: Clients poll this endpoint to track job progress. It exposes
    the current state, timing, and error information.

    RULES:
    - id is the job UUID
    - status reflects the current pipeline stage
    - error is only set when status is 'failed'
    - output_files is only populated when status is 'completed'
    """

    id: str = Field(description="Unique job identifier (UUID).")
    status: str = Field(description="Current job status.")
    filename: str = Field(description="Original uploaded filename.")
    created_at: float = Field(description="Job creation timestamp (Unix epoch seconds).")
    config: Dict[str, Any] = Field(description="Transcription configuration used for this job.")
    error: Optional[str] = Field(
        default=None,
        description="Error message, only present when status is 'failed'.",
    )
    output_files: Optional[List[str]] = Field(
        default=None,
        description="List of output filenames, only present when status is 'completed'.",
    )

    model_config = {"json_schema_extra": {
        "examples": [
            {
                "id": "550e8400e29b41d4a716446655440000",
                "status": "transcribing",
                "filename": "interview.mp3",
                "created_at": 1739959200.0,
                "config": {
                    "primary_language": "sv",
                    "secondary_language": "en",
                    "diarization": True,
                    "output_formats": ["premiere_pro", "srt_captions"],
                },
                "error": None,
                "output_files": None,
            }
        ]
    }}


class JobCreatedResponse(BaseModel):
    """Response returned when a new transcription job is submitted.

    WHY: After accepting a file upload, the API returns the job ID and
    initial status so clients can start polling immediately.

    RULES:
    - id is the job UUID for subsequent polling/download
    - status is always 'pending' on creation
    """

    id: str = Field(description="Unique job identifier (UUID) for polling status.")
    status: str = Field(description="Initial job status (always 'pending').")
    filename: str = Field(description="Original uploaded filename.")

    model_config = {"json_schema_extra": {
        "examples": [
            {
                "id": "550e8400e29b41d4a716446655440000",
                "status": "pending",
                "filename": "interview.mp3",
            }
        ]
    }}


class FileInfo(BaseModel):
    """Metadata for a single output file.

    WHY: The file listing endpoint returns metadata (not content) for
    each output file so clients know what's available before downloading.
    """

    filename: str = Field(description="Output filename.")
    media_type: str = Field(description="MIME type of the file content.")
    size: int = Field(description="File size in bytes.")


class FileListResponse(BaseModel):
    """List of output files for a completed job.

    WHY: Clients need to discover available output files before
    downloading specific ones.
    """

    job_id: str = Field(description="The job ID these files belong to.")
    files: List[FileInfo] = Field(description="Available output files.")


class FormatInfo(BaseModel):
    """Description of an available output format.

    WHY: Clients can query the /formats endpoint to discover which
    output formats are supported and what they produce.
    """

    key: str = Field(description="Format identifier used in API requests.")
    name: str = Field(description="Human-readable format name.")
    suffix: str = Field(description="File suffix produced (e.g. '-transcript.json').")


class ErrorResponse(BaseModel):
    """Standard error response body.

    WHY: All error responses use the same schema for consistent
    client-side error handling.

    RULES:
    - detail is always a human-readable error message
    """

    detail: str = Field(description="Human-readable error description.")


class HealthResponse(BaseModel):
    """Health check response.

    WHY: Load balancers and orchestrators need a simple endpoint
    to verify the service is alive and ready.
    """

    status: str = Field(description="Service health status.", json_schema_extra={"example": "ok"})
    version: str = Field(description="API version string.", json_schema_extra={"example": "0.1.0"})
