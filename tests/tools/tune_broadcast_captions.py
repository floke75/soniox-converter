#!/usr/bin/env python3
"""Broadcast caption tuning tool.

WHY: The broadcast (16:9) preset produces 2-line captions for traditional TV
subtitles. This tool evaluates quality metrics specific to the 2-line format:
weak-word endings on both lines, line balance, single-line blocks, and
unpunctuated boundaries.

HOW: Loads real Swedish transcripts from JSON test files, runs them through
the broadcast preset, analyzes the resulting SRT captions for quality metrics,
and compares different configurations to identify improvements.

USAGE:
    python tests/tools/tune_broadcast_captions.py --preset broadcast
    python tests/tools/tune_broadcast_captions.py --all
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from format_captions import format_srt, WEAK_END_WORDS
from format_captions.models import Word
from soniox_converter.adapters.caption_adapter import transcript_to_caption_words
from soniox_converter.core.assembler import assemble_tokens, filter_translation_tokens, build_transcript


def load_test_transcript(json_path: Path):
    """Load a test JSON file and convert to Transcript IR.

    WHY: Test files are stored as Soniox JSON responses. We need to convert
    them to the Transcript IR format that caption_adapter expects.

    HOW: Load JSON, extract tokens, run through assembler pipeline.
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    tokens = data['tokens']
    # Filter out any translation tokens
    tokens = filter_translation_tokens(tokens)

    # Assemble tokens into words first
    assembled_words = assemble_tokens(tokens)

    # Build transcript IR from assembled words
    transcript = build_transcript(assembled_words, source_filename=json_path.stem)
    return transcript


def parse_srt_blocks(srt_content: str) -> List[Dict]:
    """Parse SRT content into a list of dicts with 'seq', 'timestamps', 'text'.

    WHY: We need to analyze individual caption blocks to count stragglers,
    balance, and other quality metrics for 2-line broadcast format.
    """
    blocks = []
    if not srt_content.strip():
        return blocks

    lines = srt_content.split("\n")
    i = 0
    while i < len(lines):
        # Skip blank lines
        if not lines[i].strip():
            i += 1
            continue

        # Sequence number
        seq = lines[i].strip()
        i += 1
        if i >= len(lines):
            break

        # Timestamp line
        ts = lines[i].strip()
        i += 1

        # Text lines — collect until blank line or end
        text_lines = []
        while i < len(lines):
            # Check if this is a blank line that precedes a sequence number
            if lines[i] == "" and (i + 1 >= len(lines) or (
                lines[i + 1].strip().isdigit() and
                i + 2 < len(lines) and
                " --> " in lines[i + 2]
            )):
                i += 1  # skip the blank separator
                break
            text_lines.append(lines[i])
            i += 1

        blocks.append({
            "seq": seq,
            "timestamps": ts,
            "text": "\n".join(text_lines),
        })

    return blocks


def last_word_clean(text: str) -> str:
    """Extract the last word from text, stripping punctuation.

    WHY: To check if a caption line ends with a weak word, we need to extract
    the last actual word (not punctuation).
    """
    # Remove trailing punctuation and whitespace
    text = text.rstrip().rstrip('.,!?;:…—–\-')
    # Get last word
    words = text.split()
    if not words:
        return ""
    return words[-1].lower()


def analyze_broadcast_captions(srt_content: str) -> Dict:
    """Parse SRT and return quality metrics for broadcast 2-line format.

    Metrics tracked:
    - weak_word_stragglers: Count of blocks where ANY line ends with weak word
    - line1_weak_endings: Count of line 1 weak-word endings
    - line2_weak_endings: Count of line 2 weak-word endings
    - short_word_endings: Count of blocks where ANY line ends with <=3 char word
    - single_line_blocks: Count of blocks with only 1 line
    - avg_line_balance: Average absolute difference between line 1 and line 2 lengths
    - unpunctuated_boundaries: Count of blocks not ending with . ! ? , ;
    - total_blocks: Total number of caption blocks
    - weak_word_ratio: Percentage of blocks with ANY weak-word ending
    - single_line_ratio: Percentage of blocks with only 1 line
    """
    blocks = parse_srt_blocks(srt_content)

    if not blocks:
        return {
            "weak_word_stragglers": 0,
            "line1_weak_endings": 0,
            "line2_weak_endings": 0,
            "short_word_endings": 0,
            "single_line_blocks": 0,
            "avg_line_balance": 0.0,
            "unpunctuated_boundaries": 0,
            "total_blocks": 0,
            "weak_word_ratio": 0.0,
            "single_line_ratio": 0.0,
        }

    weak_word_count = 0
    line1_weak_count = 0
    line2_weak_count = 0
    short_word_count = 0
    single_line_count = 0
    unpunctuated_count = 0
    balance_diffs = []

    for block in blocks:
        text = block["text"]
        lines = text.split("\n")

        # Track single-line blocks
        if len(lines) == 1:
            single_line_count += 1

        # Check line balance (only for 2-line blocks)
        if len(lines) == 2:
            balance_diff = abs(len(lines[0]) - len(lines[1]))
            balance_diffs.append(balance_diff)

        # Check each line for weak-word and short-word endings
        has_weak_ending = False
        has_short_ending = False

        for line_idx, line in enumerate(lines):
            last_word = last_word_clean(line)

            # Check for weak-word endings
            if last_word in WEAK_END_WORDS:
                has_weak_ending = True
                if line_idx == 0:
                    line1_weak_count += 1
                else:
                    line2_weak_count += 1

            # Check for short-word endings (≤3 chars)
            if last_word and len(last_word) <= 3:
                has_short_ending = True

        if has_weak_ending:
            weak_word_count += 1
        if has_short_ending:
            short_word_count += 1

        # Check for unpunctuated boundaries (final line)
        final_line = lines[-1]
        if not final_line.rstrip().endswith(('.', '!', '?', ',', ';')):
            unpunctuated_count += 1

    # Calculate statistics
    avg_balance = sum(balance_diffs) / len(balance_diffs) if balance_diffs else 0.0
    weak_ratio = (weak_word_count / len(blocks) * 100) if blocks else 0.0
    single_line_ratio = (single_line_count / len(blocks) * 100) if blocks else 0.0

    return {
        "weak_word_stragglers": weak_word_count,
        "line1_weak_endings": line1_weak_count,
        "line2_weak_endings": line2_weak_count,
        "short_word_endings": short_word_count,
        "single_line_blocks": single_line_count,
        "avg_line_balance": avg_balance,
        "unpunctuated_boundaries": unpunctuated_count,
        "total_blocks": len(blocks),
        "weak_word_ratio": weak_ratio,
        "single_line_ratio": single_line_ratio,
    }


def run_broadcast_tests(preset_name: str, preset_value: str = "broadcast") -> Dict:
    """Run all test transcripts through the broadcast preset.

    Args:
        preset_name: Name for output directory (e.g., "baseline", "broadcast")
        preset_value: Actual preset to use with format_srt (e.g., "broadcast")

    Returns a dict mapping test file stems to their metrics.
    """
    results = {}
    corpus_dir = Path(__file__).parent.parent / "fixtures/caption_tuning/real_transcripts"
    output_dir = Path(__file__).parent.parent / "fixtures/caption_tuning/output_broadcast" / preset_name
    output_dir.mkdir(parents=True, exist_ok=True)

    for json_file in sorted(corpus_dir.glob("*.json")):
        print(f"  Processing {json_file.stem}...")

        # Load and convert transcript
        transcript = load_test_transcript(json_file)

        # Convert to caption words
        words = transcript_to_caption_words(transcript)

        # Format with broadcast preset
        srt = format_srt(words, preset=preset_value)

        # Save output SRT
        output_file = output_dir / f"{json_file.stem}.srt"
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(srt)

        # Analyze metrics
        metrics = analyze_broadcast_captions(srt)
        results[json_file.stem] = metrics

    return results


def print_broadcast_metrics_table(results: Dict, preset_name: str):
    """Print formatted metrics table for broadcast preset."""
    print(f"\n{'='*88}")
    print(f"Results for preset: {preset_name}")
    print(f"{'='*88}")
    print(f"{'Test File':<30} {'Blocks':>7} {'Weak%':>7} {'Balance':>8} {'Single%':>8} {'Short':>7} {'NoPunct':>8}")
    print(f"{'-'*88}")

    total_blocks = 0
    total_weak = 0
    total_short = 0
    total_single = 0
    total_unpunct = 0
    balance_values = []

    for test_name in sorted(results.keys()):
        metrics = results[test_name]
        total_blocks += metrics["total_blocks"]
        total_weak += metrics["weak_word_stragglers"]
        total_short += metrics["short_word_endings"]
        total_single += metrics["single_line_blocks"]
        total_unpunct += metrics["unpunctuated_boundaries"]

        # Only track balance for tests that have 2-line blocks
        if metrics["avg_line_balance"] > 0:
            balance_values.append(metrics["avg_line_balance"])

        print(f"{test_name:<30} "
              f"{metrics['total_blocks']:>7} "
              f"{metrics['weak_word_ratio']:>6.1f}% "
              f"{metrics['avg_line_balance']:>8.1f} "
              f"{metrics['single_line_ratio']:>7.1f}% "
              f"{metrics['short_word_endings']:>7} "
              f"{metrics['unpunctuated_boundaries']:>8}")

    print(f"{'-'*88}")
    avg_weak_ratio = (total_weak / total_blocks * 100) if total_blocks > 0 else 0.0
    avg_single_ratio = (total_single / total_blocks * 100) if total_blocks > 0 else 0.0
    avg_balance = sum(balance_values) / len(balance_values) if balance_values else 0.0

    print(f"{'TOTAL/AVG':<30} "
          f"{total_blocks:>7} "
          f"{avg_weak_ratio:>6.1f}% "
          f"{avg_balance:>8.1f} "
          f"{avg_single_ratio:>7.1f}% "
          f"{total_short:>7} "
          f"{total_unpunct:>8}")
    print(f"{'='*88}\n")


def print_detailed_analysis(results: Dict, preset_name: str):
    """Print detailed breakdown of weak-word endings by line."""
    print(f"\n{'='*80}")
    print(f"Detailed Weak-Word Analysis for: {preset_name}")
    print(f"{'='*80}")
    print(f"{'Test File':<30} {'Line 1 Weak':>15} {'Line 2 Weak':>15} {'Total Weak':>15}")
    print(f"{'-'*80}")

    total_line1_weak = 0
    total_line2_weak = 0
    total_weak = 0

    for test_name in sorted(results.keys()):
        metrics = results[test_name]
        total_line1_weak += metrics["line1_weak_endings"]
        total_line2_weak += metrics["line2_weak_endings"]
        total_weak += metrics["weak_word_stragglers"]

        print(f"{test_name:<30} "
              f"{metrics['line1_weak_endings']:>15} "
              f"{metrics['line2_weak_endings']:>15} "
              f"{metrics['weak_word_stragglers']:>15}")

    print(f"{'-'*80}")
    print(f"{'TOTAL':<30} "
          f"{total_line1_weak:>15} "
          f"{total_line2_weak:>15} "
          f"{total_weak:>15}")
    print(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Broadcast caption tuning tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test the broadcast preset
  python tests/tools/tune_broadcast_captions.py --preset broadcast

  # Test all available presets
  python tests/tools/tune_broadcast_captions.py --all

  # Generate baseline outputs
  python tests/tools/tune_broadcast_captions.py --preset baseline
        """
    )

    parser.add_argument('--preset', type=str, help='Run tests for the broadcast preset')
    parser.add_argument('--all', action='store_true',
                       help='Run comprehensive analysis with detailed metrics')

    args = parser.parse_args()

    if args.preset:
        preset_name = args.preset
        print(f"Running broadcast preset: {preset_name}")
        results = run_broadcast_tests(preset_name, preset_value="broadcast")
        print_broadcast_metrics_table(results, preset_name)
        print_detailed_analysis(results, preset_name)
    elif args.all:
        # Run comprehensive analysis
        print("\nRunning comprehensive broadcast caption analysis...")
        print("="*80)

        preset_name = "broadcast"
        print(f"\nGenerating outputs for: {preset_name}")
        results = run_broadcast_tests(preset_name, preset_value="broadcast")
        print_broadcast_metrics_table(results, preset_name)
        print_detailed_analysis(results, preset_name)

        # Print holistic analysis
        print("\n" + "="*80)
        print("HOLISTIC ANALYSIS")
        print("="*80)

        total_blocks = sum(m["total_blocks"] for m in results.values())
        total_weak = sum(m["weak_word_stragglers"] for m in results.values())
        total_line1_weak = sum(m["line1_weak_endings"] for m in results.values())
        total_line2_weak = sum(m["line2_weak_endings"] for m in results.values())
        total_single = sum(m["single_line_blocks"] for m in results.values())
        total_short = sum(m["short_word_endings"] for m in results.values())
        total_unpunct = sum(m["unpunctuated_boundaries"] for m in results.values())

        balance_values = [m["avg_line_balance"] for m in results.values() if m["avg_line_balance"] > 0]
        avg_balance = sum(balance_values) / len(balance_values) if balance_values else 0.0

        weak_ratio = (total_weak / total_blocks * 100) if total_blocks > 0 else 0.0
        single_ratio = (total_single / total_blocks * 100) if total_blocks > 0 else 0.0

        print(f"\nOverall Statistics:")
        print(f"  Total blocks analyzed: {total_blocks}")
        print(f"  Weak-word straggler rate: {weak_ratio:.1f}% ({total_weak}/{total_blocks})")
        print(f"    - Line 1 weak endings: {total_line1_weak}")
        print(f"    - Line 2 weak endings: {total_line2_weak}")
        print(f"  Single-line block rate: {single_ratio:.1f}% ({total_single}/{total_blocks})")
        print(f"  Average line balance (2-line blocks): {avg_balance:.1f} chars")
        print(f"  Short-word endings (≤3 chars): {total_short}")
        print(f"  Unpunctuated boundaries: {total_unpunct}")

        print(f"\nQuality Assessment:")
        if weak_ratio < 5.0:
            print(f"  ✓ EXCELLENT: Weak-word straggler rate is below 5%")
        elif weak_ratio < 10.0:
            print(f"  ✓ GOOD: Weak-word straggler rate is below 10%")
        elif weak_ratio < 15.0:
            print(f"  ⚠ ACCEPTABLE: Weak-word straggler rate is below 15%")
        else:
            print(f"  ✗ NEEDS IMPROVEMENT: Weak-word straggler rate exceeds 15%")

        if avg_balance < 5.0:
            print(f"  ✓ EXCELLENT: Line balance is very good (avg diff < 5 chars)")
        elif avg_balance < 10.0:
            print(f"  ✓ GOOD: Line balance is acceptable (avg diff < 10 chars)")
        else:
            print(f"  ⚠ NEEDS IMPROVEMENT: Lines could be better balanced")

        if single_ratio < 5.0:
            print(f"  ✓ EXCELLENT: Very few single-line blocks (<5%)")
        elif single_ratio < 10.0:
            print(f"  ✓ GOOD: Low single-line block rate (<10%)")
        else:
            print(f"  ⚠ NOTE: Higher single-line block rate ({single_ratio:.1f}%)")

        print(f"\nRecommendations:")
        if weak_ratio > 10.0:
            print(f"  • Consider increasing weak_end and boundary_weak_end penalties")
        if avg_balance > 10.0:
            print(f"  • Consider increasing balance weight to improve line distribution")
        if single_ratio > 15.0:
            print(f"  • Review single_line_long penalty if too many 1-line blocks")
        if weak_ratio < 5.0 and avg_balance < 5.0:
            print(f"  • Broadcast preset is performing well - no major tuning needed")

        print("="*80 + "\n")
    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
