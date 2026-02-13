"""Adapter: Transcript IR to caption formatter Word objects.

WHY: The converter IR keeps punctuation as separate AssembledWord tokens,
marks end-of-sentence on the *last* word (eos=True), and uses speaker labels
on each word. The caption formatting library expects punctuation merged onto
words, sentence boundaries marked on the *first* word of the next sentence
(is_segment_start=True), and speaker changes signalled by synthetic em-dash
Word objects (is_speaker_marker=True). This adapter bridges those differences.

HOW: Four transformations applied in order:
  1. Timing conversion — AssembledWord.start_s → Word.start,
     AssembledWord.start_s + duration_s → Word.end.
  2. Punctuation merging — standalone punctuation tokens are attached to
     the preceding word's text; the merged word's end time extends to the
     punctuation's end.
  3. Speaker change em-dash injection — a synthetic Word("–") with
     is_speaker_marker=True is inserted before the first word of each
     new speaker (except the very first speaker in the transcript).
  4. EOS→segment_start flip — the IR's eos=True on the last word of a
     sentence is shifted forward: the first word *after* a sentence-ending
     punctuation gets is_segment_start=True in the caption Word list.

RULES:
- Input Transcript is never modified.
- Punctuation characters that merge: . , ? ! ; : … —
- Maximum 3 consecutive punctuation tokens merge onto one word.
- The first word in the entire transcript gets is_segment_start=True.
- Em-dash marker words inherit the timestamp of the following word.
- Python 3.9.6 compatible — no slots, no match/case, no X | Y unions.
"""

from typing import List, Optional

from format_captions.models import Word as CaptionWord
from soniox_converter.core.ir import AssembledWord, Transcript

# Punctuation characters that merge onto the preceding word.
_MERGE_PUNCTUATION = frozenset({".", ",", "?", "!", ";", ":", "\u2026", "\u2014"})

# Sentence-ending punctuation (triggers segment_start on the next word).
_SENTENCE_ENDING = frozenset({".", "?", "!"})


class _MergedWord:
    """Intermediate representation during the merge/injection pipeline.

    Not a public class — exists only within this module to carry metadata
    through the multi-step transformation without polluting CaptionWord.
    """

    __slots__ = ("text", "start", "end", "speaker", "eos",
                 "ends_sentence", "is_speaker_marker")

    def __init__(
        self,
        text: str,
        start: float,
        end: float,
        speaker: Optional[str],
        eos: bool,
        ends_sentence: bool,
        is_speaker_marker: bool = False,
    ) -> None:
        self.text = text
        self.start = start
        self.end = end
        self.speaker = speaker
        self.eos = eos
        self.ends_sentence = ends_sentence
        self.is_speaker_marker = is_speaker_marker


def transcript_to_caption_words(transcript: Transcript) -> List[CaptionWord]:
    """Convert a Transcript IR into a flat list of caption Word objects.

    WHY: The SRT caption formatter needs caption Words, not IR AssembledWords.
    This function performs the four non-trivial mappings documented in
    PRD Section 6.3.2 so the caption library can produce optimised SRT output.

    HOW: Flattens all segments into a word stream, applies punctuation merging,
    speaker-change em-dash injection, and EOS-to-segment_start flipping.

    RULES:
    - Returns an empty list if the transcript has no words.
    - Standalone punctuation at the very start (no preceding word) is dropped.
    - Em-dash markers use the following word's timestamps.

    Args:
        transcript: Complete Transcript IR with segments and speaker info.

    Returns:
        Flat list of format_captions.Word objects ready for format_srt().
    """
    # --- Step 0: Flatten all segments into a single word stream ---
    flat_words: List[AssembledWord] = []
    for segment in transcript.segments:
        flat_words.extend(segment.words)

    if not flat_words:
        return []

    # --- Step 1+2: Convert timing and merge punctuation ---
    merged = _merge_punctuation(flat_words)

    # --- Step 3: Inject speaker-change em-dash markers ---
    with_speakers = _inject_speaker_markers(merged, flat_words)

    # --- Step 4: Flip EOS → segment_start ---
    result = _apply_segment_starts(with_speakers)

    return result


def _merge_punctuation(
    words: List[AssembledWord],
) -> List[_MergedWord]:
    """Merge standalone punctuation tokens onto the preceding word.

    Returns a list of _MergedWord (intermediate struct carrying the
    merged text, timing, and the original speaker/eos metadata).
    """
    merged: List[_MergedWord] = []
    merge_count = 0

    for word in words:
        if (
            word.word_type == "punctuation"
            and word.text in _MERGE_PUNCTUATION
            and merged
            and merge_count < 3
        ):
            # Merge onto preceding word
            prev = merged[-1]
            prev.text += word.text
            prev.end = word.start_s + word.duration_s
            # Track if this punctuation is sentence-ending
            if word.text in _SENTENCE_ENDING:
                prev.ends_sentence = True
            merge_count += 1
        elif word.word_type == "punctuation" and word.text in _MERGE_PUNCTUATION and not merged:
            # Standalone punctuation at the very start — drop it
            continue
        else:
            merge_count = 0
            merged.append(_MergedWord(
                text=word.text,
                start=word.start_s,
                end=word.start_s + word.duration_s,
                speaker=word.speaker,
                eos=word.eos,
                ends_sentence=False,
            ))

    return merged


def _inject_speaker_markers(
    merged: List[_MergedWord],
    original_words: List[AssembledWord],
) -> List[_MergedWord]:
    """Insert em-dash markers before the first word of each new speaker.

    The em-dash Word inherits the timestamp of the word that follows it.
    The first speaker in the transcript does NOT get an em-dash.
    """
    if not merged:
        return merged

    result: List[_MergedWord] = [merged[0]]
    prev_speaker = merged[0].speaker

    for mw in merged[1:]:
        if mw.speaker != prev_speaker and mw.speaker is not None:
            # Inject em-dash marker with the next word's timing
            marker = _MergedWord(
                text="\u2013",  # en-dash "–"
                start=mw.start,
                end=mw.start,  # zero-duration marker
                speaker=mw.speaker,
                eos=False,
                ends_sentence=False,
                is_speaker_marker=True,
            )
            result.append(marker)
        prev_speaker = mw.speaker
        result.append(mw)

    return result


def _apply_segment_starts(merged: List[_MergedWord]) -> List[CaptionWord]:
    """Convert _MergedWord list to CaptionWord list with segment_start flags.

    The first non-marker word gets is_segment_start=True.
    After any word whose ends_sentence is True, the next non-marker word
    gets is_segment_start=True.
    """
    result: List[CaptionWord] = []
    next_is_segment_start = True  # First word is always a segment start

    for mw in merged:
        if mw.is_speaker_marker:
            result.append(CaptionWord(
                text=mw.text,
                start=mw.start,
                end=mw.end,
                is_speaker_marker=True,
                is_segment_start=False,
            ))
            continue

        result.append(CaptionWord(
            text=mw.text,
            start=mw.start,
            end=mw.end,
            is_speaker_marker=False,
            is_segment_start=next_is_segment_start,
        ))

        # If this word ends a sentence, the next non-marker word starts a new segment
        next_is_segment_start = mw.ends_sentence

    return result
