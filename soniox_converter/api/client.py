"""Async HTTP client for the Soniox non-realtime speech-to-text API.

WHY: The converter needs to upload audio files, create transcription jobs,
poll for completion, fetch flat token arrays, and clean up resources. This
module encapsulates the full async workflow behind a single client class
so callers (CLI, GUI, tests) don't need to know HTTP details.

HOW: Uses httpx.AsyncClient for non-blocking HTTP. The SonioxClient is an
async context manager — enter it to get an authenticated client, exit to
close the connection pool. Each API step is a separate method:
upload_file → create_transcription → poll_until_complete → fetch_transcript → cleanup.

RULES:
- Always use the async context manager (async with SonioxClient(...) as client:)
- Default model is stt-async-v4 (latest, Jan 2026)
- Polling uses exponential backoff: 2s initial, 1.5x factor, 15s max, 60min timeout
- Context size is validated before sending (max ~10,000 chars ≈ 8,000 tokens)
- Always call cleanup() after processing to free Soniox storage
- Status callback (on_status) is optional; when provided, called with status strings
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from soniox_converter.api.models import (
    SonioxToken,
    TranscriptResponse,
    TranscriptionStatus,
)
from soniox_converter.config import SONIOX_BASE_URL, SONIOX_MODEL, load_api_key

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_POLL_INITIAL_INTERVAL_S = 2.0
_POLL_BACKOFF_FACTOR = 1.5
_POLL_MAX_INTERVAL_S = 15.0
_POLL_TIMEOUT_S = 60 * 60  # 60 minutes

_CONTEXT_MAX_CHARS = 10_000  # ~8,000 tokens at 1.25 chars/token


class SonioxAPIError(Exception):
    """Raised when the Soniox API returns an error response.

    WHY: Callers need a typed exception to distinguish Soniox errors from
    network errors or other failures.

    HOW: Wraps the HTTP status code and response body.

    RULES:
    - Always include status_code and message
    - message is the response body text or a summary
    """

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Soniox API error {status_code}: {message}")


class ContextTooLargeError(ValueError):
    """Raised when the assembled context exceeds the Soniox size limit.

    WHY: Soniox limits context to ~8,000 tokens (~10,000 characters). We
    validate before sending to give a clear, immediate error rather than
    a cryptic API rejection.

    HOW: Raised by _validate_context_size when total chars exceed the limit.

    RULES:
    - Message includes the actual size and the limit
    - Raised before any API call is made
    """


class TranscriptionError(Exception):
    """Raised when a transcription job enters the "error" status.

    WHY: The polling loop detects when Soniox reports the job failed. This
    exception carries the error message from the API.

    HOW: Raised by poll_until_complete when status is "error".

    RULES:
    - message contains the API's error_message field
    """


class TranscriptionTimeoutError(TimeoutError):
    """Raised when polling exceeds the maximum timeout.

    WHY: Prevents indefinite waiting on stuck or very long transcriptions.

    HOW: Raised by poll_until_complete when elapsed time exceeds 60 minutes.

    RULES:
    - Message includes the transcription ID and elapsed time
    """


class SonioxClient:
    """Async client for the Soniox non-realtime transcription API.

    WHY: Provides a clean, typed interface for the full transcription
    workflow: upload → create → poll → fetch → cleanup. Handles auth,
    backoff, context validation, and error wrapping.

    HOW: Wraps httpx.AsyncClient with Bearer token auth. Each API step
    is an async method. Use as an async context manager to ensure the
    HTTP connection pool is properly closed.

    RULES:
    - Use as: async with SonioxClient() as client: ...
    - api_key defaults to load_api_key() from .env
    - base_url defaults to SONIOX_BASE_URL from config
    - model defaults to SONIOX_MODEL from config
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key or load_api_key()
        self._base_url = (base_url or SONIOX_BASE_URL).rstrip("/")
        self._model = model or SONIOX_MODEL
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> SonioxClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx.Timeout(300.0, connect=30.0),
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        if self._client:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the active httpx client, raising if not in context manager."""
        if self._client is None:
            raise RuntimeError(
                "SonioxClient must be used as an async context manager: "
                "async with SonioxClient() as client: ..."
            )
        return self._client

    # ------------------------------------------------------------------
    # Step 1: Upload file
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        file_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Upload an audio/video file to Soniox and return the file_id.

        WHY: The async transcription workflow requires uploading the file
        first via POST /v1/files, which returns a file_id for use in
        create_transcription.

        HOW: Sends a multipart/form-data POST with the file content.
        The response JSON contains the file_id.

        RULES:
        - file_path must point to an existing file
        - Returns the file_id string from the response
        - Raises SonioxAPIError on non-2xx responses

        Args:
            file_path: Path to the audio/video file to upload.
            on_status: Optional callback for status updates.

        Returns:
            The file_id string assigned by Soniox.
        """
        client = self._ensure_client()
        if on_status:
            on_status("Uploading file...")

        file_path = Path(file_path)
        with open(file_path, "rb") as f:
            resp = await client.post(
                "/files",
                files={"file": (file_path.name, f)},
            )

        if resp.status_code not in (200, 201):
            raise SonioxAPIError(resp.status_code, resp.text)

        data = resp.json()
        return data["id"]

    # ------------------------------------------------------------------
    # Step 2: Create transcription
    # ------------------------------------------------------------------

    async def create_transcription(
        self,
        file_id: str,
        language_hints: list[str] | None = None,
        enable_diarization: bool = True,
        enable_language_identification: bool = True,
        script_text: str | None = None,
        terms: list[str] | None = None,
        general_context: list[dict] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Create a transcription job and return the transcription ID.

        WHY: After uploading a file, we create a transcription job via
        POST /v1/transcriptions. This configures the model, language
        hints, diarization, and optional context for better accuracy.

        HOW: Builds the request JSON with required and optional fields.
        If context parameters are provided, assembles them into the
        Soniox `context` object and validates total size before sending.

        RULES:
        - model defaults to stt-async-v4
        - language_hints: list of ISO 639-1 codes (e.g. ["sv", "en"])
        - Context size validated before sending (max ~10,000 chars)
        - Returns the transcription ID from the response
        - Raises ContextTooLargeError if context exceeds the limit
        - Raises SonioxAPIError on non-2xx responses

        Args:
            file_id: The file_id from upload_file().
            language_hints: ISO 639-1 language codes for the audio.
            enable_diarization: Whether to enable speaker diarization.
            enable_language_identification: Whether to enable language ID.
            script_text: Optional script text for the context.text field.
            terms: Optional list of domain vocabulary terms for context.terms.
            general_context: Optional list of {key, value} dicts for context.general.
            on_status: Optional callback for status updates.

        Returns:
            The transcription ID string.
        """
        client = self._ensure_client()
        if on_status:
            on_status("Creating transcription...")

        body: dict = {
            "model": self._model,
            "file_id": file_id,
            "enable_speaker_diarization": enable_diarization,
            "enable_language_identification": enable_language_identification,
        }

        if language_hints:
            body["language_hints"] = language_hints

        # Assemble optional context object
        context = _build_context(script_text, terms, general_context)
        if context:
            _validate_context_size(context)
            body["context"] = context
            if on_status:
                parts = []
                if "text" in context:
                    parts.append("script={} chars".format(len(context["text"])))
                if "terms" in context:
                    parts.append("{} terms".format(len(context["terms"])))
                on_status("  Sending context to Soniox: {}".format(", ".join(parts)))
        else:
            if on_status:
                on_status("  No context sent to Soniox.")

        resp = await client.post("/transcriptions", json=body)

        if resp.status_code not in (200, 201):
            raise SonioxAPIError(resp.status_code, resp.text)

        data = resp.json()
        return data["id"]

    # ------------------------------------------------------------------
    # Step 3: Poll until complete
    # ------------------------------------------------------------------

    async def poll_until_complete(
        self,
        transcription_id: str,
        on_status: Callable[[str], None] | None = None,
    ) -> TranscriptionStatus:
        """Poll a transcription job until it completes or fails.

        WHY: Soniox async transcription is not instant. The client must
        poll GET /v1/transcriptions/{id} until the status changes from
        "queued"/"processing" to "completed" or "error".

        HOW: Exponential backoff polling — starts at 2s intervals, grows
        by 1.5x per poll, capped at 15s. Total timeout is 60 minutes.
        Each poll response is checked for terminal states.

        RULES:
        - Returns TranscriptionStatus when status is "completed"
        - Raises TranscriptionError when status is "error"
        - Raises TranscriptionTimeoutError after 60 minutes
        - Calls on_status with human-readable status at each poll

        Args:
            transcription_id: The ID from create_transcription().
            on_status: Optional callback for status updates.

        Returns:
            TranscriptionStatus with status "completed".
        """
        client = self._ensure_client()
        interval = _POLL_INITIAL_INTERVAL_S
        start_time = time.monotonic()

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > _POLL_TIMEOUT_S:
                raise TranscriptionTimeoutError(
                    f"Transcription {transcription_id} timed out after "
                    f"{elapsed:.0f}s (limit: {_POLL_TIMEOUT_S}s)"
                )

            resp = await client.get(f"/transcriptions/{transcription_id}")
            if resp.status_code != 200:
                raise SonioxAPIError(resp.status_code, resp.text)

            status = TranscriptionStatus.from_dict(resp.json())

            if on_status:
                elapsed_min = int(elapsed) // 60
                elapsed_sec = int(elapsed) % 60
                if status.status == "queued":
                    on_status("Transcription queued...")
                elif status.status == "processing":
                    on_status(
                        f"Transcribing... (elapsed: {elapsed_min}m {elapsed_sec:02d}s)"
                    )
                elif status.status == "completed":
                    on_status("Transcription complete.")
                elif status.status == "error":
                    on_status(f"Transcription error: {status.error_message}")

            if status.status == "completed":
                return status

            if status.status == "error":
                raise TranscriptionError(
                    f"Transcription failed: {status.error_message}"
                )

            await asyncio.sleep(interval)
            interval = min(interval * _POLL_BACKOFF_FACTOR, _POLL_MAX_INTERVAL_S)

    # ------------------------------------------------------------------
    # Step 4: Fetch transcript
    # ------------------------------------------------------------------

    async def fetch_transcript(
        self,
        transcription_id: str,
        on_status: Callable[[str], None] | None = None,
    ) -> list[SonioxToken]:
        """Fetch the completed transcript and return parsed tokens.

        WHY: After polling confirms completion, we fetch the flat token
        array from GET /v1/transcriptions/{id}/transcript. This is the
        raw material for the assembler.

        HOW: GETs the transcript endpoint, parses the JSON into a
        TranscriptResponse, and returns the list of SonioxToken objects.

        RULES:
        - Only call after poll_until_complete returns "completed"
        - Returns list of SonioxToken (flat, unassembled)
        - Translation tokens are NOT filtered here (assembler's job)
        - Raises SonioxAPIError on non-2xx responses

        Args:
            transcription_id: The transcription ID.
            on_status: Optional callback for status updates.

        Returns:
            List of SonioxToken objects from the transcript response.
        """
        client = self._ensure_client()
        if on_status:
            on_status("Fetching transcript...")

        resp = await client.get(f"/transcriptions/{transcription_id}/transcript")

        if resp.status_code != 200:
            raise SonioxAPIError(resp.status_code, resp.text)

        transcript = TranscriptResponse.from_dict(resp.json())
        return transcript.tokens

    # ------------------------------------------------------------------
    # Step 5: Cleanup
    # ------------------------------------------------------------------

    async def cleanup(
        self,
        transcription_id: str,
        file_id: str,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        """Delete the transcription and uploaded file from Soniox.

        WHY: Soniox has storage limits (10GB files, 2000 transcriptions).
        Always clean up after processing to avoid hitting these limits.

        HOW: Sends DELETE requests for both the transcription and the
        file. Errors are logged but not raised — cleanup is best-effort
        since the main work is already done.

        RULES:
        - Delete transcription first, then file
        - Non-2xx responses are silently ignored (best-effort cleanup)
        - Both IDs are required

        Args:
            transcription_id: The transcription ID to delete.
            file_id: The file ID to delete.
            on_status: Optional callback for status updates.
        """
        client = self._ensure_client()
        if on_status:
            on_status("Cleaning up...")

        # Best-effort: ignore errors during cleanup
        try:
            await client.delete(f"/transcriptions/{transcription_id}")
        except httpx.HTTPError:
            pass

        try:
            await client.delete(f"/files/{file_id}")
        except httpx.HTTPError:
            pass


# ---------------------------------------------------------------------------
# Context helpers (module-private)
# ---------------------------------------------------------------------------


def _build_context(
    script_text: str | None,
    terms: list[str] | None,
    general_context: list[dict] | None,
) -> dict | None:
    """Assemble the Soniox context object from optional inputs.

    WHY: The Soniox `context` parameter on POST /v1/transcriptions has 4
    optional sections (general, text, terms, translation_terms). We only
    include sections that have actual content.

    HOW: Builds a dict with only the non-empty sections. Returns None if
    all inputs are empty/None.

    RULES:
    - script_text → context.text (string)
    - terms → context.terms (list of strings)
    - general_context → context.general (list of {key, value} dicts)
    - translation_terms is not used in Phase 1
    - Returns None if no context sections have content
    """
    context: dict = {}

    if script_text:
        context["text"] = script_text

    if terms:
        context["terms"] = terms

    if general_context:
        context["general"] = general_context

    return context or None


def _validate_context_size(context: dict) -> None:
    """Validate that the assembled context does not exceed the size limit.

    WHY: Soniox limits total context to ~8,000 tokens (~10,000 characters).
    Validating before sending gives a clear error instead of a cryptic
    API rejection.

    HOW: Estimates total character count across all context sections.
    For the text field, uses len(). For terms/general, serializes to
    estimate the character footprint.

    RULES:
    - Total context must be ≤ 10,000 characters
    - Raises ContextTooLargeError with actual size and limit if exceeded
    """
    total_chars = 0

    if "text" in context:
        total_chars += len(context["text"])

    if "terms" in context:
        # Each term contributes its length plus overhead
        total_chars += sum(len(t) for t in context["terms"])

    if "general" in context:
        for item in context["general"]:
            total_chars += len(str(item.get("key", "")))
            total_chars += len(str(item.get("value", "")))

    if total_chars > _CONTEXT_MAX_CHARS:
        raise ContextTooLargeError(
            f"Context size ({total_chars:,} characters) exceeds the Soniox "
            f"limit of {_CONTEXT_MAX_CHARS:,} characters (~8,000 tokens). "
            f"Reduce the script text or number of terms."
        )
