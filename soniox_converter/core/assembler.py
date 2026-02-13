"""Sub-word token assembly, punctuation classification, and EOS inference.

WHY: Soniox uses BPE tokenization, splitting words like "fantastic"
into [" fan", "tastic"]. Downstream formatters need whole words with
unified timing, confidence, and speaker attribution. This module is
the bridge between the flat Soniox token array and the structured IR.

HOW: A leading space in token.text signals a new word boundary.
Continuation tokens (no leading space) are appended to the current
word. Punctuation-only tokens become standalone items. After assembly,
a second pass infers end-of-sentence (EOS) markers from sentence-ending
punctuation.

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
from typing import Any

from soniox_converter.core.ir import AssembledWord


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
