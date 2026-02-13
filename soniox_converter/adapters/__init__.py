"""Adapter modules for converting between IR and external library formats.

WHY: The converter IR (AssembledWord, Segment, Transcript) uses a different
data model than the caption formatting library (format_captions.Word). Adapters
bridge these representations so each side can evolve independently.

HOW: Each adapter module provides a conversion function that maps IR dataclasses
to the target library's input types, handling any semantic transformations
(punctuation merging, signal shifting, etc.) required by the target.

RULES:
- Adapters are pure data transformations — no I/O, no side effects.
- Each adapter lives in its own module under this package.
- Adapters must not modify the source IR objects.
"""

from soniox_converter.adapters.caption_adapter import transcript_to_caption_words

__all__ = ["transcript_to_caption_words"]
