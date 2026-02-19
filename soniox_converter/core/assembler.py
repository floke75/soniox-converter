"""Sub-word token assembly, punctuation classification, EOS inference,
and Transcript IR construction.

WHY: Soniox uses BPE tokenization, splitting words like "fantastic"
into [" fan", "tastic"]. Downstream formatters need whole words with
unified timing, confidence, and speaker attribution. This module is
the bridge between the flat Soniox token array and the structured IR.

HOW: A leading space in token.text signals a new word boundary.
Continuation tokens (no leading space) are appended to the current
word. Punctuation-only tokens become standalone items. After assembly,
a second pass infers end-of-sentence (EOS) markers from sentence-ending
punctuation. Finally, build_transcript groups assembled words into
speaker-turn segments and produces the complete Transcript IR.

RULES:
- Leading space → new word (strip the space from output text)
- No leading space + existing word → continuation (extend end_ms, append confidence)
- Punctuation-only token → standalone (word_type="punctuation")
- First token in array → new word (even without leading space)
- Confidence aggregation: minimum across sub-word tokens
- Timestamps: ms → seconds (start_ms / 1000.0, (end_ms - start_ms) / 1000.0)
- EOS: word immediately before ".", "?", or "!" gets eos=True
- Translation tokens (translation_status="translation") must be filtered
  before calling assemble_tokens
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from typing import Any, List, Optional

from soniox_converter.core.ir import AssembledWord, Segment, SpeakerInfo, Transcript


# Regex matching tokens that consist entirely of punctuation characters.
# These become standalone punctuation items in the IR.
_PUNCTUATION_RE = re.compile(r"^[.,!?;:…—–\-]+$")

# Punctuation marks that signal end of sentence.
_EOS_PUNCTUATION = frozenset({".", "?", "!"})


def filter_translation_tokens(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove translation tokens from the Soniox token array.

    WHY: When translation is enabled in the Soniox request, the token
    array contains interleaved translation tokens that have no audio
    alignment and must not be assembled into words.

    HOW: Check each token's translation_status field. Keep tokens where
    the field is "original", "none", or absent. Discard "translation".

    RULES:
    - Keep: translation_status is "original", "none", or missing
    - Discard: translation_status is "translation"
    """
    return [
        t for t in tokens
        if t.get("translation_status", "none") != "translation"
    ]


def assemble_tokens(tokens: list[dict[str, Any]]) -> list[AssembledWord]:
    """Assemble Soniox sub-word tokens into whole words.

    WHY: Soniox uses BPE tokenization, splitting words like "fantastic"
    into [" fan", "tastic"]. Downstream formatters need whole words with
    unified timing, confidence, and speaker attribution.

    HOW: A leading space in token.text signals a new word boundary.
    Continuation tokens (no leading space) are appended to the current
    word. Punctuation-only tokens become standalone items.

    RULES:
    - Leading space → new word (strip the space from output text)
    - No leading space + existing word → continuation (extend end_ms, append confidence)
    - Punctuation-only token → standalone (word_type="punctuation")
    - First token in array → new word (even without leading space)
    - Translation tokens (translation_status="translation") must
      already be filtered out before calling this function

    Args:
        tokens: Flat list of Soniox token dicts from the async API response.

    Returns:
        List of AssembledWord objects with unified text, timing, confidence,
        speaker, and language fields ready for segmentation and formatting.
    """
    words: list[AssembledWord] = []

    # Accumulator for building multi-token words
    current_text: str | None = None
    current_start_ms: int = 0
    current_end_ms: int = 0
    current_confidences: list[float] = []
    current_speaker: str | None = None
    current_language: str | None = None

    def _flush_current() -> None:
        """Emit the current accumulated word, if any."""
        nonlocal current_text
        if current_text is not None:
            words.append(AssembledWord(
                text=current_text,
                start_s=current_start_ms / 1000.0,
                duration_s=(current_end_ms - current_start_ms) / 1000.0,
                confidence=min(current_confidences),
                word_type="word",
                speaker=current_speaker,
                language=current_language,
            ))
            current_text = None

    for token in tokens:
        text: str = token["text"]
        start_ms: int = token["start_ms"]
        end_ms: int = token["end_ms"]
        confidence: float = token["confidence"]
        speaker: str | None = token.get("speaker")
        language: str | None = token.get("language")

        # Rule 3: Punctuation-only tokens → standalone
        if _PUNCTUATION_RE.match(text):
            _flush_current()
            words.append(AssembledWord(
                text=text,
                start_s=start_ms / 1000.0,
                duration_s=(end_ms - start_ms) / 1000.0,
                confidence=confidence,
                word_type="punctuation",
                speaker=speaker,
                language=language,
            ))
            continue

        # Rule 1: Leading space → new word
        # Rule 4: First token (current_text is None) → new word
        if text.startswith(" ") or current_text is None:
            _flush_current()
            current_text = text.lstrip(" ")
            current_start_ms = start_ms
            current_end_ms = end_ms
            current_confidences = [confidence]
            current_speaker = speaker
            current_language = language
        else:
            # Rule 2: Continuation token → extend current word
            current_text += text
            current_end_ms = end_ms
            current_confidences.append(confidence)

    # Flush any remaining word
    _flush_current()

    # Second pass: infer EOS from sentence-ending punctuation
    _infer_eos(words)

    return words


def _infer_eos(words: list[AssembledWord]) -> None:
    """Set eos=True on words immediately before sentence-ending punctuation.

    WHY: Soniox provides no explicit sentence boundary. EOS is inferred
    from punctuation marks that conventionally end sentences.

    HOW: Scan for punctuation tokens whose text is ".", "?", or "!".
    The word immediately before each such token gets eos=True.

    RULES:
    - Sentence-ending punctuation: ".", "?", "!"
    - The WORD (not punctuation) before the sentence-ender gets eos=True
    - Commas, colons, semicolons are NOT sentence-ending
    - If there's no preceding word, do nothing (edge case)
    """
    for i, word in enumerate(words):
        if word.word_type == "punctuation" and word.text in _EOS_PUNCTUATION:
            # Find the nearest preceding word (not punctuation)
            for j in range(i - 1, -1, -1):
                if words[j].word_type == "word":
                    words[j].eos = True
                    break


def _build_segment(
    words: List[AssembledWord],
    speaker: Optional[str],
) -> Segment:
    """Build a single Segment from a list of words.

    WHY: Segments group contiguous words from a single speaker with
    timing and language metadata.

    HOW: Computes start/duration from first and last word timing.
    Determines the dominant language from word languages.

    RULES:
    - start_s is the first word's start
    - duration_s spans from first word start to last word end
    - language is the most frequent language among words in this segment
    """
    first = words[0]
    last_w = words[-1]
    start_s = first.start_s
    duration_s = (last_w.start_s + last_w.duration_s) - start_s

    # Dominant language in this segment
    lang_counts = Counter()  # type: Counter
    for w in words:
        if w.language:
            lang_counts[w.language] += 1
    language = lang_counts.most_common(1)[0][0] if lang_counts else ""

    return Segment(
        speaker=speaker,
        language=language,
        start_s=start_s,
        duration_s=duration_s,
        words=list(words),
    )


def build_transcript(
    words: List[AssembledWord],
    source_filename: str,
) -> Transcript:
    """Build a Transcript IR from assembled words.

    WHY: The assembler produces a flat list of AssembledWord objects.
    Formatters expect a Transcript with speaker-grouped segments,
    speaker metadata, and language info. This function bridges the gap.

    HOW: Walks through words and creates a new Segment whenever the
    speaker label changes. Collects unique speakers and assigns UUIDs
    and display names. Determines the primary language by majority vote.

    RULES:
    - New segment whenever speaker changes (speaker-turn segmentation)
    - SpeakerInfo gets a UUID v4 and "Speaker N" display name
    - Primary language is the most frequent language among words
    - Duration is from start of first word to end of last word

    Args:
        words: Flat list of AssembledWord objects from the assembler.
        source_filename: Original audio/video filename for output naming.

    Returns:
        Complete Transcript IR ready for formatters.
    """
    if not words:
        return Transcript(
            segments=[],
            speakers=[],
            primary_language="",
            source_filename=source_filename,
            duration_s=0.0,
        )

    # Build segments by speaker turns
    segments = []  # type: List[Segment]
    current_speaker = words[0].speaker  # type: Optional[str]
    current_words = [words[0]]  # type: List[AssembledWord]

    for word in words[1:]:
        if word.speaker != current_speaker and word.word_type == "word":
            # Flush current segment
            segments.append(_build_segment(current_words, current_speaker))
            current_words = [word]
            current_speaker = word.speaker
        else:
            current_words.append(word)

    # Flush last segment
    if current_words:
        segments.append(_build_segment(current_words, current_speaker))

    # Build speaker info
    seen_speakers = {}  # type: dict
    speaker_list = []  # type: List[SpeakerInfo]
    speaker_index = 1
    for seg in segments:
        label = seg.speaker
        if label is not None and label not in seen_speakers:
            info = SpeakerInfo(
                soniox_label=label,
                display_name="Speaker {}".format(speaker_index),
                uuid=str(uuid.uuid4()),
            )
            seen_speakers[label] = info
            speaker_list.append(info)
            speaker_index += 1

    # Determine primary language by majority vote
    lang_counts = Counter()  # type: Counter
    for word in words:
        if word.language:
            lang_counts[word.language] += 1
    primary_language = lang_counts.most_common(1)[0][0] if lang_counts else ""

    # Total duration
    last_word = words[-1]
    duration_s = last_word.start_s + last_word.duration_s

    return Transcript(
        segments=segments,
        speakers=speaker_list,
        primary_language=primary_language,
        source_filename=source_filename,
        duration_s=duration_s,
    )
