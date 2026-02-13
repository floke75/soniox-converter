"""Soniox API request and response dataclasses.

WHY: The Soniox async API returns flat JSON objects for tokens, transcription
status, and transcript responses. Typed dataclasses make these structures
explicit, enable IDE autocompletion, and catch field mismatches early.

HOW: Each dataclass maps 1:1 to a Soniox API JSON object. Factory methods
(from_dict) handle parsing from raw API responses. Fields that are only
present when specific features are enabled (speaker diarization, language
identification, translation) are typed as Optional.

RULES:
- SonioxToken fields match the Soniox async API exactly (Section 1.1 of reference)
- start_ms/end_ms are None only for translation tokens (translation_status="translation")
- speaker is None when diarization is disabled
- language is None when language identification is disabled
- translation_status is None when translation is not configured
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SonioxToken:
    """A single token from the Soniox async transcript response.

    WHY: Soniox uses BPE tokenization, producing sub-word fragments like
    [" fan", "tastic"]. Each token carries its own timing, confidence,
    and optional speaker/language metadata. The assembler combines these
    into whole words.

    HOW: Fields map directly to the Soniox token JSON object. Optional
    fields (speaker, language, translation_status) are None when the
    corresponding feature was not enabled in the transcription request.

    RULES:
    - text: raw token text including leading space if present (e.g. " are")
    - start_ms/end_ms: integer milliseconds, None only for translation tokens
    - confidence: float 0.0–1.0, always present
    - speaker: string "1"–"15" when diarization enabled, else None
    - language: ISO 639-1 code when language ID enabled, else None
    - translation_status: "original", "translation", or "none" when
      translation configured, else None
    """

    text: str
    start_ms: int | None
    end_ms: int | None
    confidence: float
    speaker: str | None = None
    language: str | None = None
    translation_status: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> SonioxToken:
        """Parse a SonioxToken from a raw API response dict.

        WHY: The API returns plain dicts; this converts to a typed object.

        HOW: Extracts known fields, using None for optional/absent fields.

        RULES:
        - text and confidence are always required
        - start_ms/end_ms default to None (absent on translation tokens)
        - speaker, language, translation_status default to None
        """
        return cls(
            text=data["text"],
            start_ms=data.get("start_ms"),
            end_ms=data.get("end_ms"),
            confidence=data["confidence"],
            speaker=data.get("speaker"),
            language=data.get("language"),
            translation_status=data.get("translation_status"),
        )


@dataclass
class TranscriptionStatus:
    """Status response from polling GET /v1/transcriptions/{id}.

    WHY: The polling loop needs to check whether a transcription job is
    still queued, processing, completed, or errored. This dataclass
    provides typed access to the status and optional error message.

    HOW: Maps the top-level fields of the transcription status response.

    RULES:
    - status is one of: "queued", "processing", "completed", "error"
    - error_message is only present when status is "error"
    - file_id is needed for cleanup after processing
    """

    id: str
    status: str
    file_id: str | None = None
    error_message: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> TranscriptionStatus:
        """Parse a TranscriptionStatus from a raw API response dict.

        WHY: Converts the polling response dict to a typed object.

        HOW: Extracts id, status, and optional error_message fields.

        RULES:
        - id and status are always required
        - error_message defaults to None
        """
        return cls(
            id=data["id"],
            status=data["status"],
            file_id=data.get("file_id"),
            error_message=data.get("error_message"),
        )


@dataclass
class TranscriptResponse:
    """Full transcript response from GET /v1/transcriptions/{id}/transcript.

    WHY: The transcript endpoint returns the flat token array along with
    the transcription ID and pre-assembled plaintext. This dataclass
    holds all three fields for downstream processing.

    HOW: Parses the response JSON into typed fields. The tokens list
    contains SonioxToken objects ready for assembly.

    RULES:
    - id is the transcription UUID
    - text is the pre-assembled plaintext (convenience field, not used for assembly)
    - tokens is the flat array that the assembler processes
    """

    id: str
    text: str
    tokens: list[SonioxToken]

    @classmethod
    def from_dict(cls, data: dict) -> TranscriptResponse:
        """Parse a TranscriptResponse from a raw API response dict.

        WHY: Converts the full transcript response into typed objects.

        HOW: Parses each token dict into a SonioxToken object.

        RULES:
        - All three fields (id, text, tokens) are required
        - Each token dict is parsed via SonioxToken.from_dict
        """
        return cls(
            id=data["id"],
            text=data["text"],
            tokens=[SonioxToken.from_dict(t) for t in data["tokens"]],
        )
