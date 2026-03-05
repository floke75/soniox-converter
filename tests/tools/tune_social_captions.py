#!/usr/bin/env python3
"""Social media caption tuning tool.

WHY: The social media (9:16) preset currently produces captions with weak-word
stragglers at block boundaries (e.g., lines ending with "för", "att", "och").
This tool systematically tests different penalty configurations to find optimal
settings that eliminate stragglers while maintaining natural phrasing.

HOW: Loads real Swedish transcripts from JSON test files, runs them through
different preset configurations, analyzes the resulting SRT captions for quality
metrics (weak-word stragglers, orphans, unpunctuated boundaries), and compares
presets to identify improvements.

USAGE:
    python tests/tools/tune_social_captions.py --preset baseline
    python tests/tools/tune_social_captions.py --preset tuned-v1
    python tests/tools/tune_social_captions.py --compare baseline tuned-v1
    python tests/tools/tune_social_captions.py --all
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

# Import test presets
from tests.fixtures.caption_tuning.presets import TEST_PRESETS


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
    orphans, and other quality metrics.
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

    WHY: To check if a caption block ends with a weak word, we need to extract
    the last actual word (not punctuation).
    """
    # Remove trailing punctuation and whitespace
    text = text.rstrip().rstrip('.,!?;:…—–\-')
    # Get last word
    words = text.split()
    if not words:
        return ""
    return words[-1].lower()


def analyze_captions(srt_content: str) -> Dict:
    """Parse SRT and return quality metrics.

    Metrics tracked:
    - weak_word_stragglers: Count of blocks ending with weak words
    - short_word_stragglers: Count of blocks ending with <=2 char words
    - orphan_blocks: Count of blocks with <8 total chars
    - unpunctuated_boundaries: Count of blocks not ending with . ! ? , ;
    - avg_block_length: Average characters per block
    - block_length_std: Standard deviation of block lengths
    - total_blocks: Total number of caption blocks
    - weak_word_ratio: Percentage of blocks ending with weak words
    """
    blocks = parse_srt_blocks(srt_content)

    if not blocks:
        return {
            "weak_word_stragglers": 0,
            "short_word_stragglers": 0,
            "orphan_blocks": 0,
            "unpunctuated_boundaries": 0,
            "avg_block_length": 0.0,
            "block_length_std": 0.0,
            "total_blocks": 0,
            "weak_word_ratio": 0.0,
        }

    weak_word_count = 0
    short_word_count = 0
    orphan_count = 0
    unpunctuated_count = 0
    block_lengths = []

    for block in blocks:
        text = block["text"]
        block_len = len(text)
        block_lengths.append(block_len)

        # Check for orphan blocks (< 8 chars)
        if block_len < 8:
            orphan_count += 1

        # Check for weak-word endings
        last_word = last_word_clean(text)
        if last_word in WEAK_END_WORDS:
            weak_word_count += 1

        # Check for short-word endings (≤2 chars)
        if last_word and len(last_word) <= 2:
            short_word_count += 1

        # Check for unpunctuated boundaries
        if not text.rstrip().endswith(('.', '!', '?', ',', ';')):
            unpunctuated_count += 1

    # Calculate statistics
    avg_length = sum(block_lengths) / len(block_lengths) if block_lengths else 0.0

    # Standard deviation
    if len(block_lengths) > 1:
        mean = avg_length
        variance = sum((x - mean) ** 2 for x in block_lengths) / len(block_lengths)
        std_dev = variance ** 0.5
    else:
        std_dev = 0.0

    weak_ratio = (weak_word_count / len(blocks) * 100) if blocks else 0.0

    return {
        "weak_word_stragglers": weak_word_count,
        "short_word_stragglers": short_word_count,
        "orphan_blocks": orphan_count,
        "unpunctuated_boundaries": unpunctuated_count,
        "avg_block_length": avg_length,
        "block_length_std": std_dev,
        "total_blocks": len(blocks),
        "weak_word_ratio": weak_ratio,
    }


def run_preset_tests(preset_name: str, preset_config: Dict) -> Dict:
    """Run all test transcripts through a preset config.

    Returns a dict mapping test file stems to their metrics.
    """
    results = {}
    corpus_dir = Path(__file__).parent.parent / "fixtures/caption_tuning/real_transcripts"
    output_dir = Path(__file__).parent.parent / "fixtures/caption_tuning/output" / preset_name
    output_dir.mkdir(parents=True, exist_ok=True)

    for json_file in sorted(corpus_dir.glob("*.json")):
        print(f"  Processing {json_file.stem}...")

        # Load and convert transcript
        transcript = load_test_transcript(json_file)

        # Convert to caption words
        words = transcript_to_caption_words(transcript)

        # Format with preset
        srt = format_srt(words, config=preset_config)

        # Save output SRT
        output_file = output_dir / f"{json_file.stem}.srt"
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(srt)

        # Analyze metrics
        metrics = analyze_captions(srt)
        results[json_file.stem] = metrics

    return results


def print_metrics_table(results: Dict, preset_name: str):
    """Print formatted metrics table for a preset."""
    print(f"\n{'='*80}")
    print(f"Results for preset: {preset_name}")
    print(f"{'='*80}")
    print(f"{'Test File':<30} {'Blocks':>8} {'Weak%':>8} {'Short':>8} {'Orphan':>8} {'NoPunct':>8}")
    print(f"{'-'*80}")

    total_blocks = 0
    total_weak = 0
    total_short = 0
    total_orphan = 0
    total_unpunct = 0

    for test_name in sorted(results.keys()):
        metrics = results[test_name]
        total_blocks += metrics["total_blocks"]
        total_weak += metrics["weak_word_stragglers"]
        total_short += metrics["short_word_stragglers"]
        total_orphan += metrics["orphan_blocks"]
        total_unpunct += metrics["unpunctuated_boundaries"]

        print(f"{test_name:<30} "
              f"{metrics['total_blocks']:>8} "
              f"{metrics['weak_word_ratio']:>7.1f}% "
              f"{metrics['short_word_stragglers']:>8} "
              f"{metrics['orphan_blocks']:>8} "
              f"{metrics['unpunctuated_boundaries']:>8}")

    print(f"{'-'*80}")
    avg_weak_ratio = (total_weak / total_blocks * 100) if total_blocks > 0 else 0.0
    print(f"{'TOTAL/AVG':<30} "
          f"{total_blocks:>8} "
          f"{avg_weak_ratio:>7.1f}% "
          f"{total_short:>8} "
          f"{total_orphan:>8} "
          f"{total_unpunct:>8}")
    print(f"{'='*80}\n")


def compare_presets(base_name: str, tuned_name: str):
    """Compare two preset configurations and show improvements."""
    if base_name not in TEST_PRESETS:
        print(f"Error: Unknown preset '{base_name}'")
        print(f"Available presets: {', '.join(TEST_PRESETS.keys())}")
        return

    if tuned_name not in TEST_PRESETS:
        print(f"Error: Unknown preset '{tuned_name}'")
        print(f"Available presets: {', '.join(TEST_PRESETS.keys())}")
        return

    print(f"\nComparing {base_name} vs {tuned_name}...")
    print("="*80)

    # Run both presets
    print(f"\nRunning baseline: {base_name}")
    base_results = run_preset_tests(base_name, TEST_PRESETS[base_name])

    print(f"\nRunning tuned: {tuned_name}")
    tuned_results = run_preset_tests(tuned_name, TEST_PRESETS[tuned_name])

    # Print individual tables
    print_metrics_table(base_results, base_name)
    print_metrics_table(tuned_results, tuned_name)

    # Compute deltas
    print(f"\n{'='*80}")
    print(f"Improvement Summary: {tuned_name} vs {base_name}")
    print(f"{'='*80}")
    print(f"{'Metric':<35} {'Baseline':>15} {'Tuned':>15} {'Delta':>15}")
    print(f"{'-'*80}")

    # Aggregate metrics
    base_total_blocks = sum(m["total_blocks"] for m in base_results.values())
    base_total_weak = sum(m["weak_word_stragglers"] for m in base_results.values())
    tuned_total_blocks = sum(m["total_blocks"] for m in tuned_results.values())
    tuned_total_weak = sum(m["weak_word_stragglers"] for m in tuned_results.values())

    base_weak_ratio = (base_total_weak / base_total_blocks * 100) if base_total_blocks > 0 else 0.0
    tuned_weak_ratio = (tuned_total_weak / tuned_total_blocks * 100) if tuned_total_blocks > 0 else 0.0
    delta_weak_ratio = tuned_weak_ratio - base_weak_ratio

    print(f"{'Weak-word straggler %':<35} {base_weak_ratio:>14.1f}% {tuned_weak_ratio:>14.1f}% {delta_weak_ratio:>+14.1f}%")

    # Other metrics
    base_total_short = sum(m["short_word_stragglers"] for m in base_results.values())
    tuned_total_short = sum(m["short_word_stragglers"] for m in tuned_results.values())
    delta_short = tuned_total_short - base_total_short

    base_total_orphan = sum(m["orphan_blocks"] for m in base_results.values())
    tuned_total_orphan = sum(m["orphan_blocks"] for m in tuned_results.values())
    delta_orphan = tuned_total_orphan - base_total_orphan

    base_total_unpunct = sum(m["unpunctuated_boundaries"] for m in base_results.values())
    tuned_total_unpunct = sum(m["unpunctuated_boundaries"] for m in tuned_results.values())
    delta_unpunct = tuned_total_unpunct - base_total_unpunct

    print(f"{'Short-word stragglers':<35} {base_total_short:>15} {tuned_total_short:>15} {delta_short:>+15}")
    print(f"{'Orphan blocks (<8 chars)':<35} {base_total_orphan:>15} {tuned_total_orphan:>15} {delta_orphan:>+15}")
    print(f"{'Unpunctuated boundaries':<35} {base_total_unpunct:>15} {tuned_total_unpunct:>15} {delta_unpunct:>+15}")
    print(f"{'='*80}\n")

    # Verdict
    if delta_weak_ratio < -1.0:
        print("✅ IMPROVEMENT: Weak-word stragglers reduced by >1%")
    elif delta_weak_ratio > 1.0:
        print("❌ REGRESSION: Weak-word stragglers increased by >1%")
    else:
        print("⚠️  NEUTRAL: Minimal change in weak-word stragglers")


def main():
    parser = argparse.ArgumentParser(
        description="Social media caption tuning tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test a single preset
  python tests/tools/tune_social_captions.py --preset baseline

  # Compare two presets
  python tests/tools/tune_social_captions.py --compare baseline tuned-v1

  # Test all presets
  python tests/tools/tune_social_captions.py --all
        """
    )

    parser.add_argument('--preset', type=str, help='Run tests for a single preset')
    parser.add_argument('--compare', nargs=2, metavar=('BASE', 'TUNED'),
                       help='Compare two presets')
    parser.add_argument('--all', action='store_true',
                       help='Run all available presets')

    args = parser.parse_args()

    if args.compare:
        compare_presets(args.compare[0], args.compare[1])
    elif args.preset:
        if args.preset not in TEST_PRESETS:
            print(f"Error: Unknown preset '{args.preset}'")
            print(f"Available presets: {', '.join(TEST_PRESETS.keys())}")
            return 1

        print(f"Running preset: {args.preset}")
        results = run_preset_tests(args.preset, TEST_PRESETS[args.preset])
        print_metrics_table(results, args.preset)
    elif args.all:
        for preset_name in sorted(TEST_PRESETS.keys()):
            print(f"\nRunning preset: {preset_name}")
            results = run_preset_tests(preset_name, TEST_PRESETS[preset_name])
            print_metrics_table(results, preset_name)
    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
