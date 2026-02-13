"""Caption formatter library for Swedish SDH subtitles.

WHY: The Soniox transcript converter needs to produce SRT caption files with
optimized line breaks tuned for Swedish. This package provides a clean library
API that the SRT formatter module can call, replacing the original monolithic
script. The library supports concurrent formatting with different presets
(broadcast vs social) without global state.

HOW: The single public entry point is format_srt(words, preset). It resolves
the preset name to a config dict, runs the DP segmentation pipeline, and
returns the finished SRT string. All internal functions receive the config
dict as an explicit parameter.

RULES:
- format_srt() is the ONLY public API for producing SRT output.
- Preset names: "broadcast" (default), "social", "some" (alias for social).
- The words list must contain Word objects from format_captions.models.
- Never mutate the preset constants — copies are made internally.
- Python 3.9.6 compatible (no slots=True, no match/case, no X | Y unions).
"""

import copy
from typing import List, Optional

from .models import Word
from .presets import PRESETS, PRESET_BROADCAST, PRESET_SOCIAL, WEAK_END_WORDS
from .core import (
    segment_words,
    best_line_break,
    generate_srt,
    parse_input,
    try_parse_json,
)

__all__ = [
    "format_srt",
    "Word",
    "PRESETS",
    "PRESET_BROADCAST",
    "PRESET_SOCIAL",
    "WEAK_END_WORDS",
]


def format_srt(
    words: List[Word],
    preset: str = "broadcast",
    config: Optional[dict] = None,
) -> str:
    """Format timestamped words into an SRT subtitle string.

    WHY: This is the single public entry point for the caption formatting
    library. External code (the SRT formatter adapter, CLI, tests) calls
    this function instead of reaching into internal modules.

    HOW: Resolves the preset name to a config dict (or uses a custom config),
    makes a deep copy to avoid mutating constants, then runs the full pipeline:
    segment_words() -> generate_srt().

    RULES:
    - preset must be one of: "broadcast", "social", "some".
    - If config is provided, it overrides the preset entirely.
    - Returns empty string if words list is empty or segmentation fails.
    - Thread-safe: each call works on its own config copy.

    Args:
        words: List of Word objects with timing and metadata.
        preset: Preset name ("broadcast", "social", "some"). Default: "broadcast".
        config: Optional custom config dict. If provided, preset is ignored.

    Returns:
        SRT-formatted subtitle string.

    Raises:
        ValueError: If preset name is not recognized and no config is provided.
    """
    if config is not None:
        cfg = copy.deepcopy(config)
    else:
        if preset not in PRESETS:
            raise ValueError(
                "Unknown preset '{}'. Available: {}".format(
                    preset, ", ".join(PRESETS.keys())
                )
            )
        cfg = copy.deepcopy(PRESETS[preset])

    if not words:
        return ""

    segments = segment_words(words, cfg)
    if not segments:
        return ""

    return generate_srt(segments, cfg)
