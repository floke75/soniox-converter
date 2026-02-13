"""Plain text transcript formatter with speaker-labeled paragraphs.

WHY: Editors need a simple, readable transcript for review, archival,
and quick reference — no JSON schemas, no timecodes, just text grouped
by speaker. This is the simplest output format and serves as the
baseline proof that the pluggable formatter pattern works.

HOW: Iterates through all IR segments in order. For each contiguous
run of segments from the same speaker, the words are joined into a
single paragraph under a "Speaker N:" header. Punctuation tokens are
merged onto the preceding word so output reads naturally ("today?"
not "today ?"). A blank line separates each speaker paragraph.

RULES:
- One paragraph per speaker turn (contiguous same-speaker segments)
- Header format: "Speaker N:" on its own line, text on the next line
- Punctuation merged onto preceding word (no space before ".", "?", etc.)
- Double newline between paragraphs
- No trailing whitespace on any line
- Output suffix: "-transcript.txt"
- Media type: "text/plain"
"""

from __future__ import annotations

from typing import Dict, List, Optional

from soniox_converter.core.ir import AssembledWord, SpeakerInfo, Transcript
from soniox_converter.formatters.base import BaseFormatter, FormatterOutput

# Punctuation characters that merge onto the preceding word with no space.
_MERGE_PUNCTUATION = frozenset({".", ",", "?", "!", ";", ":", "\u2026", "\u2014", "\u2013", "-"})


def _build_speaker_name_map(speakers: List[SpeakerInfo]) -> Dict[Optional[str], str]:
    """Map Soniox speaker labels to display names.

    Falls back to "Speaker 1" when no speakers are defined (diarization off).
    """
    name_map: Dict[Optional[str], str] = {}
    for info in speakers:
        name_map[info.soniox_label] = info.display_name
    if not name_map:
        name_map[None] = "Speaker 1"
    return name_map


def _merge_words_to_text(words: List[AssembledWord]) -> str:
    """Join assembled words into natural text with punctuation merged.

    WHY: The IR keeps punctuation as separate tokens ("today" + "?").
    Plain text output needs them merged ("today?") for readability.

    HOW: Walk words left-to-right. If the current token is punctuation
    whose text is in _MERGE_PUNCTUATION, append it directly to the
    accumulator without a space. Otherwise prepend a space (except for
    the very first word). Decimal number continuations (e.g. "2," + "5")
    are joined without a space.
    """
    if not words:
        return ""

    parts: List[str] = []
    for word in words:
        if word.word_type == "punctuation" and word.text in _MERGE_PUNCTUATION:
            # Merge onto preceding word — no space
            parts.append(word.text)
        elif parts:
            # Suppress space after comma/dash when next token is numeric
            # (e.g. "2," + "5" → "2,5" not "2, 5")
            prev = parts[-1]
            if prev in (",", "-") and word.text.isdigit():
                parts.append(word.text)
            else:
                parts.append(" ")
                parts.append(word.text)
        else:
            parts.append(word.text)

    return "".join(parts)


class PlainTextFormatter(BaseFormatter):
    """Formatter that produces speaker-labeled plain text paragraphs.

    WHY: Provides a human-readable transcript for review and archival.
    The simplest formatter — proves the pluggable pattern works.

    HOW: Groups words by speaker turn (contiguous same-speaker segments),
    merges punctuation, and writes "Speaker N:\\ntext\\n\\n" blocks.

    RULES:
    - One paragraph per speaker turn
    - "Speaker N:" header, text on next line
    - Punctuation merged onto preceding word
    - Output suffix: "-transcript.txt"
    """

    @property
    def name(self) -> str:
        return "Plain Text"

    def format(self, transcript: Transcript) -> List[FormatterOutput]:
        """Convert the Transcript IR into a plain text transcript.

        Args:
            transcript: The complete IR with segments, speakers, and metadata.

        Returns:
            A single-element list containing the plain text output.
        """
        speaker_names = _build_speaker_name_map(transcript.speakers)
        paragraphs: List[str] = []

        # Group contiguous same-speaker segments into paragraphs
        current_speaker: Optional[str] = None
        current_words: List[AssembledWord] = []

        for segment in transcript.segments:
            if segment.speaker != current_speaker:
                # Flush previous speaker's paragraph
                if current_words:
                    display = speaker_names.get(current_speaker, "Speaker")
                    text = _merge_words_to_text(current_words)
                    paragraphs.append("{name}:\n{text}".format(
                        name=display, text=text,
                    ))
                current_speaker = segment.speaker
                current_words = list(segment.words)
            else:
                current_words.extend(segment.words)

        # Flush final paragraph
        if current_words:
            display = speaker_names.get(current_speaker, "Speaker")
            text = _merge_words_to_text(current_words)
            paragraphs.append("{name}:\n{text}".format(
                name=display, text=text,
            ))

        content = "\n\n".join(paragraphs)
        if content:
            content += "\n"

        return [
            FormatterOutput(
                suffix="-transcript.txt",
                content=content,
                media_type="text/plain",
            )
        ]
