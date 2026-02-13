"""Soniox API client package — async HTTP interface to the Soniox ASR service.

WHY: The converter needs to upload audio files, create transcription jobs,
poll for completion, fetch results, and clean up resources. This package
encapsulates all Soniox API communication behind an async client class.

HOW: Uses httpx.AsyncClient for non-blocking HTTP. The SonioxClient class
provides methods for each API workflow step. Response data is parsed into
typed dataclasses defined in models.py.

RULES:
- All HTTP calls go through SonioxClient (no direct httpx usage elsewhere)
- Authentication is via Bearer token from config
- Always clean up files and transcriptions after processing
"""

from soniox_converter.api.client import SonioxClient
from soniox_converter.api.models import SonioxToken, TranscriptionStatus

__all__ = ["SonioxClient", "SonioxToken", "TranscriptionStatus"]
