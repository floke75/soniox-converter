#!/usr/bin/env python3
"""
format_captions.py
Swedish SDH caption formatter for EFN video content.

This script delegates to the format_captions package. It is kept for
backwards compatibility so existing invocations continue to work:

    python3 format_captions.py input.json output.srt
    python3 format_captions.py input.json output.srt --format social
    python3 format_captions.py input.json  # outputs to stdout
    cat input.json | python3 format_captions.py - output.srt

Format presets:
    --format broadcast  (default) 16:9 TV, 2 lines, max 42 chars/line
    --format social     9:16 vertical/SoMe, 1 line, max 25 chars/line
    --format some       Alias for social

For library usage, import from the package directly:
    from format_captions import format_srt, Word
"""

# Re-export public API for convenience
from format_captions import format_srt, Word  # noqa: F401
from format_captions.presets import (  # noqa: F401
    PRESET_BROADCAST,
    PRESET_SOCIAL,
    PRESETS,
    WEAK_END_WORDS,
)
from format_captions.core import (  # noqa: F401
    parse_input,
    try_parse_json,
    segment_words,
    best_line_break,
    generate_srt,
)
from format_captions.cli import main


if __name__ == "__main__":
    main()
