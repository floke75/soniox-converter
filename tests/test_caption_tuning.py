"""Regression tests for social media caption tuning.

WHY: The social media (9:16) preset was tuned in 2026-03 to reduce weak-word
stragglers at block boundaries. These tests ensure the tuning persists and
doesn't regress in future changes.

HOW: Uses real Swedish transcripts with known problematic patterns (heavy use
of weak words like "för", "att", "och"). Tests verify that:
1. Weak-word straggler rate stays below 20% (was 28.9% before tuning)
2. Short-word straggler count is reduced
3. Unpunctuated boundaries are minimized

RULES:
- Tests use real transcript data from fixtures/caption_tuning/real_transcripts/
- Threshold of 20% weak-word stragglers allows some unavoidable edge cases
- If these tests fail, investigate before relaxing thresholds
"""

import json
from pathlib import Path

import pytest

from format_captions import format_srt, WEAK_END_WORDS
from soniox_converter.adapters.caption_adapter import transcript_to_caption_words
from soniox_converter.core.assembler import assemble_tokens, filter_translation_tokens, build_transcript


def load_test_transcript(test_name: str):
    """Load a test transcript from the caption tuning fixtures."""
    json_path = Path(__file__).parent / "fixtures/caption_tuning/real_transcripts" / f"{test_name}.json"

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    tokens = filter_translation_tokens(data['tokens'])
    assembled_words = assemble_tokens(tokens)
    transcript = build_transcript(assembled_words, source_filename=test_name)
    return transcript


def parse_srt_blocks(srt_content: str):
    """Parse SRT content into blocks."""
    blocks = []
    if not srt_content.strip():
        return blocks

    lines = srt_content.split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue

        seq = lines[i].strip()
        i += 1
        if i >= len(lines):
            break

        ts = lines[i].strip()
        i += 1

        text_lines = []
        while i < len(lines):
            if lines[i] == "" and (i + 1 >= len(lines) or (
                lines[i + 1].strip().isdigit() and
                i + 2 < len(lines) and
                " --> " in lines[i + 2]
            )):
                i += 1
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
    """Extract the last word from text, stripping punctuation."""
    text = text.rstrip().rstrip('.,!?;:…—–-')
    words = text.split()
    if not words:
        return ""
    return words[-1].lower()


class TestSocialMediaCaptionTuning:
    """Regression tests for social media caption quality improvements."""

    def test_weak_word_stragglers_below_threshold(self):
        """Social preset keeps weak-word stragglers below 20% on challenging content."""
        # Use the heavy weak-word test file
        transcript = load_test_transcript("weak_words_heavy")
        words = transcript_to_caption_words(transcript)
        srt = format_srt(words, preset="social")

        blocks = parse_srt_blocks(srt)
        assert len(blocks) > 0, "Should produce caption blocks"

        weak_endings = [b for b in blocks if last_word_clean(b["text"]) in WEAK_END_WORDS]
        weak_ratio = len(weak_endings) / len(blocks) * 100

        # Before Phase 8 tuning: 28.9% average across all tests
        # After Phase 8 tuning: 16.0% average
        # After Phase 9 tuning: 1.4% average (achieved <10% target)
        # Updated threshold from 20% to 10%
        assert weak_ratio < 10.0, (
            f"Weak-word straggler rate {weak_ratio:.1f}% exceeds 10% threshold. "
            f"Found {len(weak_endings)} weak endings in {len(blocks)} blocks. "
            f"This suggests the caption tuning has regressed."
        )

    def test_short_word_stragglers_reduced(self):
        """Social preset minimizes short-word (≤2 chars) endings."""
        transcript = load_test_transcript("weak_words_heavy")
        words = transcript_to_caption_words(transcript)
        srt = format_srt(words, preset="social")

        blocks = parse_srt_blocks(srt)
        short_endings = [
            b for b in blocks
            if last_word_clean(b["text"]) and len(last_word_clean(b["text"])) <= 2
        ]

        # Before tuning: 8 total short-word stragglers across all tests
        # After tuning: 5 total
        # Allow max 2 per test file (baseline had 1 for this file)
        assert len(short_endings) <= 2, (
            f"Found {len(short_endings)} short-word stragglers, expected ≤2"
        )

    def test_unpunctuated_boundaries_minimized(self):
        """Social preset prefers breaks at punctuation."""
        transcript = load_test_transcript("weak_words_heavy")
        words = transcript_to_caption_words(transcript)
        srt = format_srt(words, preset="social")

        blocks = parse_srt_blocks(srt)
        unpunctuated = [
            b for b in blocks
            if not b["text"].rstrip().endswith(('.', '!', '?', ',', ';'))
        ]

        # This test file had 10 unpunctuated boundaries before and after tuning
        # The tuning focused on weak words, not punctuation
        # Allow up to 11 to detect regressions without false positives
        assert len(unpunctuated) <= 11, (
            f"Found {len(unpunctuated)} unpunctuated boundaries, expected ≤11"
        )

    def test_mixed_complexity_quality(self):
        """Social preset handles mixed sentence lengths well."""
        transcript = load_test_transcript("mixed_complexity")
        words = transcript_to_caption_words(transcript)
        srt = format_srt(words, preset="social")

        blocks = parse_srt_blocks(srt)
        weak_endings = [b for b in blocks if last_word_clean(b["text"]) in WEAK_END_WORDS]
        weak_ratio = len(weak_endings) / len(blocks) * 100

        # This file went from 33.3% to 20.0% weak-word stragglers
        assert weak_ratio < 25.0, (
            f"Mixed complexity weak-word rate {weak_ratio:.1f}% exceeds 25% threshold"
        )

    def test_long_sentences_quality(self):
        """Social preset handles long sentences without excessive stragglers."""
        transcript = load_test_transcript("long_sentences")
        words = transcript_to_caption_words(transcript)
        srt = format_srt(words, preset="social")

        blocks = parse_srt_blocks(srt)
        weak_endings = [b for b in blocks if last_word_clean(b["text"]) in WEAK_END_WORDS]
        weak_ratio = len(weak_endings) / len(blocks) * 100

        # This file went from 34.6% to 11.5% weak-word stragglers
        # Most dramatic improvement
        assert weak_ratio < 15.0, (
            f"Long sentences weak-word rate {weak_ratio:.1f}% exceeds 15% threshold"
        )

    def test_overall_quality_improvement(self):
        """Social preset maintains overall quality across diverse content."""
        test_files = [
            "weak_words_heavy",
            "mixed_complexity",
            "long_sentences",
            "medium_sentences",
            "short_sentences",
        ]

        total_blocks = 0
        total_weak = 0

        for test_name in test_files:
            transcript = load_test_transcript(test_name)
            words = transcript_to_caption_words(transcript)
            srt = format_srt(words, preset="social")

            blocks = parse_srt_blocks(srt)
            weak_endings = [b for b in blocks if last_word_clean(b["text"]) in WEAK_END_WORDS]

            total_blocks += len(blocks)
            total_weak += len(weak_endings)

        overall_ratio = (total_weak / total_blocks * 100) if total_blocks > 0 else 0.0

        # Before tuning: 28.9% average
        # After tuning: 1.4% average
        # Allow 18% threshold for overall regression detection
        assert overall_ratio < 18.0, (
            f"Overall weak-word straggler rate {overall_ratio:.1f}% exceeds 18% threshold. "
            f"This indicates the caption tuning improvements have regressed."
        )

    def test_allows_longer_captions_to_avoid_stragglers(self):
        """Verify algorithm goes over 25 chars if it avoids weak-word stragglers."""
        # Use the weak_words_heavy transcript which has problematic patterns
        transcript = load_test_transcript("weak_words_heavy")
        words = transcript_to_caption_words(transcript)
        srt = format_srt(words, preset="social")

        blocks = parse_srt_blocks(srt)

        # Find blocks that are 26-30 chars (using the flexibility)
        flexible_blocks = [b for b in blocks if 26 <= len(b["text"]) <= 30]

        # All blocks should stay within hard maximum of 30 chars
        for block in blocks:
            assert len(block["text"]) <= 30, \
                f"Block '{block['text']}' ({len(block['text'])} chars) exceeds hard max of 30"

        # If there are blocks using the 26-30 char flexibility,
        # verify they don't end with weak words
        for block in flexible_blocks:
            last_word = last_word_clean(block["text"])
            assert last_word not in WEAK_END_WORDS, \
                f"Block '{block['text']}' ({len(block['text'])} chars) ends with weak word '{last_word}'"
