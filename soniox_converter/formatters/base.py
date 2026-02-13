"""Abstract base formatter and output container.

WHY: Every output format consumes the same Transcript IR but produces
different file content. This base class enforces a consistent interface
so the GUI, CLI, and API layers can work with any formatter generically.

HOW: BaseFormatter is an ABC with two requirements — a ``name`` property
and a ``format()`` method. FormatterOutput is a plain dataclass that
bundles a file suffix with its content (string or bytes) and MIME type.

RULES:
- Subclasses MUST implement ``name`` (human-readable) and ``format()``
- ``format()`` returns a list — most formatters return one item, but
  multi-file formatters (e.g. Kinetic Word Reveal) return several
- ``suffix`` starts with a hyphen, e.g. ``"-transcript.json"``
- The caller is responsible for prepending the source filename stem
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from soniox_converter.core.ir import Transcript


@dataclass
class FormatterOutput:
    """One output file produced by a formatter.

    Attributes:
        suffix: File suffix appended to the source stem,
                e.g. ``"-transcript.json"`` → ``"interview-transcript.json"``.
        content: The file content as a string (JSON, SRT, plain text)
                 or bytes (future binary formats).
        media_type: MIME type for the content, e.g. ``"application/json"``.
    """

    suffix: str
    content: str | bytes
    media_type: str


class BaseFormatter(ABC):
    """Abstract base for all output formatters.

    WHY: Every output format consumes the same Transcript IR but produces
    different file content. This base class enforces a consistent interface
    so the GUI, CLI, and API layers can work with any formatter generically.

    MULTI-FILE SUPPORT: Most formatters produce one file, but some (e.g.,
    Kinetic Word Reveal) produce multiple. The format() method returns a
    list of FormatterOutput objects. Single-file formatters return a list
    of one.

    To add a new output format:
    1. Create a new file in formatters/
    2. Subclass BaseFormatter
    3. Implement format() and name
    4. Register in FORMATTERS dict in formatters/__init__.py
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable format name, e.g. 'Premiere Pro JSON'."""

    @abstractmethod
    def format(self, transcript: Transcript) -> list[FormatterOutput]:
        """Convert the Transcript IR into one or more output files.

        Args:
            transcript: The complete intermediate representation of an
                        assembled transcript, including segments, speakers,
                        and language metadata.

        Returns:
            List of FormatterOutput objects, each containing a file suffix,
            content string/bytes, and MIME type.
        """
