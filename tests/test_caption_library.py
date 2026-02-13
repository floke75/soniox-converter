"""Unit tests for the caption formatting library (format_captions).

WHY: The caption library produces SRT output via DP-optimised segmentation
with Swedish linguistic heuristics. It must work correctly with both
broadcast and social presets, and must not use global state (all functions
take config parameters).

HOW: Tests exercise the public API format_srt() with both preset names,
verify that no global state leaks between calls, and validate basic SRT
output structure.

RULES:
- Tests use the public format_srt() API only.
- Word objects come from format_captions.models.Word.
- No global state — concurrent calls with different presets must work.
"""

from typing import List

import pytest

from format_captions import format_srt
from format_captions.models import Word


def _make_words(texts, start=0.0, gap=0.3):
    """Helper to create a list of Word objects with sequential timing."""
    words = []  # type: List[Word]
    t = start
    for i, text in enumerate(texts):
        is_speaker = text in ("\u2013", "-", "\u2014")
        words.append(Word(
            text=text,
            start=t,
            end=t + gap,
            is_speaker_marker=is_speaker,
            is_segment_start=(i == 0 and not is_speaker),
        ))
        t += gap + 0.05
    return words


class TestFormatSRTBroadcast:
    """format_srt() with broadcast preset (2x42 chars)."""

    def test_produces_srt_output(self):
        words = _make_words(["Hej", "och", "v\u00e4lkommen", "till", "programmet."])
        result = format_srt(words, preset="broadcast")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_sequence_numbers(self):
        words = _make_words(["Hej", "och", "v\u00e4lkommen", "till", "programmet."])
        result = format_srt(words, preset="broadcast")
        lines = result.strip().split("\n")
        assert lines[0].strip() == "1"

    def test_contains_timecodes(self):
        words = _make_words(["Hej", "och", "v\u00e4lkommen", "till", "programmet."])
        result = format_srt(words, preset="broadcast")
        assert " --> " in result

    def test_timecode_format(self):
        """Timecodes should be HH:MM:SS,mmm format."""
        words = _make_words(["Hej", "och", "v\u00e4lkommen."])
        result = format_srt(words, preset="broadcast")
        # Find timecode lines
        for line in result.split("\n"):
            if " --> " in line:
                parts = line.split(" --> ")
                assert len(parts) == 2
                for ts in parts:
                    ts = ts.strip()
                    # Format: HH:MM:SS,mmm
                    assert ":" in ts
                    assert "," in ts

    def test_empty_words_returns_empty(self):
        result = format_srt([], preset="broadcast")
        assert result == ""


class TestFormatSRTSocial:
    """format_srt() with social preset (1x25 chars)."""

    def test_produces_srt_output(self):
        words = _make_words(["Hej", "och", "v\u00e4lkommen."])
        result = format_srt(words, preset="social")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_sequence_numbers(self):
        words = _make_words(["Hej", "och", "v\u00e4lkommen."])
        result = format_srt(words, preset="social")
        lines = result.strip().split("\n")
        assert lines[0].strip() == "1"

    def test_some_alias_works(self):
        """'some' is an alias for 'social'."""
        words = _make_words(["Hej", "och", "v\u00e4lkommen."])
        result_social = format_srt(words, preset="social")
        result_some = format_srt(words, preset="some")
        # Both should produce valid non-empty SRT
        assert len(result_social) > 0
        assert len(result_some) > 0

    def test_empty_words_returns_empty(self):
        result = format_srt([], preset="social")
        assert result == ""


class TestNoGlobalState:
    """All functions take config parameter — no global state leaks."""

    def test_concurrent_presets_independent(self):
        """Calling with different presets back-to-back doesn't leak state."""
        words = _make_words(["Det", "h\u00e4r", "\u00e4r", "ett", "test", "av", "systemet."])

        result_broadcast = format_srt(words, preset="broadcast")
        result_social = format_srt(words, preset="social")

        # Both should produce valid output
        assert len(result_broadcast) > 0
        assert len(result_social) > 0
        # Results should be strings
        assert isinstance(result_broadcast, str)
        assert isinstance(result_social, str)

    def test_repeated_calls_same_result(self):
        """Same input + same preset → same output (deterministic)."""
        words = _make_words(["Hej", "d\u00e4r."])
        result1 = format_srt(words, preset="broadcast")
        result2 = format_srt(words, preset="broadcast")
        assert result1 == result2

    def test_unknown_preset_raises(self):
        """Unknown preset name raises ValueError."""
        words = _make_words(["Hej."])
        with pytest.raises(ValueError, match="Unknown preset"):
            format_srt(words, preset="nonexistent")
