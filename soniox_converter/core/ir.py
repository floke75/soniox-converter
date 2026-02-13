"""Intermediate representation dataclasses for assembled transcripts.

WHY: Soniox returns a flat sub-word token array with no structure.
Downstream formatters (Premiere Pro JSON, SRT, plain text, etc.) each
need words, segments, speakers, and timing — but in different groupings.
The IR provides a single, well-typed intermediate form that all
formatters consume, decoupling assembly from formatting.

HOW: Four dataclasses form a hierarchy:
  AssembledWord — one word or punctuation mark with timing and metadata
  Segment       — contiguous words from a single speaker
  SpeakerInfo   — metadata for a unique speaker (label, name, UUID)
  Transcript    — the complete assembled transcript with all metadata

RULES:
- AssembledWord is the atomic unit — every formatter works with these
- Segments group words by speaker; formatters may re-segment as needed
- SpeakerInfo maps Soniox labels ("1") to display names and UUIDs
- All times are in float seconds (converted from Soniox milliseconds)
- Confidence is aggregated (minimum) across sub-word tokens
- The tags field is always empty for Soniox input; reserved for future use
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AssembledWord:
    """A single word or punctuation mark assembled from one or more Soniox sub-word tokens.

    WHY: Soniox BPE tokenization splits words like "fantastic" into
    [" fan", "tastic"]. Formatters need whole words with unified timing,
    confidence, and speaker attribution.

    HOW: The assembler combines sub-word tokens using the leading-space
    word boundary rule, then creates one AssembledWord per logical word
    or punctuation mark.

    RULES:
    - text: stripped of leading spaces, sub-words concatenated
    - start_s / duration_s: float seconds from first/last sub-word
    - confidence: minimum across all constituent sub-word tokens
    - word_type: "word" or "punctuation"
    - eos: True if this word ends a sentence (inferred from following punctuation)
    - speaker: Soniox label ("1", "2", ...) or None when diarization is off
    - language: ISO 639-1 code or None when language ID is off
    - tags: always [] for Soniox input; reserved for future use
    """

    text: str
    start_s: float
    duration_s: float
    confidence: float
    word_type: str  # "word" or "punctuation"
    eos: bool = False
    speaker: str | None = None
    language: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class Segment:
    """A contiguous group of words from a single speaker.

    WHY: Output formats need words grouped by speaker. Premiere Pro uses
    one segment per sentence, plain text uses one paragraph per speaker
    turn. The IR provides speaker-grouped segments as a starting point
    that formatters can further subdivide.

    HOW: The segmenter iterates assembled words and creates a new segment
    whenever the speaker label changes.

    RULES:
    - All words in a segment share the same speaker label
    - start_s is the start of the first word
    - duration_s spans from first word's start to last word's end
    - language is the dominant language (most frequent among words)
    """

    speaker: str | None
    language: str
    start_s: float
    duration_s: float
    words: list[AssembledWord] = field(default_factory=list)


@dataclass
class SpeakerInfo:
    """Metadata for a unique speaker in the transcript.

    WHY: Soniox labels speakers as simple strings ("1", "2"). Output
    formats like Premiere Pro need UUIDs and human-readable names.
    SpeakerInfo bridges between Soniox labels and format-specific
    speaker representations.

    HOW: Created during segmentation by mapping each unique Soniox
    speaker label to a generated UUID v4 and display name.

    RULES:
    - soniox_label: original Soniox string ("1", "2", ...)
    - display_name: "Speaker 1", "Speaker 2", etc.
    - uuid: RFC 4122 UUID v4, generated once per speaker per transcript
    """

    soniox_label: str
    display_name: str
    uuid: str


@dataclass
class Transcript:
    """The complete intermediate representation of an assembled transcript.

    WHY: This is the top-level container that formatters receive. It holds
    everything needed to produce any output format: segments with words,
    speaker metadata, language info, and source file identity.

    HOW: Built by the full pipeline: API response → token assembly →
    segmentation → Transcript construction.

    RULES:
    - segments: ordered by time, each containing speaker-grouped words
    - speakers: one SpeakerInfo per unique speaker in the transcript
    - primary_language: ISO 639-1 code of the dominant language
    - source_filename: original audio/video filename (for output naming)
    - duration_s: total audio duration (end of last word)
    """

    segments: list[Segment]
    speakers: list[SpeakerInfo]
    primary_language: str
    source_filename: str
    duration_s: float
