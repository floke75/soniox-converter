"""Premiere Pro Audio Transcript JSON formatter.

WHY: Adobe Premiere Pro can import transcript JSON files for speech-to-text
workflows. This formatter converts the Soniox IR into that exact schema
(version 1.0.0), validated against PremierePro_transcript_format_spec.json.

HOW: Segments are split at sentence boundaries (EOS markers) — a 10-sentence
monologue from one speaker produces 10 segments. Each segment carries the
speaker's UUID, BCP-47 language code, timing, and a word array. The output
is validated with jsonschema before returning.

RULES:
- One segment per sentence (split where a word has eos=True)
- Speaker references use UUID v4 from the SpeakerInfo in the Transcript IR
- Language codes mapped from ISO 639-1 → BCP-47 via LANGUAGE_MAP
- Schema version is "1.0.0" (implied by the $id in the JSON schema)
- All word objects need: text, start, duration, confidence, type, eos, tags
- Punctuation tokens are included as standalone words with type="punctuation"
- Output suffix: "-transcript.json"
- Validate output against the schema before returning; raise on failure
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import jsonschema

from soniox_converter.config import UNKNOWN_LANGUAGE_CODE, map_language
from soniox_converter.core.ir import AssembledWord, SpeakerInfo, Transcript
from soniox_converter.formatters.base import BaseFormatter, FormatterOutput

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "PremierePro_transcript_format_spec.json"


def _load_schema() -> dict[str, Any]:
    """Load the Premiere Pro JSON schema from disk.

    Cached at module level after first call to avoid repeated I/O.
    """
    with open(_SCHEMA_PATH) as f:
        return json.load(f)


_CACHED_SCHEMA: dict[str, Any] | None = None


def _get_schema() -> dict[str, Any]:
    global _CACHED_SCHEMA
    if _CACHED_SCHEMA is None:
        _CACHED_SCHEMA = _load_schema()
    return _CACHED_SCHEMA


def _map_language(iso_code: str | None) -> str:
    """Map an ISO 639-1 language code to BCP-47 for Premiere Pro.

    Falls back to "??-??" for unknown or None codes.
    Delegates to config.map_language for the actual lookup.
    """
    if iso_code is None:
        return UNKNOWN_LANGUAGE_CODE
    return map_language(iso_code)


def _build_speaker_map(speakers: list[SpeakerInfo]) -> dict[str | None, str]:
    """Build a mapping from Soniox speaker labels to UUID strings.

    If no speakers are provided, creates a default speaker mapping
    so that segments with speaker=None still get a valid UUID.
    """
    speaker_map: dict[str | None, str] = {}
    for info in speakers:
        speaker_map[info.soniox_label] = info.uuid
    if not speaker_map:
        speaker_map[None] = str(uuid.uuid4())
    return speaker_map


def _build_speakers_array(speakers: list[SpeakerInfo]) -> list[dict[str, str]]:
    """Build the Premiere Pro speakers array from IR SpeakerInfo list."""
    if not speakers:
        fallback_uuid = str(uuid.uuid4())
        return [{"id": fallback_uuid, "name": "Speaker 1"}]
    return [
        {"id": info.uuid, "name": info.display_name}
        for info in speakers
    ]


def _word_to_dict(word: AssembledWord) -> dict[str, Any]:
    """Convert an AssembledWord to a Premiere Pro word dict."""
    return {
        "text": word.text,
        "start": word.start_s,
        "duration": word.duration_s,
        "confidence": word.confidence,
        "type": word.word_type,
        "eos": word.eos,
        "tags": list(word.tags),
    }


def _segment_by_sentence(
    words: list[AssembledWord],
    speaker_map: dict[str | None, str],
    default_language: str,
) -> list[dict[str, Any]]:
    """Split words into Premiere Pro segments at sentence boundaries.

    WHY: Premiere Pro segments correspond to sentences, not speaker turns.
    A 10-sentence monologue from one speaker produces 10 segments.

    HOW: Accumulate words into a buffer. When a word has eos=True, the
    *next non-punctuation* boundary starts a new segment. The sentence-ending
    punctuation that follows the eos word belongs to the same segment.

    The strategy:
    1. Walk through words sequentially
    2. Add each word to the current sentence buffer
    3. When we encounter eos=True on a word, set a flag
    4. After eos, continue adding trailing punctuation to the same segment
    5. When we hit the next non-punctuation word, flush the buffer as a
       completed segment and start a new one
    """
    segments: list[dict[str, Any]] = []
    current_words: list[AssembledWord] = []
    sentence_ended = False

    for word in words:
        if sentence_ended and word.word_type != "punctuation":
            # Flush the completed sentence as a segment
            if current_words:
                segments.append(_build_segment(
                    current_words, speaker_map, default_language
                ))
            current_words = [word]
            sentence_ended = False
        else:
            current_words.append(word)
            if word.eos:
                sentence_ended = True

    # Flush any remaining words as the last segment
    if current_words:
        segments.append(_build_segment(
            current_words, speaker_map, default_language
        ))

    return segments


def _build_segment(
    words: list[AssembledWord],
    speaker_map: dict[str | None, str],
    default_language: str,
) -> dict[str, Any]:
    """Build a single Premiere Pro segment dict from a list of words."""
    first = words[0]
    last = words[-1]

    # Determine speaker UUID — use the first word's speaker
    speaker_label = first.speaker
    speaker_uuid = speaker_map.get(speaker_label)
    if speaker_uuid is None:
        speaker_uuid = speaker_map.get(None, str(uuid.uuid4()))

    # Determine language — use the first non-punctuation word's language,
    # falling back to the default
    segment_language = default_language
    for w in words:
        if w.word_type == "word" and w.language is not None:
            segment_language = _map_language(w.language)
            break

    start = first.start_s
    end = last.start_s + last.duration_s
    duration = end - start

    return {
        "start": start,
        "duration": duration,
        "speaker": speaker_uuid,
        "language": segment_language,
        "words": [_word_to_dict(w) for w in words],
    }


class PremiereProFormatter(BaseFormatter):
    """Formatter that produces Premiere Pro Audio Transcript JSON.

    WHY: Premiere Pro's Speech-to-Text panel can import transcript JSON
    for automated captioning and text-based editing. This formatter
    converts the Soniox IR into that exact schema.

    HOW: Segments are created by splitting at sentence boundaries (eos=True).
    Each segment carries speaker UUID, BCP-47 language, timing, and words.
    The output is validated against the JSON schema before returning.

    RULES:
    - One segment per sentence (not per speaker turn)
    - Trailing punctuation after eos stays in the same segment
    - Output suffix is "-transcript.json"
    - Schema validation is mandatory — raises on invalid output
    """

    @property
    def name(self) -> str:
        return "Premiere Pro JSON"

    def format(self, transcript: Transcript) -> list[FormatterOutput]:
        """Convert the Transcript IR into Premiere Pro Audio Transcript JSON.

        Args:
            transcript: The complete IR with segments, speakers, and metadata.

        Returns:
            A single-element list containing the JSON output.

        Raises:
            jsonschema.ValidationError: If the generated JSON does not
                conform to the Premiere Pro transcript schema.
        """
        speaker_map = _build_speaker_map(transcript.speakers)
        default_language = _map_language(transcript.primary_language)

        # Flatten all words from all IR segments into one list,
        # preserving order, for sentence-based re-segmentation
        all_words: list[AssembledWord] = []
        for segment in transcript.segments:
            all_words.extend(segment.words)

        # Build Premiere Pro segments (one per sentence)
        pp_segments = _segment_by_sentence(
            all_words, speaker_map, default_language
        )

        # Build the top-level Premiere Pro JSON structure
        output: dict[str, Any] = {
            "language": default_language,
            "segments": pp_segments,
            "speakers": _build_speakers_array(transcript.speakers),
        }

        # Validate against the JSON schema
        schema = _get_schema()
        jsonschema.validate(instance=output, schema=schema)

        content = json.dumps(output, indent=2, ensure_ascii=False)

        return [
            FormatterOutput(
                suffix="-transcript.json",
                content=content,
                media_type="application/json",
            )
        ]
