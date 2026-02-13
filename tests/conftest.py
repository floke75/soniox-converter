"""Shared test fixtures for the soniox_converter test suite.

WHY: Multiple test modules need the same verified sample data from the
Soniox async API reference (Section 5). Centralizing fixtures here avoids
duplication and ensures all tests use the authoritative test case.

HOW: Pytest fixtures provide the raw token array, the full JSON response
dict, and a pre-assembled Transcript IR built from the verified sample.

RULES:
- Token data matches soniox_async_api_reference.md Section 5 exactly.
- The pre-assembled Transcript uses the expected output from Section 5.1.
- Speaker UUIDs are deterministic (hardcoded) for test reproducibility.
"""

from typing import Any, Dict, List

import pytest

from soniox_converter.core.ir import AssembledWord, Segment, SpeakerInfo, Transcript


# ---------------------------------------------------------------------------
# Verified sample token array from API reference Section 5
# ---------------------------------------------------------------------------

VERIFIED_TOKENS: List[Dict[str, Any]] = [
    {"text": "How",      "start_ms": 120,  "end_ms": 250,  "confidence": 0.97, "speaker": "1", "language": "en"},
    {"text": " are",     "start_ms": 260,  "end_ms": 380,  "confidence": 0.95, "speaker": "1", "language": "en"},
    {"text": " you",     "start_ms": 390,  "end_ms": 510,  "confidence": 0.96, "speaker": "1", "language": "en"},
    {"text": " do",      "start_ms": 520,  "end_ms": 600,  "confidence": 0.93, "speaker": "1", "language": "en"},
    {"text": "ing",      "start_ms": 600,  "end_ms": 720,  "confidence": 0.94, "speaker": "1", "language": "en"},
    {"text": " to",      "start_ms": 730,  "end_ms": 790,  "confidence": 0.91, "speaker": "1", "language": "en"},
    {"text": "day",      "start_ms": 790,  "end_ms": 920,  "confidence": 0.96, "speaker": "1", "language": "en"},
    {"text": "?",        "start_ms": 920,  "end_ms": 940,  "confidence": 0.99, "speaker": "1", "language": "en"},
    {"text": "I",        "start_ms": 1200, "end_ms": 1260, "confidence": 0.98, "speaker": "2", "language": "en"},
    {"text": " am",      "start_ms": 1270, "end_ms": 1380, "confidence": 0.97, "speaker": "2", "language": "en"},
    {"text": " fan",     "start_ms": 1390, "end_ms": 1520, "confidence": 0.90, "speaker": "2", "language": "en"},
    {"text": "tastic",   "start_ms": 1520, "end_ms": 1780, "confidence": 0.93, "speaker": "2", "language": "en"},
    {"text": ",",        "start_ms": 1780, "end_ms": 1800, "confidence": 0.98, "speaker": "2", "language": "en"},
    {"text": " thank",   "start_ms": 1810, "end_ms": 1950, "confidence": 0.96, "speaker": "2", "language": "en"},
    {"text": " you",     "start_ms": 1960, "end_ms": 2100, "confidence": 0.97, "speaker": "2", "language": "en"},
    {"text": ".",        "start_ms": 2100, "end_ms": 2120, "confidence": 0.99, "speaker": "2", "language": "en"},
]

# Deterministic speaker UUIDs for test reproducibility
SPEAKER_1_UUID = "aaaaaaaa-1111-4000-8000-000000000001"
SPEAKER_2_UUID = "bbbbbbbb-2222-4000-8000-000000000002"


@pytest.fixture
def verified_sample_tokens():
    """The verified Soniox token array from API reference Section 5."""
    return list(VERIFIED_TOKENS)


@pytest.fixture
def sample_soniox_response():
    """Full Soniox API response dict with verified sample tokens."""
    return {
        "id": "73d4357d-cad2-4338-a60d-ec6f2044f721",
        "text": "How are you doing today? I am fantastic, thank you.",
        "tokens": list(VERIFIED_TOKENS),
    }


@pytest.fixture
def verified_sample_transcript():
    """Pre-assembled Transcript IR matching Section 5.1 expected output.

    Two segments: Speaker 1 asks a question, Speaker 2 replies.
    Words, timing, confidence, and EOS flags match the expected table exactly.
    """
    speaker1 = SpeakerInfo(
        soniox_label="1",
        display_name="Speaker 1",
        uuid=SPEAKER_1_UUID,
    )
    speaker2 = SpeakerInfo(
        soniox_label="2",
        display_name="Speaker 2",
        uuid=SPEAKER_2_UUID,
    )

    # Speaker 1's words: How are you doing today ? (with EOS on "today")
    seg1_words = [
        AssembledWord(text="How",   start_s=0.120, duration_s=0.130, confidence=0.97, word_type="word",        eos=False, speaker="1", language="en"),
        AssembledWord(text="are",   start_s=0.260, duration_s=0.120, confidence=0.95, word_type="word",        eos=False, speaker="1", language="en"),
        AssembledWord(text="you",   start_s=0.390, duration_s=0.120, confidence=0.96, word_type="word",        eos=False, speaker="1", language="en"),
        AssembledWord(text="doing", start_s=0.520, duration_s=0.200, confidence=0.93, word_type="word",        eos=False, speaker="1", language="en"),
        AssembledWord(text="today", start_s=0.730, duration_s=0.190, confidence=0.91, word_type="word",        eos=True,  speaker="1", language="en"),
        AssembledWord(text="?",     start_s=0.920, duration_s=0.020, confidence=0.99, word_type="punctuation", eos=False, speaker="1", language="en"),
    ]

    # Speaker 2's words: I am fantastic , thank you . (with EOS on "you")
    seg2_words = [
        AssembledWord(text="I",         start_s=1.200, duration_s=0.060, confidence=0.98, word_type="word",        eos=False, speaker="2", language="en"),
        AssembledWord(text="am",        start_s=1.270, duration_s=0.110, confidence=0.97, word_type="word",        eos=False, speaker="2", language="en"),
        AssembledWord(text="fantastic", start_s=1.390, duration_s=0.390, confidence=0.90, word_type="word",        eos=False, speaker="2", language="en"),
        AssembledWord(text=",",         start_s=1.780, duration_s=0.020, confidence=0.98, word_type="punctuation", eos=False, speaker="2", language="en"),
        AssembledWord(text="thank",     start_s=1.810, duration_s=0.140, confidence=0.96, word_type="word",        eos=False, speaker="2", language="en"),
        AssembledWord(text="you",       start_s=1.960, duration_s=0.140, confidence=0.97, word_type="word",        eos=True,  speaker="2", language="en"),
        AssembledWord(text=".",         start_s=2.100, duration_s=0.020, confidence=0.99, word_type="punctuation", eos=False, speaker="2", language="en"),
    ]

    segment1 = Segment(
        speaker="1",
        language="en",
        start_s=0.120,
        duration_s=0.820,  # 0.940 - 0.120
        words=seg1_words,
    )

    segment2 = Segment(
        speaker="2",
        language="en",
        start_s=1.200,
        duration_s=0.920,  # 2.120 - 1.200
        words=seg2_words,
    )

    return Transcript(
        segments=[segment1, segment2],
        speakers=[speaker1, speaker2],
        primary_language="en",
        source_filename="test_audio.mp4",
        duration_s=2.120,
    )
