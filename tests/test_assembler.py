"""Unit tests for the token assembler module.

WHY: The assembler is the most critical transformation in the pipeline —
it converts Soniox's flat BPE token array into structured words. Incorrect
assembly produces wrong text, timing, or sentence boundaries that cascade
through every formatter.

HOW: Tests cover each assembly rule from the PRD/API reference:
  - Leading-space word boundary detection
  - Continuation token joining
  - Punctuation classification
  - First-token edge case
  - EOS inference from sentence-ending punctuation
  - Confidence aggregation (minimum strategy)
  - Translation token filtering
  - Timestamp conversion (ms → seconds)
  - Full verified sample validation against Section 5.1

RULES:
- All expected values come from soniox_async_api_reference.md Section 5.1.
- Floating-point comparisons use pytest.approx with default tolerance.
"""

import pytest

from soniox_converter.core.assembler import (
    assemble_tokens,
    filter_translation_tokens,
)


class TestLeadingSpaceWordBoundary:
    """Leading space in token text signals a new word boundary."""

    def test_leading_space_starts_new_word(self):
        tokens = [
            {"text": "Hello", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
            {"text": " world", "start_ms": 110, "end_ms": 200, "confidence": 0.95},
        ]
        words = assemble_tokens(tokens)
        assert len(words) == 2
        assert words[0].text == "Hello"
        assert words[1].text == "world"

    def test_no_leading_space_is_continuation(self):
        tokens = [
            {"text": " fan", "start_ms": 100, "end_ms": 200, "confidence": 0.9},
            {"text": "tastic", "start_ms": 200, "end_ms": 350, "confidence": 0.93},
        ]
        words = assemble_tokens(tokens)
        assert len(words) == 1
        assert words[0].text == "fantastic"


class TestContinuationTokenJoining:
    """Continuation tokens (no leading space) are appended to current word."""

    def test_two_part_word(self):
        tokens = [
            {"text": " fan", "start_ms": 960, "end_ms": 1100, "confidence": 0.90},
            {"text": "tastic", "start_ms": 1100, "end_ms": 1350, "confidence": 0.93},
        ]
        words = assemble_tokens(tokens)
        assert len(words) == 1
        assert words[0].text == "fantastic"
        assert words[0].start_s == pytest.approx(0.960)
        assert words[0].duration_s == pytest.approx(0.390)

    def test_three_part_word(self):
        tokens = [
            {"text": "Beau", "start_ms": 300, "end_ms": 420, "confidence": 0.9},
            {"text": "ti", "start_ms": 420, "end_ms": 540, "confidence": 0.85},
            {"text": "ful", "start_ms": 540, "end_ms": 780, "confidence": 0.88},
        ]
        words = assemble_tokens(tokens)
        assert len(words) == 1
        assert words[0].text == "Beautiful"
        assert words[0].start_s == pytest.approx(0.300)
        assert words[0].duration_s == pytest.approx(0.480)

    def test_doing_from_verified_sample(self):
        """'doing' = ' do' + 'ing' from verified sample."""
        tokens = [
            {"text": " do", "start_ms": 520, "end_ms": 600, "confidence": 0.93, "speaker": "1"},
            {"text": "ing", "start_ms": 600, "end_ms": 720, "confidence": 0.94, "speaker": "1"},
        ]
        words = assemble_tokens(tokens)
        assert len(words) == 1
        assert words[0].text == "doing"
        assert words[0].start_s == pytest.approx(0.520)
        assert words[0].duration_s == pytest.approx(0.200)

    def test_today_from_verified_sample(self):
        """'today' = ' to' + 'day' from verified sample."""
        tokens = [
            {"text": " to", "start_ms": 730, "end_ms": 790, "confidence": 0.91, "speaker": "1"},
            {"text": "day", "start_ms": 790, "end_ms": 920, "confidence": 0.96, "speaker": "1"},
        ]
        words = assemble_tokens(tokens)
        assert len(words) == 1
        assert words[0].text == "today"
        assert words[0].start_s == pytest.approx(0.730)
        assert words[0].duration_s == pytest.approx(0.190)


class TestPunctuationClassification:
    """Punctuation-only tokens become standalone items with type='punctuation'."""

    def test_question_mark(self):
        tokens = [
            {"text": " word", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
            {"text": "?", "start_ms": 100, "end_ms": 120, "confidence": 0.99},
        ]
        words = assemble_tokens(tokens)
        assert len(words) == 2
        assert words[1].text == "?"
        assert words[1].word_type == "punctuation"

    def test_period(self):
        tokens = [
            {"text": " word", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
            {"text": ".", "start_ms": 100, "end_ms": 120, "confidence": 0.99},
        ]
        words = assemble_tokens(tokens)
        assert words[1].word_type == "punctuation"

    def test_comma(self):
        tokens = [
            {"text": " word", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
            {"text": ",", "start_ms": 100, "end_ms": 120, "confidence": 0.98},
        ]
        words = assemble_tokens(tokens)
        assert words[1].word_type == "punctuation"

    def test_regular_word_is_not_punctuation(self):
        tokens = [
            {"text": " Hello", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
        ]
        words = assemble_tokens(tokens)
        assert words[0].word_type == "word"


class TestFirstTokenEdgeCase:
    """First token in the array starts a new word even without leading space."""

    def test_first_token_no_space(self):
        tokens = [
            {"text": "How", "start_ms": 120, "end_ms": 250, "confidence": 0.97},
        ]
        words = assemble_tokens(tokens)
        assert len(words) == 1
        assert words[0].text == "How"
        assert words[0].word_type == "word"

    def test_first_token_with_space(self):
        tokens = [
            {"text": " Hello", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
        ]
        words = assemble_tokens(tokens)
        assert len(words) == 1
        assert words[0].text == "Hello"

    def test_empty_token_list(self):
        words = assemble_tokens([])
        assert words == []


class TestEOSInference:
    """EOS is inferred from sentence-ending punctuation (., ?, !)."""

    def test_question_mark_sets_eos_on_preceding_word(self):
        tokens = [
            {"text": " today", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
            {"text": "?", "start_ms": 100, "end_ms": 120, "confidence": 0.99},
        ]
        words = assemble_tokens(tokens)
        assert words[0].eos is True
        assert words[1].eos is False  # punctuation itself is not eos

    def test_period_sets_eos_on_preceding_word(self):
        tokens = [
            {"text": " you", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
            {"text": ".", "start_ms": 100, "end_ms": 120, "confidence": 0.99},
        ]
        words = assemble_tokens(tokens)
        assert words[0].eos is True

    def test_exclamation_sets_eos(self):
        tokens = [
            {"text": " great", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
            {"text": "!", "start_ms": 100, "end_ms": 120, "confidence": 0.99},
        ]
        words = assemble_tokens(tokens)
        assert words[0].eos is True

    def test_comma_does_not_set_eos(self):
        tokens = [
            {"text": " well", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
            {"text": ",", "start_ms": 100, "end_ms": 120, "confidence": 0.98},
        ]
        words = assemble_tokens(tokens)
        assert words[0].eos is False

    def test_colon_does_not_set_eos(self):
        tokens = [
            {"text": " note", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
            {"text": ":", "start_ms": 100, "end_ms": 120, "confidence": 0.98},
        ]
        words = assemble_tokens(tokens)
        assert words[0].eos is False

    def test_no_preceding_word_for_punctuation(self):
        """Edge case: punctuation at the start — should not crash."""
        tokens = [
            {"text": "?", "start_ms": 0, "end_ms": 20, "confidence": 0.99},
        ]
        words = assemble_tokens(tokens)
        assert len(words) == 1
        assert words[0].eos is False


class TestConfidenceAggregation:
    """Confidence is aggregated using minimum across sub-word tokens."""

    def test_minimum_confidence(self):
        tokens = [
            {"text": " fan", "start_ms": 1390, "end_ms": 1520, "confidence": 0.90},
            {"text": "tastic", "start_ms": 1520, "end_ms": 1780, "confidence": 0.93},
        ]
        words = assemble_tokens(tokens)
        assert words[0].confidence == pytest.approx(0.90)

    def test_three_token_minimum(self):
        tokens = [
            {"text": "Beau", "start_ms": 300, "end_ms": 420, "confidence": 0.9},
            {"text": "ti", "start_ms": 420, "end_ms": 540, "confidence": 0.85},
            {"text": "ful", "start_ms": 540, "end_ms": 780, "confidence": 0.88},
        ]
        words = assemble_tokens(tokens)
        assert words[0].confidence == pytest.approx(0.85)

    def test_single_token_confidence(self):
        tokens = [
            {"text": " Hello", "start_ms": 0, "end_ms": 100, "confidence": 0.97},
        ]
        words = assemble_tokens(tokens)
        assert words[0].confidence == pytest.approx(0.97)


class TestTranslationTokenFiltering:
    """Translation tokens must be filtered before assembly."""

    def test_filter_translation_tokens(self):
        tokens = [
            {"text": " Hej", "start_ms": 0, "end_ms": 100, "confidence": 0.9, "translation_status": "original"},
            {"text": " Hello", "confidence": 0.95, "translation_status": "translation"},
            {"text": " du", "start_ms": 110, "end_ms": 200, "confidence": 0.92, "translation_status": "original"},
        ]
        filtered = filter_translation_tokens(tokens)
        assert len(filtered) == 2
        assert filtered[0]["text"] == " Hej"
        assert filtered[1]["text"] == " du"

    def test_filter_keeps_none_status(self):
        tokens = [
            {"text": " word", "start_ms": 0, "end_ms": 100, "confidence": 0.9, "translation_status": "none"},
        ]
        filtered = filter_translation_tokens(tokens)
        assert len(filtered) == 1

    def test_filter_keeps_absent_status(self):
        tokens = [
            {"text": " word", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
        ]
        filtered = filter_translation_tokens(tokens)
        assert len(filtered) == 1


class TestTimestampConversion:
    """Timestamps convert from ms to seconds: start_ms/1000, (end_ms - start_ms)/1000."""

    def test_start_conversion(self):
        tokens = [
            {"text": " Hello", "start_ms": 1200, "end_ms": 1260, "confidence": 0.98},
        ]
        words = assemble_tokens(tokens)
        assert words[0].start_s == pytest.approx(1.200)

    def test_duration_conversion(self):
        tokens = [
            {"text": " Hello", "start_ms": 1200, "end_ms": 1260, "confidence": 0.98},
        ]
        words = assemble_tokens(tokens)
        assert words[0].duration_s == pytest.approx(0.060)

    def test_multi_token_duration(self):
        """Duration spans from first token start to last token end."""
        tokens = [
            {"text": " fan", "start_ms": 1390, "end_ms": 1520, "confidence": 0.90},
            {"text": "tastic", "start_ms": 1520, "end_ms": 1780, "confidence": 0.93},
        ]
        words = assemble_tokens(tokens)
        assert words[0].start_s == pytest.approx(1.390)
        assert words[0].duration_s == pytest.approx(0.390)


class TestSpeakerAndLanguage:
    """Speaker and language fields are passed through from tokens."""

    def test_speaker_preserved(self):
        tokens = [
            {"text": " Hello", "start_ms": 0, "end_ms": 100, "confidence": 0.9, "speaker": "1"},
        ]
        words = assemble_tokens(tokens)
        assert words[0].speaker == "1"

    def test_language_preserved(self):
        tokens = [
            {"text": " Hello", "start_ms": 0, "end_ms": 100, "confidence": 0.9, "language": "en"},
        ]
        words = assemble_tokens(tokens)
        assert words[0].language == "en"

    def test_speaker_none_when_absent(self):
        tokens = [
            {"text": " Hello", "start_ms": 0, "end_ms": 100, "confidence": 0.9},
        ]
        words = assemble_tokens(tokens)
        assert words[0].speaker is None


class TestFullVerifiedSample:
    """Full verified sample: assemble and compare against Section 5.1 expected table."""

    def test_complete_assembly(self, verified_sample_tokens):
        words = assemble_tokens(verified_sample_tokens)

        # Expected: 13 items (6 from speaker 1, 7 from speaker 2)
        assert len(words) == 13

        # Verify each word against the expected table from Section 5.1
        expected = [
            ("How",       0.120, 0.130, 0.97, "word",        False, "1"),
            ("are",       0.260, 0.120, 0.95, "word",        False, "1"),
            ("you",       0.390, 0.120, 0.96, "word",        False, "1"),
            ("doing",     0.520, 0.200, 0.93, "word",        False, "1"),
            ("today",     0.730, 0.190, 0.91, "word",        True,  "1"),
            ("?",         0.920, 0.020, 0.99, "punctuation", False, "1"),
            ("I",         1.200, 0.060, 0.98, "word",        False, "2"),
            ("am",        1.270, 0.110, 0.97, "word",        False, "2"),
            ("fantastic", 1.390, 0.390, 0.90, "word",        False, "2"),
            (",",         1.780, 0.020, 0.98, "punctuation", False, "2"),
            ("thank",     1.810, 0.140, 0.96, "word",        False, "2"),
            ("you",       1.960, 0.140, 0.97, "word",        True,  "2"),
            (".",         2.100, 0.020, 0.99, "punctuation", False, "2"),
        ]

        for i, (text, start, dur, conf, wtype, eos, speaker) in enumerate(expected):
            w = words[i]
            assert w.text == text, "Word {}: text mismatch".format(i)
            assert w.start_s == pytest.approx(start), "Word {}: start mismatch".format(i)
            assert w.duration_s == pytest.approx(dur), "Word {}: duration mismatch".format(i)
            assert w.confidence == pytest.approx(conf), "Word {}: confidence mismatch".format(i)
            assert w.word_type == wtype, "Word {}: type mismatch".format(i)
            assert w.eos == eos, "Word {}: eos mismatch".format(i)
            assert w.speaker == speaker, "Word {}: speaker mismatch".format(i)
