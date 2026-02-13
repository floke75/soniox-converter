"""SRT caption formatter — broadcast and social presets via the caption library.

WHY: All SRT output must go through the caption formatting library (no standalone
SRT). The library provides DP-optimised segmentation with Swedish linguistic
heuristics (weak-word avoidance, line balancing, CPS control). This formatter
is the bridge between the converter's Transcript IR and the caption library.

HOW: Uses the caption_adapter to convert the Transcript IR into caption Word
objects (merging punctuation, injecting speaker em-dashes, flipping EOS to
segment_start). Then calls format_srt() twice — once for broadcast preset
(2x42 chars) and once for social preset (1x25 chars) — producing two SRT
output files.

RULES:
- Always produces TWO files: {stem}-broadcast.srt and {stem}-social.srt.
- Registered as "srt_captions" in the FORMATTERS dict.
- Media type for both outputs: "application/x-subrip".
- Never modifies the Transcript IR.
- Python 3.9.6 compatible — no slots=True, no match/case, no X | Y unions.
"""

from typing import List

from format_captions import format_srt
from soniox_converter.adapters.caption_adapter import transcript_to_caption_words
from soniox_converter.core.ir import Transcript
from soniox_converter.formatters.base import BaseFormatter, FormatterOutput


class SRTCaptionFormatter(BaseFormatter):
    """Formatter that produces broadcast and social SRT caption files.

    WHY: Editors need SRT captions optimised for two delivery targets —
    broadcast (16:9, 2-line, 42 chars) and social media (9:16, 1-line,
    25 chars). Both go through the same Swedish-tuned DP segmentation
    pipeline in the caption formatting library.

    HOW: Converts the Transcript IR to caption Words via the adapter,
    then runs format_srt() with each preset. Each call produces a
    complete SRT string.

    RULES:
    - Returns a 2-element list: broadcast first, social second.
    - If the transcript has no words, both outputs are empty SRT strings.
    - The adapter handles punctuation merging, speaker markers, and
      EOS-to-segment_start conversion.
    """

    @property
    def name(self) -> str:
        return "SRT Captions"

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
