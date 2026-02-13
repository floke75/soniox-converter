"""Configuration constants, language mappings, and .env loading.

WHY: Centralizes all configurable values so they are easy to find,
update, and override. Language mappings, supported file formats, and
API defaults are plain data structures — not buried in logic — so both
humans and coding agents can modify them confidently.

HOW: python-dotenv loads the .env file on import. Constants are defined
as module-level dicts, sets, and strings. The load_api_key() function
provides a clear error when the key is missing.

RULES:
- LANGUAGE_MAP maps ISO 639-1 → BCP-47 locale codes (19 languages)
- Unmapped language codes fall back to "??-??" (Premiere Pro unknown)
- SONIOX_SUPPORTED_FORMATS lists accepted audio/video file extensions
- API key is loaded from .env via python-dotenv, never hardcoded
- All defaults can be overridden via environment variables
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env from the project root (where the script is run from)
load_dotenv()

# ---------------------------------------------------------------------------
# Language mapping: ISO 639-1 → BCP-47 locale
# ---------------------------------------------------------------------------

LANGUAGE_MAP: dict[str, str] = {
    "sv": "sv-se",
    "en": "en-us",
    "da": "da-dk",
    "no": "nb-no",
    "fi": "fi-fi",
    "de": "de-de",
    "fr": "fr-fr",
    "es": "es-es",
    "nl": "nl-nl",
    "it": "it-it",
    "pt": "pt-br",
    "ja": "ja-jp",
    "ko": "ko-kr",
    "zh": "cmn-hans",
    "ar": "ar-sa",
    "ru": "ru-ru",
    "pl": "pl-pl",
    "tr": "tr-tr",
    "hi": "hi-in",
}

UNKNOWN_LANGUAGE_CODE = "??-??"
"""Premiere Pro's sentinel for unknown/unsupported languages."""


def map_language(iso_code: str) -> str:
    """Map an ISO 639-1 code to a BCP-47 locale code.

    WHY: Soniox uses ISO 639-1 (e.g. "en"), but output formats like
    Premiere Pro need BCP-47 locale codes (e.g. "en-us").

    HOW: Direct lookup in LANGUAGE_MAP with fallback to "??-??".

    RULES:
    - Known codes map to their BCP-47 equivalent
    - Unknown codes return "??-??" (Premiere Pro's unknown language)
    """
    return LANGUAGE_MAP.get(iso_code, UNKNOWN_LANGUAGE_CODE)


# ---------------------------------------------------------------------------
# Supported audio/video file extensions
# ---------------------------------------------------------------------------

SONIOX_SUPPORTED_FORMATS: set[str] = {
    ".aac", ".aiff", ".amr", ".asf", ".flac",
    ".mp3", ".ogg", ".wav", ".webm", ".m4a", ".mp4",
}
"""Audio/video file extensions accepted by Soniox (lowercase, with dot)."""

# ---------------------------------------------------------------------------
# API configuration defaults
# ---------------------------------------------------------------------------

SONIOX_BASE_URL = os.getenv("SONIOX_BASE_URL", "https://api.soniox.com/v1")
SONIOX_MODEL = os.getenv("SONIOX_MODEL", "stt-async-v4")
DEFAULT_PRIMARY_LANGUAGE = os.getenv("DEFAULT_PRIMARY_LANGUAGE", "sv")
DEFAULT_SECONDARY_LANGUAGE = os.getenv("DEFAULT_SECONDARY_LANGUAGE", "en")
DEFAULT_DIARIZATION = os.getenv("DEFAULT_DIARIZATION", "true").lower() == "true"


def load_api_key() -> str:
    """Load the Soniox API key from the environment.

    WHY: The API key is required for all Soniox API calls. Loading it
    from the environment (via .env) keeps it out of source code.

    HOW: Reads SONIOX_API_KEY from os.environ (populated by python-dotenv).

    RULES:
    - Raises ValueError if the key is missing or empty
    - Never returns a default/placeholder value
    """
    key = os.getenv("SONIOX_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "Soniox API key not configured. "
            "Add SONIOX_API_KEY to the .env file in the app folder."
        )
    return key
