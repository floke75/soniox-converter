"""Core assembly and intermediate representation modules.

WHY: The core package contains the stable heart of the converter —
the IR dataclasses and the token assembly logic. These are consumed
by all formatters and must remain backward-compatible.

HOW: ir.py defines the data structures, assembler.py builds them
from flat Soniox token arrays, context.py handles companion file
discovery and context parameter construction.

RULES:
- IR dataclasses are the contract — change with care
- Assembly logic is format-agnostic — no formatter-specific logic here
- Context module handles file discovery and size validation
"""
