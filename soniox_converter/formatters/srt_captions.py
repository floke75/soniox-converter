"""SRT caption formatters — broadcast and social presets via the caption library.

WHY: All SRT output must go through the caption formatting library (no standalone
SRT). The library provides DP-optimised segmentation with Swedish linguistic
heuristics (weak-word avoidance, line balancing, CPS control). These formatters
are the bridge between the converter's Transcript IR and the caption library.

HOW: Uses the caption_adapter to convert the Transcript IR into caption Word
objects (merging punctuation, injecting speaker em-dashes, flipping EOS to
segment_start). SRTBroadcastFormatter and SRTSocialFormatter each call format_srt()
with a specific preset, producing one SRT output file each.

RULES:
- SRTBroadcastFormatter produces {stem}-broadcast.srt (16:9, 2-line, 42 chars)
- SRTSocialFormatter produces {stem}-social.srt (9:16, 1-line, 25 chars)
- SRTCaptionFormatter (deprecated) produces BOTH files for backwards compatibility
- Registered as "srt_broadcast", "srt_social", and "srt_captions" in FORMATTERS dict
- Media type for all outputs: "application/x-subrip"
- Never modifies the Transcript IR
- Python 3.9.6 compatible — no slots=True, no match/case, no X | Y unions
"""

from typing import List

from format_captions import format_srt
from soniox_converter.adapters.caption_adapter import transcript_to_caption_words
from soniox_converter.core.ir import Transcript
from soniox_converter.formatters.base import BaseFormatter, FormatterOutput


class _SRTFormatterBase(BaseFormatter):
    """Base class for SRT formatters with preset selection.

    WHY: Both broadcast and social formatters follow the same pipeline
    (Transcript → caption Words → format_srt), only differing in the preset
    parameter and output filename suffix.

    HOW: Subclasses set self.preset in __init__, and this base class handles
    the conversion and formatting logic.

    RULES:
    - Subclasses must set self.preset to "broadcast" or "social"
    - Returns a single FormatterOutput (not a list of two)
    - Abstract: do not register this base class in FORMATTERS
    """

    def __init__(self, preset: str):
        """Initialize with a specific SRT preset.

        Args:
            preset: Either "broadcast" or "social" (matches format_srt presets)
        """
        self.preset = preset

    def format(self, transcript: Transcript) -> List[FormatterOutput]:
        """Convert the Transcript IR into a single SRT file using self.preset.

        Args:
            transcript: The complete IR with segments, speakers, and metadata.

        Returns:
            A single-element list containing the SRT output for this preset.
        """
        caption_words = transcript_to_caption_words(transcript)
        srt_content = format_srt(caption_words, preset=self.preset)

        suffix_map = {
            "broadcast": "-broadcast.srt",
            "social": "-social.srt",
        }

        return [
            FormatterOutput(
                suffix=suffix_map[self.preset],
                content=srt_content,
                media_type="application/x-subrip",
            )
        ]


class SRTBroadcastFormatter(_SRTFormatterBase):
    """Formatter that produces broadcast SRT caption files (16:9, 2-line, 42 chars).

    WHY: Editors need SRT captions optimised for broadcast delivery (16:9 aspect
    ratio, 2-line display, max 42 characters per line). This formatter provides
    only the broadcast output, allowing users to select it independently.

    HOW: Inherits from _SRTFormatterBase and sets preset="broadcast".

    RULES:
    - Returns a single FormatterOutput: {stem}-broadcast.srt
    - If the transcript has no words, outputs an empty SRT string
    - Registered as "srt_broadcast" in the FORMATTERS dict
    """

    def __init__(self):
        super().__init__(preset="broadcast")

    @property
    def name(self) -> str:
        return "SRT Broadcast (16:9)"


class SRTSocialFormatter(_SRTFormatterBase):
    """Formatter that produces social SRT caption files (9:16, 1-line, 25 chars).

    WHY: Editors need SRT captions optimised for social media delivery (9:16
    aspect ratio, 1-line display, max 25 characters per line). This formatter
    provides only the social output, allowing users to select it independently.

    HOW: Inherits from _SRTFormatterBase and sets preset="social".

    RULES:
    - Returns a single FormatterOutput: {stem}-social.srt
    - If the transcript has no words, outputs an empty SRT string
    - Registered as "srt_social" in the FORMATTERS dict
    """

    def __init__(self):
        super().__init__(preset="social")

    @property
    def name(self) -> str:
        return "SRT Social (9:16)"


class SRTCaptionFormatter(BaseFormatter):
    """DEPRECATED: Use SRTBroadcastFormatter or SRTSocialFormatter instead.

    Formatter that produces both broadcast and social SRT caption files.

    WHY: Backwards compatibility for existing workflows that expect both
    SRT files to be generated when "srt_captions" is selected. New users
    should use the individual formatters for finer control.

    HOW: Converts the Transcript IR to caption Words via the adapter,
    then runs format_srt() with each preset. Each call produces a
    complete SRT string.

    RULES:
    - Returns a 2-element list: broadcast first, social second
    - If the transcript has no words, both outputs are empty SRT strings
    - The adapter handles punctuation merging, speaker markers, and
      EOS-to-segment_start conversion
    - Registered as "srt_captions" (deprecated) in the FORMATTERS dict
    """

    @property
    def name(self) -> str:
        return "SRT Captions (deprecated)"

    def format(self, transcript: Transcript) -> List[FormatterOutput]:
        """Convert the Transcript IR into broadcast and social SRT files.

        Args:
            transcript: The complete IR with segments, speakers, and metadata.

        Returns:
            Two FormatterOutput objects: broadcast SRT and social SRT.
        """
        caption_words = transcript_to_caption_words(transcript)

        broadcast_srt = format_srt(caption_words, preset="broadcast")
        social_srt = format_srt(caption_words, preset="social")

        return [
            FormatterOutput(
                suffix="-broadcast.srt",
                content=broadcast_srt,
                media_type="application/x-subrip",
            ),
            FormatterOutput(
                suffix="-social.srt",
                content=social_srt,
                media_type="application/x-subrip",
            ),
        ]
