"""Kinetic Word Reveal formatter — three-file SRT for social video.

WHY: Social media video (TikTok, Reels, Shorts) uses animated word-by-word
captions where words pop onto screen one at a time, grouped into "buckets"
of up to 3 words. Each bucket occupies three visual rows (top, middle,
bottom). Adobe Premiere Pro's kinetic word reveal feature reformats JSON
transcripts, destroying our row layout. SRT files preserve the layout
because each subtitle block's text is displayed as-is.

HOW: Words are merged with trailing punctuation, split into sentences via
EOS markers, then each sentence is divided into buckets of ``max_bucket_size``
words (default 3). Each word in a bucket is assigned to a row (1, 2, 3) by
position. Three separate SRT files are produced — one per row — where each
file contains only the words that appear on that row.

RULES:
- Single speaker only — ignores diarization, treats entire transcript as one
- Punctuation is merged onto the preceding word before bucketing
- Buckets are groups of ``max_bucket_size`` words; last bucket gets remainder
- Words appear at their spoken ``start_s``; all words in a bucket share an
  end time (the next bucket's first word ``start_s``, capped by ``max_hold_s``)
- Three output files with suffixes: -kinetic-row1.srt, -kinetic-row2.srt,
  -kinetic-row3.srt
- Each file is a valid SRT subtitle file
- Configurable: max_bucket_size, max_hold_s, final_hold_s, min_word_display_s
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from soniox_converter.core.ir import AssembledWord, Transcript
from soniox_converter.formatters.base import BaseFormatter, FormatterOutput

# Matches tokens that look like part of a number: digits, decimal separators,
# digit groups with spaces (e.g. "120", "000", "2,", "25-"), and lone separators.
_NUMBER_PART_RE = re.compile(r"^[\d.,\-]+$")


@dataclass
class _MergedWord:
    """A word with punctuation merged onto it, ready for bucketing.

    Keeps the original timing and confidence from the IR word, with
    punctuation text appended.
    """

    text: str
    start_s: float
    duration_s: float
    confidence: float
    eos: bool


@dataclass
class _Bucket:
    """A group of up to max_bucket_size merged words that appear/disappear together."""

    words: List[_MergedWord] = field(default_factory=list)
    end_s: float = 0.0  # computed after all buckets are created


def _merge_punctuation(words: List[AssembledWord]) -> List[_MergedWord]:
    """Merge punctuation tokens onto the preceding word.

    WHY: Kinetic captions show one visual word per row slot. Punctuation
    like "!" or "." must be attached to the word before it so the viewer
    sees "world!" not "world" + "!".

    HOW: Walk through words. When a punctuation token is encountered,
    append its text to the previous merged word and extend its duration.
    If punctuation appears at the very start (no preceding word), skip it.
    """
    merged: List[_MergedWord] = []
    for w in words:
        if w.word_type == "punctuation":
            if merged:
                prev = merged[-1]
                prev.text += w.text
                # Extend duration to cover the punctuation token
                new_end = w.start_s + w.duration_s
                prev.duration_s = new_end - prev.start_s
                # Inherit eos from punctuation if set
                if w.eos:
                    prev.eos = True
                # Also mark as sentence end if text ends with sentence-ending punctuation
                if prev.text.endswith(('.', '!', '?')):
                    prev.eos = True
        else:
            mw = _MergedWord(
                text=w.text,
                start_s=w.start_s,
                duration_s=w.duration_s,
                confidence=w.confidence,
                eos=w.eos,
            )
            # Belt-and-suspenders: mark as sentence end if word text
            # ends with sentence-ending punctuation (handles cases where
            # the period is part of the word token, not a separate token)
            if mw.text.endswith(('.', '!', '?')):
                mw.eos = True
            merged.append(mw)
    return merged


def _is_number_part(text: str) -> bool:
    """Check if a token looks like part of a multi-token number.

    Matches: "2,", "5", "120", "000", "25-", "30"
    Does not match: regular words like "miljoner", "kronor"
    """
    return bool(_NUMBER_PART_RE.match(text))


def _group_numbers(words: List[_MergedWord]) -> List[_MergedWord]:
    """Group multi-token numbers into single merged words.

    WHY: Soniox tokenises numbers like "2,5" as ["2,", "5"], "120 000" as
    ["120", "000"], and "25-30" as ["25-", "30"]. In kinetic captions these
    must stay together so "2,5 miljoner" appears as one visual group, not
    split across rows.

    HOW: When a number-like token is followed by another number-like token,
    merge them (with a space if needed). Continue merging as long as the
    next token is also numeric. Then also absorb the following non-numeric
    word (e.g. "miljoner", "kronor") into the same merged word so the
    number and its unit stay together in the same bucket slot.
    """
    if not words:
        return words

    result: List[_MergedWord] = []
    i = 0
    while i < len(words):
        word = words[i]

        # Check if this starts a number sequence
        if _is_number_part(word.text):
            # Accumulate consecutive number parts
            group_text = word.text
            group_start = word.start_s
            group_end = word.start_s + word.duration_s
            group_confidence = word.confidence
            group_eos = word.eos
            j = i + 1

            while j < len(words) and _is_number_part(words[j].text):
                next_w = words[j]
                # Join with space unless previous ends with comma/dash
                # (e.g. "2," + "5" → "2,5" but "120" + "000" → "120 000")
                if group_text.endswith((",", "-")):
                    group_text += next_w.text
                else:
                    group_text += " " + next_w.text
                group_end = next_w.start_s + next_w.duration_s
                group_confidence = min(group_confidence, next_w.confidence)
                if next_w.eos:
                    group_eos = True
                j += 1

            # If we merged multiple number tokens, also absorb the next
            # non-numeric word as the unit (e.g. "miljoner", "kronor")
            if j > i + 1 and j < len(words) and not _is_number_part(words[j].text):
                unit = words[j]
                group_text += " " + unit.text
                group_end = unit.start_s + unit.duration_s
                group_confidence = min(group_confidence, unit.confidence)
                if unit.eos:
                    group_eos = True
                j += 1

            result.append(_MergedWord(
                text=group_text,
                start_s=group_start,
                duration_s=group_end - group_start,
                confidence=group_confidence,
                eos=group_eos,
            ))
            i = j
        else:
            result.append(word)
            i += 1

    return result


def _split_sentences(words: List[_MergedWord]) -> List[List[_MergedWord]]:
    """Split merged words into sentences using EOS markers.

    Each sentence ends at a word with eos=True. Any trailing words
    without EOS form a final sentence.
    """
    sentences: List[List[_MergedWord]] = []
    current: List[_MergedWord] = []
    for w in words:
        current.append(w)
        if w.eos:
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def _make_buckets(sentence: List[_MergedWord], max_bucket_size: int) -> List[_Bucket]:
    """Divide a sentence into buckets of up to max_bucket_size words.

    Words are taken left-to-right in groups. The final bucket gets
    the remainder (1 to max_bucket_size words).
    """
    buckets: List[_Bucket] = []
    for i in range(0, len(sentence), max_bucket_size):
        chunk = sentence[i:i + max_bucket_size]
        buckets.append(_Bucket(words=chunk))
    return buckets


def _compute_bucket_end_times(
    all_buckets: List[_Bucket],
    max_hold_s: float,
    final_hold_s: float,
    min_word_display_s: float,
) -> None:
    """Compute the end time for each bucket in-place.

    Timing rules from PRD 6.5.2:
    1. Normal: next bucket's first word start_s
    2. Last bucket in transcript: last word end + final_hold_s, capped at max_hold
    3. Max hold cap: if gap to next bucket > max_hold, clamp to last_word.start_s + max_hold
    """
    for i, bucket in enumerate(all_buckets):
        last_word = bucket.words[-1]
        last_word_end = last_word.start_s + last_word.duration_s

        if i + 1 < len(all_buckets):
            # There is a next bucket
            next_start = all_buckets[i + 1].words[0].start_s
            # Cap at max_hold from the last word's start
            max_end = last_word.start_s + max_hold_s
            bucket.end_s = min(next_start, max_end)
        else:
            # Final bucket in transcript
            bucket.end_s = last_word_end + final_hold_s
            # Cap at max_hold from the last word's start
            max_end = last_word.start_s + max_hold_s
            bucket.end_s = min(bucket.end_s, max_end)

        # Ensure minimum display time for the last word in the bucket
        min_end = last_word.start_s + min_word_display_s
        if bucket.end_s < min_end:
            bucket.end_s = min_end


def _format_srt_timestamp(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return "{:02d}:{:02d}:{:02d},{:03d}".format(hours, minutes, secs, millis)


def _build_srt_rows(
    all_buckets: List[_Bucket],
    max_bucket_size: int,
) -> List[str]:
    """Build per-row SRT content strings from bucketed words.

    Each bucket assigns words to rows by position: word 0 → row 1,
    word 1 → row 2, word 2 → row 3. Each row gets a separate SRT file.

    Returns a list of ``max_bucket_size`` SRT content strings.
    """
    # Collect subtitle entries per row: list of (start_s, end_s, text)
    row_entries: List[List[tuple]] = [[] for _ in range(max_bucket_size)]

    for bucket in all_buckets:
        bucket_end = bucket.end_s
        for row_idx, word in enumerate(bucket.words):
            text = word.text
            row_entries[row_idx].append((word.start_s, bucket_end, text))

    # Build SRT strings, clamping end times to avoid overlaps within a row
    srt_contents: List[str] = []
    for row_idx in range(max_bucket_size):
        entries = row_entries[row_idx]
        if not entries:
            srt_contents.append("")
            continue

        lines: List[str] = []
        for seq_num, (start_s, end_s, text) in enumerate(entries, 1):
            # Clamp end_s so it doesn't overlap the next entry on this row
            if seq_num < len(entries):
                next_start = entries[seq_num][0]  # seq_num is 1-based, so index = seq_num
                if end_s > next_start:
                    end_s = next_start
            lines.append(str(seq_num))
            lines.append("{} --> {}".format(
                _format_srt_timestamp(start_s),
                _format_srt_timestamp(end_s),
            ))
            lines.append(text)
            lines.append("")  # blank line separator

        srt_contents.append("\n".join(lines))

    return srt_contents


class KineticWordsFormatter(BaseFormatter):
    """Formatter producing three SRT files for kinetic word reveal.

    WHY: Social media video captions need words that pop onto screen one
    at a time in a 3-row stack. Adobe Premiere Pro reformats JSON transcript
    text, destroying our row layout. SRT subtitle files preserve the text
    as-is, so line breaks for vertical positioning are maintained.

    HOW: Merges punctuation, splits into sentences, buckets words into
    groups of 3, computes appear/disappear timing, then distributes
    words across three row files with line-break positioning.

    RULES:
    - Single speaker (ignores diarization)
    - Punctuation merged onto preceding word before bucketing
    - Three output files: -kinetic-row1.srt, -kinetic-row2.srt, -kinetic-row3.srt
    - Row position determined by word index within bucket (0→row1, 1→row2, 2→row3)
    - Configurable via constructor: max_bucket_size, max_hold_s, final_hold_s,
      min_word_display_s
    """

    def __init__(
        self,
        max_bucket_size: int = 3,
        max_hold_s: float = 3.0,
        final_hold_s: float = 1.5,
        min_word_display_s: float = 0.15,
    ) -> None:
        self.max_bucket_size = max_bucket_size
        self.max_hold_s = max_hold_s
        self.final_hold_s = final_hold_s
        self.min_word_display_s = min_word_display_s

    @property
    def name(self) -> str:
        return "Kinetic Word Reveal"

    def format(self, transcript: Transcript) -> List[FormatterOutput]:
        """Convert the Transcript IR into three kinetic word reveal SRT files.

        Args:
            transcript: The complete IR with segments, speakers, and metadata.

        Returns:
            Three FormatterOutput objects, one per row position.
        """
        # Flatten all words from all IR segments
        all_words: List[AssembledWord] = []
        for segment in transcript.segments:
            all_words.extend(segment.words)

        # Step 1: Merge punctuation onto preceding words
        merged = _merge_punctuation(all_words)

        if not merged:
            # Empty transcript — return three empty SRT files
            return self._empty_outputs()

        # Step 1b: Group multi-token numbers (e.g. "2," + "5" → "2,5")
        merged = _group_numbers(merged)

        # Step 2: Split into sentences
        sentences = _split_sentences(merged)

        # Step 3: Bucket each sentence, then flatten into one list
        all_buckets: List[_Bucket] = []
        for sentence in sentences:
            all_buckets.extend(_make_buckets(sentence, self.max_bucket_size))

        # Step 4: Compute end times for each bucket
        _compute_bucket_end_times(
            all_buckets, self.max_hold_s, self.final_hold_s, self.min_word_display_s
        )

        # Step 5: Build per-row SRT content
        srt_contents = _build_srt_rows(all_buckets, self.max_bucket_size)

        # Step 6: Package as FormatterOutput
        suffixes = [
            "-kinetic-row1.srt",
            "-kinetic-row2.srt",
            "-kinetic-row3.srt",
        ]

        outputs: List[FormatterOutput] = []
        for row_idx in range(self.max_bucket_size):
            content = srt_contents[row_idx]
            outputs.append(FormatterOutput(
                suffix=suffixes[row_idx],
                content=content,
                media_type="application/x-subrip",
            ))

        return outputs

    def _empty_outputs(self) -> List[FormatterOutput]:
        """Produce three empty SRT files for an empty transcript."""
        suffixes = [
            "-kinetic-row1.srt",
            "-kinetic-row2.srt",
            "-kinetic-row3.srt",
        ]
        return [
            FormatterOutput(
                suffix=s,
                content="",
                media_type="application/x-subrip",
            )
            for s in suffixes
        ]
