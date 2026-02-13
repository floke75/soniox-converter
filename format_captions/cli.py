"""CLI wrapper for the caption formatter library.

WHY: The original format_captions.py was a CLI script. This module preserves
the exact same command-line interface so existing workflows and scripts
continue to work unchanged. It also supports `python -m format_captions`.

HOW: Parses sys.argv for input path, output path, and --format flag, then
delegates to the library's format_srt() function. Input parsing (JSON with
fallback for incomplete files) is handled by core.try_parse_json() and
core.parse_input().

RULES:
- CLI interface is identical to the original script:
    python -m format_captions input.json output.srt [--format social]
    python -m format_captions input.json  (outputs to stdout)
    cat input.json | python -m format_captions - output.srt
- Exit codes: 0 = success, 1 = error.
- Progress messages go to stderr; SRT content goes to stdout (if no output file).
"""

import copy
import sys
from typing import List

from .core import parse_input, try_parse_json, segment_words, generate_srt
from .presets import PRESETS

HELP_TEXT = """format_captions — Swedish SDH caption formatter

Usage:
    python -m format_captions input.json output.srt
    python -m format_captions input.json output.srt --format social
    python -m format_captions input.json  # outputs to stdout
    cat input.json | python -m format_captions - output.srt

Format presets:
    --format broadcast  (default) 16:9 TV, 2 lines, max 42 chars/line
    --format social     9:16 vertical/SoMe, 1 line, max 25 chars/line
    --format some       Alias for social
"""


def main(argv: "List[str]" = None) -> None:
    """Run the caption formatter CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).
    """
    if argv is None:
        argv = sys.argv[1:]

    args = list(argv)

    if not args or args[0] in ("-h", "--help"):
        print(HELP_TEXT)
        print("Available format presets:")
        print("  broadcast  16:9 TV subtitles (2 lines, 42 chars max)")
        print("  social     9:16 vertical video (1 line, 25 chars max)")
        print("  some       Alias for social")
        sys.exit(0)

    # Extract --format argument
    format_name = "broadcast"
    filtered_args = []  # type: List[str]
    i = 0
    while i < len(args):
        if args[i] == "--format" and i + 1 < len(args):
            format_name = args[i + 1].lower()
            i += 2
        elif args[i].startswith("--format="):
            format_name = args[i].split("=", 1)[1].lower()
            i += 1
        else:
            filtered_args.append(args[i])
            i += 1

    # Validate format
    if format_name not in PRESETS:
        print(
            "Error: Unknown format '{}'. Available: {}".format(
                format_name, ", ".join(PRESETS.keys())
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    # Get input/output paths
    input_path = filtered_args[0] if filtered_args else "-"
    output_path = filtered_args[1] if len(filtered_args) > 1 else None

    # Read input
    if input_path == "-":
        raw = sys.stdin.read()
    else:
        with open(input_path, "r", encoding="utf-8") as f:
            raw = f.read()

    # Parse JSON
    try:
        data = try_parse_json(raw)
    except ValueError as e:
        print("Error: {}".format(e), file=sys.stderr)
        sys.exit(1)

    # Parse words
    words = parse_input(data)
    if not words:
        print("Error: No words found in input", file=sys.stderr)
        sys.exit(1)

    # Segment and generate SRT
    cfg = copy.deepcopy(PRESETS[format_name])
    segments = segment_words(words, cfg)
    if not segments:
        print("Error: Segmentation produced no output", file=sys.stderr)
        sys.exit(1)

    srt = generate_srt(segments, cfg)

    # Output
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(srt)
        format_label = "SoMe" if format_name in ("social", "some") else "broadcast"
        print(
            "Wrote {} captions ({} format) to {}".format(
                len(segments), format_label, output_path
            ),
            file=sys.stderr,
        )
    else:
        print(srt)


if __name__ == "__main__":
    main()
