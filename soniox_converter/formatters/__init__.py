"""Output formatter registry — pluggable format hub.

WHY: The CLI, GUI, and API layers need a single lookup to find the right
formatter by name. A central dict makes it trivial to add new formats:
create the formatter class, import it here, add one line.

HOW: FORMATTERS maps string keys to formatter *classes* (not instances).
Callers instantiate as needed: ``formatter = FORMATTERS["premiere_pro"]()``.

RULES:
- Keys are snake_case identifiers (used in CLI flags, config, etc.)
- Values are BaseFormatter subclasses (not instances)
- Every formatter listed here must be importable without side effects
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from soniox_converter.formatters.kinetic_words import KineticWordsFormatter
from soniox_converter.formatters.plain_text import PlainTextFormatter
from soniox_converter.formatters.premiere_pro import PremiereProFormatter
from soniox_converter.formatters.srt_captions import SRTCaptionFormatter

if TYPE_CHECKING:
    from soniox_converter.formatters.base import BaseFormatter

FORMATTERS: dict[str, type[BaseFormatter]] = {
    "premiere_pro": PremiereProFormatter,
    "plain_text": PlainTextFormatter,
    "kinetic_words": KineticWordsFormatter,
    "srt_captions": SRTCaptionFormatter,
}
