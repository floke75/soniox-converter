"""Kinetic Word Reveal formatter — three-file Premiere Pro JSON for social video.

WHY: Social media video (TikTok, Reels, Shorts) uses animated word-by-word
captions where words pop onto screen one at a time, grouped into "buckets"
of up to 3 words. Each bucket occupies three visual rows (top, middle,
bottom). Premiere Pro handles positioning; this formatter handles timing.

HOW: Words are merged with trailing punctuation, split into sentences via
EOS markers, then each sentence is divided into buckets of ``max_bucket_size``
words (default 3). Each word in a bucket is assigned to a row (1, 2, 3) by
position. Three separate Premiere Pro JSON files are produced — one per row —
where each file contains only the words that appear on that row. Timing is
calculated so all words in a bucket disappear together when the next bucket
starts.

RULES:
- Single speaker only — ignores diarization, treats entire transcript as one
- Punctuation is merged onto the preceding word before bucketing
- Buckets are groups of ``max_bucket_size`` words; last bucket gets remainder
- Words appear at their spoken ``start_s``; all words in a bucket share an
  end time (the next bucket's first word ``start_s``, capped by ``max_hold_s``)
- Three output files with suffixes: -kinetic-row1.json, -kinetic-row2.json,
  -kinetic-row3.json
- Each file is a valid Premiere Pro transcript JSON (schema-validated)
- ``word.type`` is always ``"word"`` (no standalone punctuation)
- ``word.eos`` is True on the last word of each sentence
- Configurable: max_bucket_size, max_hold_s, final_hold_s, min_word_display_s
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

import jsonschema

from soniox_converter.config import UNKNOWN_LANGUAGE_CODE, map_language
from soniox_converter.core.ir import AssembledWord, Transcript
from soniox_converter.formatters.base import BaseFormatter, FormatterOutput

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "PremierePro_transcript_format_spec.json"

_CACHED_SCHEMA: Optional[dict] = None


def _get_schema() -> dict:
    """Load and cache the Premiere Pro JSON schema."""
    global _CACHED_SCHEMA
    if _CACHED_SCHEMA is None:
        with open(_SCHEMA_PATH) as f:
            _CACHED_SCHEMA = json.load(f)
    return _CACHED_SCHEMA


@dataclass
class _MergedWord:
    """A word with punctuation merged onto it, ready for bucketing.

    Keeps the original timing and confidence from the IR word, with
    punctuation text appended.
    """

    text: str
    start_s: float
    duration_s: float
    confidence: float
    eos: bool


@dataclass
class _Bucket:
    """A group of up to max_bucket_size merged words that appear/disappear together."""

    words: List[_MergedWord] = field(default_factory=list)
    end_s: float = 0.0  # computed after all buckets are created


def _merge_punctuation(words: List[AssembledWord]) -> List[_MergedWord]:
    """Merge punctuation tokens onto the preceding word.

    WHY: Kinetic captions show one visual word per row slot. Punctuation
    like "!" or "." must be attached to the word before it so the viewer
    sees "world!" not "world" + "!".

    HOW: Walk through words. When a punctuation token is encountered,
    append its text to the previous merged word and extend its duration.
    If punctuation appears at the very start (no preceding word), skip it.
    """
    merged: List[_MergedWord] = []
    for w in words:
        if w.word_type == "punctuation":
            if merged:
                prev = merged[-1]
                prev.text += w.text
                # Extend duration to cover the punctuation token
                new_end = w.start_s + w.duration_s
                prev.duration_s = new_end - prev.start_s
                # Inherit eos from punctuation if set
                if w.eos:
                    prev.eos = True
        else:
            merged.append(_MergedWord(
                text=w.text,
                start_s=w.start_s,
                duration_s=w.duration_s,
                confidence=w.confidence,
                eos=w.eos,
            ))
    return merged


def _split_sentences(words: List[_MergedWord]) -> List[List[_MergedWord]]:
    """Split merged words into sentences using EOS markers.

    Each sentence ends at a word with eos=True. Any trailing words
    without EOS form a final sentence.
    """
    sentences: List[List[_MergedWord]] = []
    current: List[_MergedWord] = []
    for w in words:
        current.append(w)
        if w.eos:
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def _make_buckets(sentence: List[_MergedWord], max_bucket_size: int) -> List[_Bucket]:
    """Divide a sentence into buckets of up to max_bucket_size words.

    Words are taken left-to-right in groups. The final bucket gets
    the remainder (1 to max_bucket_size words).
    """
    buckets: List[_Bucket] = []
    for i in range(0, len(sentence), max_bucket_size):
        chunk = sentence[i:i + max_bucket_size]
        buckets.append(_Bucket(words=chunk))
    return buckets


def _compute_bucket_end_times(
    all_buckets: List[_Bucket],
    max_hold_s: float,
    final_hold_s: float,
    min_word_display_s: float,
) -> None:
    """Compute the end time for each bucket in-place.

    Timing rules from PRD 6.5.2:
    1. Normal: next bucket's first word start_s
    2. Last bucket in transcript: last word end + final_hold_s, capped at max_hold
    3. Max hold cap: if gap to next bucket > max_hold, clamp to last_word.start_s + max_hold
    """
    for i, bucket in enumerate(all_buckets):
        last_word = bucket.words[-1]
        last_word_end = last_word.start_s + last_word.duration_s

        if i + 1 < len(all_buckets):
            # There is a next bucket
            next_start = all_buckets[i + 1].words[0].start_s
            # Cap at max_hold from the last word's start
            max_end = last_word.start_s + max_hold_s
            bucket.end_s = min(next_start, max_end)
        else:
            # Final bucket in transcript
            bucket.end_s = last_word_end + final_hold_s
            # Cap at max_hold from the last word's start
            max_end = last_word.start_s + max_hold_s
            bucket.end_s = min(bucket.end_s, max_end)

        # Ensure minimum display time for the last word in the bucket
        min_end = last_word.start_s + min_word_display_s
        if bucket.end_s < min_end:
            bucket.end_s = min_end


def _build_row_outputs(
    all_buckets: List[_Bucket],
    speaker_uuid: str,
    language: str,
    max_bucket_size: int,
) -> List[List[dict]]:
    """Build per-row segment lists from bucketed words.

    Each bucket assigns words to rows by position: word 0 → row 1,
    word 1 → row 2, word 2 → row 3. Each row gets a separate list
    of Premiere Pro segments. A segment contains exactly one word.

    Returns a list of ``max_bucket_size`` row lists, each containing
    segment dicts ready for the Premiere Pro JSON structure.
    """
    rows: List[List[dict]] = [[] for _ in range(max_bucket_size)]

    for bucket in all_buckets:
        bucket_end = bucket.end_s
        for row_idx, word in enumerate(bucket.words):
            word_duration = bucket_end - word.start_s
            if word_duration < 0:
                word_duration = 0.0

            word_dict: dict[str, Any] = {
                "text": word.text,
                "start": word.start_s,
                "duration": word_duration,
                "confidence": word.confidence,
                "type": "word",
                "eos": word.eos,
                "tags": [],
            }

            segment: dict[str, Any] = {
                "start": word.start_s,
                "duration": word_duration,
                "speaker": speaker_uuid,
                "language": language,
                "words": [word_dict],
            }

            rows[row_idx].append(segment)

    return rows


class KineticWordsFormatter(BaseFormatter):
    """Formatter producing three Premiere Pro JSON files for kinetic word reveal.

    WHY: Social media video captions need words that pop onto screen one
    at a time in a 3-row stack. This formatter handles the timing math
    and produces three separate track files the editor imports into
    Premiere Pro.

    HOW: Merges punctuation, splits into sentences, buckets words into
    groups of 3, computes appear/disappear timing, then distributes
    words across three row files. Each file is a valid Premiere Pro
    transcript JSON.

    RULES:
    - Single speaker (ignores diarization)
    - Punctuation merged onto preceding word before bucketing
    - Three output files: -kinetic-row1.json, -kinetic-row2.json, -kinetic-row3.json
    - Each file validated against the Premiere Pro schema
    - Configurable via constructor: max_bucket_size, max_hold_s, final_hold_s,
      min_word_display_s
    """

    def __init__(
        self,
        max_bucket_size: int = 3,
        max_hold_s: float = 3.0,
        final_hold_s: float = 1.5,
        min_word_display_s: float = 0.15,
    ) -> None:
        self.max_bucket_size = max_bucket_size
        self.max_hold_s = max_hold_s
        self.final_hold_s = final_hold_s
        self.min_word_display_s = min_word_display_s

    @property
    def name(self) -> str:
        return "Kinetic Word Reveal"

    def format(self, transcript: Transcript) -> List[FormatterOutput]:
        """Convert the Transcript IR into three kinetic word reveal JSON files.

        Args:
            transcript: The complete IR with segments, speakers, and metadata.

        Returns:
            Three FormatterOutput objects, one per row position.

        Raises:
            jsonschema.ValidationError: If any generated JSON does not
                conform to the Premiere Pro transcript schema.
        """
        # Single speaker — create one UUID for the kinetic output
        speaker_uuid = str(uuid.uuid4())
        language = map_language(transcript.primary_language)
        if language is None:
            language = UNKNOWN_LANGUAGE_CODE

        # Flatten all words from all IR segments
        all_words: List[AssembledWord] = []
        for segment in transcript.segments:
            all_words.extend(segment.words)

        # Step 1: Merge punctuation onto preceding words
        merged = _merge_punctuation(all_words)

        if not merged:
            # Empty transcript — return three empty but valid JSONs
            return self._empty_outputs(speaker_uuid, language)

        # Step 2: Split into sentences
        sentences = _split_sentences(merged)

        # Step 3: Bucket each sentence, then flatten into one list
        all_buckets: List[_Bucket] = []
        for sentence in sentences:
            all_buckets.extend(_make_buckets(sentence, self.max_bucket_size))

        # Step 4: Compute end times for each bucket
        _compute_bucket_end_times(
            all_buckets, self.max_hold_s, self.final_hold_s, self.min_word_display_s
        )

        # Step 5: Build per-row segment lists
        row_segments = _build_row_outputs(
            all_buckets, speaker_uuid, language, self.max_bucket_size
        )

        # Step 6: Build and validate three output files
        schema = _get_schema()
        outputs: List[FormatterOutput] = []
        suffixes = [
            "-kinetic-row1.json",
            "-kinetic-row2.json",
            "-kinetic-row3.json",
        ]

        for row_idx in range(self.max_bucket_size):
            segments = row_segments[row_idx]

            if not segments:
                # Row has no words — create a minimal valid JSON
                # Use a dummy segment so the schema's minItems:1 is satisfied
                outputs.append(self._minimal_output(
                    suffixes[row_idx], speaker_uuid, language
                ))
                continue

            output_dict: dict[str, Any] = {
                "language": language,
                "segments": segments,
                "speakers": [{"id": speaker_uuid, "name": "Speaker 1"}],
            }

            jsonschema.validate(instance=output_dict, schema=schema)

            content = json.dumps(output_dict, indent=2, ensure_ascii=False)
            outputs.append(FormatterOutput(
                suffix=suffixes[row_idx],
                content=content,
                media_type="application/json",
            ))

        return outputs

    def _empty_outputs(
        self, speaker_uuid: str, language: str
    ) -> List[FormatterOutput]:
        """Produce three minimal valid JSON files for an empty transcript."""
        suffixes = [
            "-kinetic-row1.json",
            "-kinetic-row2.json",
            "-kinetic-row3.json",
        ]
        return [
            self._minimal_output(s, speaker_uuid, language)
            for s in suffixes
        ]

    def _minimal_output(
        self, suffix: str, speaker_uuid: str, language: str
    ) -> FormatterOutput:
        """Create a minimal valid Premiere Pro JSON for an empty row.

        Uses a zero-duration placeholder segment to satisfy the schema's
        minItems: 1 requirement on segments and words.
        """
        output_dict: dict[str, Any] = {
            "language": language,
            "segments": [
                {
                    "start": 0.0,
                    "duration": 0.0,
                    "speaker": speaker_uuid,
                    "language": language,
                    "words": [
                        {
                            "text": "",
                            "start": 0.0,
                            "duration": 0.0,
                            "confidence": 1.0,
                            "type": "word",
                            "eos": True,
                            "tags": [],
                        }
                    ],
                }
            ],
            "speakers": [{"id": speaker_uuid, "name": "Speaker 1"}],
        }
        content = json.dumps(output_dict, indent=2, ensure_ascii=False)
        return FormatterOutput(
            suffix=suffix,
            content=content,
            media_type="application/json",
        )
