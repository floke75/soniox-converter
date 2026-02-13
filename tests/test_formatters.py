"""Unit tests for all formatter modules.

WHY: Each formatter transforms the IR into a specific output format.
Incorrect formatting could produce invalid files that crash Premiere Pro,
display garbled captions, or lose speaker attribution.

HOW: Tests validate each formatter against the verified sample Transcript IR:
  - Premiere Pro: schema validation, sentence-based segmentation, speaker UUIDs
  - Plain text: speaker-labeled paragraphs with punctuation merging
  - Kinetic: 3-file output, bucket timing, round-robin rows
  - SRT: adapter transformations, valid SRT format

RULES:
- Schema validation uses PremierePro_transcript_format_spec.json.
- All tests use the verified_sample_transcript fixture from conftest.py.
"""

import json
from pathlib import Path
from typing import Any, Dict

import jsonschema
import pytest

from soniox_converter.core.ir import (
    AssembledWord,
    Segment,
    SpeakerInfo,
    Transcript,
)
from soniox_converter.formatters.premiere_pro import PremiereProFormatter
from soniox_converter.formatters.plain_text import PlainTextFormatter
from soniox_converter.formatters.kinetic_words import KineticWordsFormatter
from soniox_converter.formatters.srt_captions import SRTCaptionFormatter
from soniox_converter.adapters.caption_adapter import transcript_to_caption_words

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "PremierePro_transcript_format_spec.json"


def _load_schema():
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def _parse_srt_blocks(srt_content):
    """Parse SRT content into a list of dicts with 'seq', 'timestamps', 'text'."""
    blocks = []
    if not srt_content.strip():
        return blocks
    # Split on double newlines (but be careful — our text may contain newlines)
    # Parse manually: sequence number, timestamp line, then text until next blank+digit
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
        # Text lines — collect until blank line followed by a digit (next block) or end
        text_lines = []
        while i < len(lines):
            # Check if this is a blank line that precedes a sequence number (next block)
            if lines[i] == "" and (i + 1 >= len(lines) or (lines[i + 1].strip().isdigit() and i + 2 < len(lines) and " --> " in lines[i + 2])):
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


# =========================================================================
# Premiere Pro Formatter Tests
# =========================================================================

class TestPremiereProFormatter:
    """Premiere Pro JSON formatter tests."""

    def test_schema_validation(self, verified_sample_transcript):
        """Output validates against PremierePro_transcript_format_spec.json."""
        formatter = PremiereProFormatter()
        outputs = formatter.format(verified_sample_transcript)
        assert len(outputs) == 1

        data = json.loads(outputs[0].content)
        schema = _load_schema()
        # Should not raise
        jsonschema.validate(instance=data, schema=schema)

    def test_sentence_based_segmentation(self, verified_sample_transcript):
        """Segments are split at sentence boundaries, not speaker boundaries.

        The verified sample has 2 sentences:
        1. 'How are you doing today?' (speaker 1)
        2. 'I am fantastic, thank you.' (speaker 2)
        So there should be 2 segments.
        """
        formatter = PremiereProFormatter()
        outputs = formatter.format(verified_sample_transcript)
        data = json.loads(outputs[0].content)

        assert len(data["segments"]) == 2

    def test_speaker_uuid_mapping(self, verified_sample_transcript):
        """Each segment references a speaker UUID from the speakers array."""
        formatter = PremiereProFormatter()
        outputs = formatter.format(verified_sample_transcript)
        data = json.loads(outputs[0].content)

        speaker_ids = {s["id"] for s in data["speakers"]}
        for segment in data["segments"]:
            assert segment["speaker"] in speaker_ids

    def test_word_fields_present(self, verified_sample_transcript):
        """Each word has the required fields: text, start, duration, confidence, type, eos, tags."""
        formatter = PremiereProFormatter()
        outputs = formatter.format(verified_sample_transcript)
        data = json.loads(outputs[0].content)

        required_fields = {"text", "start", "duration", "confidence", "type", "eos", "tags"}
        for segment in data["segments"]:
            for word in segment["words"]:
                assert set(word.keys()) == required_fields

    def test_language_field(self, verified_sample_transcript):
        """Top-level and segment language fields use BCP-47 format."""
        formatter = PremiereProFormatter()
        outputs = formatter.format(verified_sample_transcript)
        data = json.loads(outputs[0].content)

        # The verified sample is English
        assert data["language"] == "en-us"

    def test_output_suffix(self, verified_sample_transcript):
        formatter = PremiereProFormatter()
        outputs = formatter.format(verified_sample_transcript)
        assert outputs[0].suffix == "-transcript.json"

    def test_output_media_type(self, verified_sample_transcript):
        formatter = PremiereProFormatter()
        outputs = formatter.format(verified_sample_transcript)
        assert outputs[0].media_type == "application/json"

    def test_punctuation_included_as_words(self, verified_sample_transcript):
        """Punctuation tokens are included as standalone words with type='punctuation'."""
        formatter = PremiereProFormatter()
        outputs = formatter.format(verified_sample_transcript)
        data = json.loads(outputs[0].content)

        all_words = []
        for seg in data["segments"]:
            all_words.extend(seg["words"])

        punct_words = [w for w in all_words if w["type"] == "punctuation"]
        assert len(punct_words) >= 2  # at least ? and .


# =========================================================================
# Plain Text Formatter Tests
# =========================================================================

class TestPlainTextFormatter:
    """Plain text formatter tests."""

    def test_speaker_labeled_paragraphs(self, verified_sample_transcript):
        """Output has speaker labels and paragraph structure."""
        formatter = PlainTextFormatter()
        outputs = formatter.format(verified_sample_transcript)
        assert len(outputs) == 1

        text = outputs[0].content
        assert "Speaker 1:" in text
        assert "Speaker 2:" in text

    def test_punctuation_merged(self, verified_sample_transcript):
        """Punctuation merged onto preceding word (no space before '?')."""
        formatter = PlainTextFormatter()
        outputs = formatter.format(verified_sample_transcript)
        text = outputs[0].content

        assert "today?" in text
        assert "you." in text
        # Should NOT have "today ?" with a space
        assert "today ?" not in text

    def test_paragraphs_separated(self, verified_sample_transcript):
        """Paragraphs separated by double newline."""
        formatter = PlainTextFormatter()
        outputs = formatter.format(verified_sample_transcript)
        text = outputs[0].content

        assert "\n\n" in text

    def test_output_suffix(self, verified_sample_transcript):
        formatter = PlainTextFormatter()
        outputs = formatter.format(verified_sample_transcript)
        assert outputs[0].suffix == "-transcript.txt"

    def test_output_media_type(self, verified_sample_transcript):
        formatter = PlainTextFormatter()
        outputs = formatter.format(verified_sample_transcript)
        assert outputs[0].media_type == "text/plain"


# =========================================================================
# Kinetic Word Reveal Formatter Tests
# =========================================================================

class TestKineticWordsFormatter:
    """Kinetic word reveal formatter tests."""

    def test_three_file_output(self, verified_sample_transcript):
        """Produces exactly 3 output files (one per row)."""
        formatter = KineticWordsFormatter()
        outputs = formatter.format(verified_sample_transcript)
        assert len(outputs) == 3

    def test_output_suffixes(self, verified_sample_transcript):
        """Output files have correct SRT suffixes."""
        formatter = KineticWordsFormatter()
        outputs = formatter.format(verified_sample_transcript)
        suffixes = [o.suffix for o in outputs]
        assert "-kinetic-row1.srt" in suffixes
        assert "-kinetic-row2.srt" in suffixes
        assert "-kinetic-row3.srt" in suffixes

    def test_each_file_is_valid_srt(self, verified_sample_transcript):
        """Each output file is a valid SRT with sequence numbers and timestamps."""
        formatter = KineticWordsFormatter()
        outputs = formatter.format(verified_sample_transcript)
        for output in outputs:
            content = output.content
            if not content.strip():
                continue  # empty rows are valid
            # Check that the first block has a sequence number and timestamp
            lines = content.split("\n")
            assert lines[0].strip() == "1"
            assert " --> " in lines[1]

    def test_srt_timestamp_format(self, verified_sample_transcript):
        """SRT timestamps use HH:MM:SS,mmm format."""
        import re
        formatter = KineticWordsFormatter()
        outputs = formatter.format(verified_sample_transcript)
        ts_pattern = re.compile(r"\d{2}:\d{2}:\d{2},\d{3}")
        for output in outputs:
            if not output.content.strip():
                continue
            for line in output.content.split("\n"):
                if " --> " in line:
                    parts = line.split(" --> ")
                    assert ts_pattern.match(parts[0].strip())
                    assert ts_pattern.match(parts[1].strip())

    def test_round_robin_rows(self, verified_sample_transcript):
        """Words are distributed across rows — row 1 has most entries."""
        formatter = KineticWordsFormatter()
        outputs = formatter.format(verified_sample_transcript)

        def count_blocks(srt_content):
            if not srt_content.strip():
                return 0
            return len([l for l in srt_content.split("\n") if " --> " in l])

        row1_count = count_blocks(outputs[0].content)
        row2_count = count_blocks(outputs[1].content)
        row3_count = count_blocks(outputs[2].content)

        assert row1_count >= row2_count >= row3_count

    def test_row_positioning_newlines(self, verified_sample_transcript):
        """Row 2 text has 1 leading newline, row 3 has 2 leading newlines."""
        formatter = KineticWordsFormatter()
        outputs = formatter.format(verified_sample_transcript)

        # Row 1: no leading newlines in text lines
        for block in _parse_srt_blocks(outputs[0].content):
            assert not block["text"].startswith("\n")

        # Row 2: text starts with exactly 1 newline
        for block in _parse_srt_blocks(outputs[1].content):
            assert block["text"].startswith("\n")
            assert not block["text"].startswith("\n\n")

        # Row 3: text starts with exactly 2 newlines
        if outputs[2].content.strip():
            for block in _parse_srt_blocks(outputs[2].content):
                assert block["text"].startswith("\n\n")
                assert not block["text"].startswith("\n\n\n")

    def test_one_word_or_number_group_per_block(self, verified_sample_transcript):
        """Each SRT block contains one word or number group (after stripping newlines)."""
        formatter = KineticWordsFormatter()
        outputs = formatter.format(verified_sample_transcript)

        for output in outputs:
            for block in _parse_srt_blocks(output.content):
                # Strip leading newlines (row positioning)
                text = block["text"].lstrip("\n")
                # Should have at least one visible token
                assert len(text.strip()) > 0, "Empty block: {}".format(repr(text))

    def test_no_overlapping_timestamps_within_row(self, verified_sample_transcript):
        """Within each row, no subtitle's end time exceeds the next subtitle's start."""
        import re
        formatter = KineticWordsFormatter()
        outputs = formatter.format(verified_sample_transcript)

        ts_pattern = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")

        def parse_ts(ts_str):
            m = ts_pattern.match(ts_str.strip())
            h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            return h * 3600 + mi * 60 + s + ms / 1000.0

        for output in outputs:
            blocks = _parse_srt_blocks(output.content)
            for i in range(len(blocks) - 1):
                current_end = parse_ts(blocks[i]["timestamps"].split(" --> ")[1])
                next_start = parse_ts(blocks[i + 1]["timestamps"].split(" --> ")[0])
                assert current_end <= next_start + 0.001, \
                    "Overlap in row: block {} ends at {} but block {} starts at {}".format(
                        i + 1, current_end, i + 2, next_start)

    def test_number_grouping(self):
        """Multi-token numbers are grouped together in kinetic output."""
        from soniox_converter.formatters.kinetic_words import _merge_punctuation, _group_numbers
        from soniox_converter.core.ir import AssembledWord

        # Simulate "120 000 kronor" as three separate tokens
        words = [
            AssembledWord(text="120", start_s=0.0, duration_s=0.3,
                         confidence=0.9, word_type="word"),
            AssembledWord(text="000", start_s=0.3, duration_s=0.3,
                         confidence=0.9, word_type="word"),
            AssembledWord(text="kronor", start_s=0.6, duration_s=0.3,
                         confidence=0.9, word_type="word"),
        ]
        merged = _merge_punctuation(words)
        grouped = _group_numbers(merged)
        assert len(grouped) == 1
        assert grouped[0].text == "120 000 kronor"

    def test_decimal_number_grouping(self):
        """Decimal numbers like '2,5' are grouped with their unit."""
        from soniox_converter.formatters.kinetic_words import _merge_punctuation, _group_numbers
        from soniox_converter.core.ir import AssembledWord

        # Simulate "2,5 miljoner" — comma is punctuation, merges onto "2"
        words = [
            AssembledWord(text="2", start_s=0.0, duration_s=0.2,
                         confidence=0.9, word_type="word"),
            AssembledWord(text=",", start_s=0.2, duration_s=0.05,
                         confidence=0.9, word_type="punctuation"),
            AssembledWord(text="5", start_s=0.3, duration_s=0.2,
                         confidence=0.9, word_type="word"),
            AssembledWord(text="miljoner", start_s=0.5, duration_s=0.3,
                         confidence=0.9, word_type="word"),
        ]
        merged = _merge_punctuation(words)  # "2," + "5" + "miljoner"
        grouped = _group_numbers(merged)
        assert len(grouped) == 1
        assert grouped[0].text == "2,5 miljoner"


# =========================================================================
# SRT Caption Formatter Tests
# =========================================================================

class TestSRTCaptionFormatter:
    """SRT caption formatter tests."""

    def test_produces_two_files(self, verified_sample_transcript):
        """Produces broadcast and social SRT files."""
        formatter = SRTCaptionFormatter()
        outputs = formatter.format(verified_sample_transcript)
        assert len(outputs) == 2

    def test_output_suffixes(self, verified_sample_transcript):
        formatter = SRTCaptionFormatter()
        outputs = formatter.format(verified_sample_transcript)
        suffixes = [o.suffix for o in outputs]
        assert "-broadcast.srt" in suffixes
        assert "-social.srt" in suffixes

    def test_output_media_type(self, verified_sample_transcript):
        formatter = SRTCaptionFormatter()
        outputs = formatter.format(verified_sample_transcript)
        for output in outputs:
            assert output.media_type == "application/x-subrip"

    def test_valid_srt_format(self, verified_sample_transcript):
        """SRT output has valid format: sequence numbers, timecodes, text."""
        formatter = SRTCaptionFormatter()
        outputs = formatter.format(verified_sample_transcript)

        for output in outputs:
            lines = output.content.strip().split("\n")
            if not lines or not lines[0]:
                continue

            # First non-empty line should be a sequence number
            assert lines[0].strip().isdigit()

            # Check for SRT timecode format (HH:MM:SS,mmm --> HH:MM:SS,mmm)
            assert " --> " in output.content


class TestCaptionAdapter:
    """Caption adapter transformation tests."""

    def test_punctuation_merging(self, verified_sample_transcript):
        """Punctuation is merged onto preceding words in the adapter output."""
        words = transcript_to_caption_words(verified_sample_transcript)

        # After merging, "today" + "?" becomes "today?" as one word
        word_texts = [w.text for w in words if not w.is_speaker_marker]
        assert any("?" in t for t in word_texts)

    def test_speaker_em_dash_injection(self):
        """Em-dash markers injected at speaker changes (except first speaker)."""
        speaker1 = SpeakerInfo(soniox_label="1", display_name="Speaker 1", uuid="uuid-1")
        speaker2 = SpeakerInfo(soniox_label="2", display_name="Speaker 2", uuid="uuid-2")

        words_seg1 = [
            AssembledWord(text="Hello", start_s=0.0, duration_s=0.5, confidence=0.9, word_type="word", speaker="1"),
        ]
        words_seg2 = [
            AssembledWord(text="Hi", start_s=1.0, duration_s=0.3, confidence=0.95, word_type="word", speaker="2"),
        ]

        transcript = Transcript(
            segments=[
                Segment(speaker="1", language="en", start_s=0.0, duration_s=0.5, words=words_seg1),
                Segment(speaker="2", language="en", start_s=1.0, duration_s=0.3, words=words_seg2),
            ],
            speakers=[speaker1, speaker2],
            primary_language="en",
            source_filename="test.mp4",
            duration_s=1.3,
        )

        caption_words = transcript_to_caption_words(transcript)
        markers = [w for w in caption_words if w.is_speaker_marker]
        assert len(markers) == 1  # Only second speaker gets em-dash

    def test_eos_to_segment_start_flip(self):
        """EOS on last word of sentence becomes segment_start on next sentence's first word."""
        speaker1 = SpeakerInfo(soniox_label="1", display_name="Speaker 1", uuid="uuid-1")

        words = [
            AssembledWord(text="Hello", start_s=0.0, duration_s=0.3, confidence=0.9, word_type="word", eos=True, speaker="1"),
            AssembledWord(text=".", start_s=0.3, duration_s=0.02, confidence=0.99, word_type="punctuation", speaker="1"),
            AssembledWord(text="World", start_s=0.5, duration_s=0.3, confidence=0.95, word_type="word", speaker="1"),
        ]

        transcript = Transcript(
            segments=[
                Segment(speaker="1", language="en", start_s=0.0, duration_s=0.8, words=words),
            ],
            speakers=[speaker1],
            primary_language="en",
            source_filename="test.mp4",
            duration_s=0.8,
        )

        caption_words = transcript_to_caption_words(transcript)
        non_markers = [w for w in caption_words if not w.is_speaker_marker]

        # First word: is_segment_start=True (always)
        assert non_markers[0].is_segment_start is True
        # "World" should have is_segment_start=True (after sentence ending)
        # Find "World" in the list
        world_word = [w for w in non_markers if "World" in w.text]
        assert len(world_word) == 1
        assert world_word[0].is_segment_start is True

    def test_first_word_is_segment_start(self, verified_sample_transcript):
        """The first word in the transcript always gets is_segment_start=True."""
        words = transcript_to_caption_words(verified_sample_transcript)
        non_markers = [w for w in words if not w.is_speaker_marker]
        assert non_markers[0].is_segment_start is True
