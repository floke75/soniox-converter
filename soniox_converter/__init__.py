"""Soniox Transcript Converter — extensible transcription format hub.

WHY: Soniox ASR produces flat sub-word token arrays that no editing tool
can ingest directly. This package assembles tokens into a well-typed
intermediate representation (IR) and converts to multiple output formats
(Premiere Pro JSON, SRT captions, kinetic word reveal, plain text).

HOW: Three-stage pipeline — ingest (API client), assemble (core IR),
format (pluggable formatters). Each stage is independently testable.

RULES:
- All formatters consume the same Transcript IR
- Adding a new output format = one new formatter module, no core changes
- The IR is the stable contract between assembly and formatting
"""

__version__ = "0.1.0"
