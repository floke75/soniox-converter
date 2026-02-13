"""Data models for the caption formatter.

WHY: The caption formatter needs a structured representation of timestamped
words from speech-to-text output. The Word dataclass is the universal input
type — every function in the pipeline consumes or produces Word instances.

HOW: A single Word dataclass holds the text, timing, and metadata for one
word in the transcript. Speaker markers (em-dashes) and segment boundaries
(sentence starts) are flagged so the segmentation algorithm can make
informed decisions about caption breaks.

RULES:
- Word.text is sacred — never modify, paraphrase, or reorder word text.
- is_speaker_marker words are excluded from caption text but trigger "– " prefix.
- is_segment_start marks the first real word in a source segment (sentence),
  used by the DP algorithm to prefer breaking at natural boundaries.
- Timestamps are in seconds (float), not milliseconds.
"""

from dataclasses import dataclass


@dataclass
class Word:
    """A single timestamped word from a speech-to-text transcript.

    Attributes:
        text: The word text (never modified by the formatter).
        start: Start time in seconds.
        end: End time in seconds.
        is_speaker_marker: True if text is "–", "-", or "—" (speaker change indicator).
        is_segment_start: True if this word starts a new segment (sentence boundary).
    """
    text: str
    start: float
    end: float
    is_speaker_marker: bool = False
    is_segment_start: bool = False
