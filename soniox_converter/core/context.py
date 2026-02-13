"""Helper functions for loading Soniox context files.

WHY: Soniox's `context` parameter on POST /v1/transcriptions provides
optional sections (text, terms, general) that improve transcription
accuracy. The converter discovers companion files next to the audio
file (e.g. interview-script.txt, interview-terms.txt) and a project-wide
default-terms.txt, then assembles them into the context dict that the
API client sends.

HOW: resolve_companion_files() discovers files by naming convention.
load_script() and load_terms() read their respective file formats.
load_default_terms() reads the project-wide default-terms.txt.
build_context() assembles everything into the Soniox context dict
and validates total size (≤ 10,000 chars).

RULES:
- Companion files: {stem}-script.txt and {stem}-terms.txt next to audio
- Terms files: one term per line, strip whitespace, ignore blank lines
  and lines starting with '#'
- default-terms.txt: project-wide terms in the working directory
- Context size limit: ≤ 10,000 characters total
- All functions are pure (no side effects beyond file I/O)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Maximum total size of the context dict in characters.
# Soniox limit is ~8,000 tokens ≈ 10,000 characters.
MAX_CONTEXT_SIZE = 10_000


@dataclass
class ContextFiles:
    """Resolved paths to optional companion context files.

    WHY: The converter needs to know which context files exist before
    loading them. This dataclass holds the resolved paths (or None)
    so the caller can decide what to load.

    HOW: resolve_companion_files() populates this from the audio file's
    directory using the naming convention {stem}-script.txt and
    {stem}-terms.txt.

    RULES:
    - script_path: path to {stem}-script.txt, or None if not found
    - terms_path: path to {stem}-terms.txt, or None if not found
    - default_terms_path: path to default-terms.txt, or None if not found
    """

    script_path: Path | None = None
    terms_path: Path | None = None
    default_terms_path: Path | None = None


def resolve_companion_files(audio_path: str | Path) -> ContextFiles:
    """Discover companion context files next to the audio file.

    WHY: Users place optional script and terms files alongside their
    audio files. The converter auto-discovers them by naming convention
    so users don't have to specify paths manually.

    HOW: Given audio_path, derive the stem and look for:
      {stem}-script.txt  → loaded into context.text
      {stem}-terms.txt   → loaded into context.terms
      default-terms.txt  → in the audio file's directory (project-wide terms)

    RULES:
    - Only .txt files are recognized
    - Files must exist to be included (no error for missing files)
    - The stem is derived by stripping all extensions from the filename

    Args:
        audio_path: Path to the source audio/video file.

    Returns:
        ContextFiles with resolved paths (None for files not found).
    """
    audio = Path(audio_path)
    directory = audio.parent

    # Strip all extensions to get the stem (e.g. "interview.mp4.wav" → "interview")
    stem = audio.name
    while "." in stem:
        stem = stem.rsplit(".", 1)[0]

    result = ContextFiles()

    script_path = directory / f"{stem}-script.txt"
    if script_path.is_file():
        result.script_path = script_path

    terms_path = directory / f"{stem}-terms.txt"
    if terms_path.is_file():
        result.terms_path = terms_path

    default_terms_path = directory / "default-terms.txt"
    if default_terms_path.is_file():
        result.default_terms_path = default_terms_path

    return result


def load_script(path: str | Path) -> str:
    """Load a reference script from a text file.

    WHY: A reference script helps Soniox produce more accurate
    transcriptions by providing expected text content.

    HOW: Read the entire file as UTF-8 text, strip leading/trailing
    whitespace.

    RULES:
    - File must be UTF-8 encoded
    - Returns the full text content, stripped of leading/trailing whitespace
    - Raises FileNotFoundError if the file doesn't exist

    Args:
        path: Path to the script text file.

    Returns:
        The script text content.
    """
    return Path(path).read_text(encoding="utf-8").strip()


def load_terms(path: str | Path) -> list[str]:
    """Load domain vocabulary terms from a text file.

    WHY: Domain-specific terms (brand names, proper nouns, technical
    vocabulary) help Soniox recognize words it might otherwise
    misinterpret.

    HOW: Read the file line by line. Strip whitespace from each line.
    Skip blank lines and lines starting with '#' (comments).

    RULES:
    - One term per line
    - Strip leading/trailing whitespace from each line
    - Ignore blank lines (after stripping)
    - Ignore lines starting with '#' (comment lines)
    - File must be UTF-8 encoded

    Args:
        path: Path to the terms text file.

    Returns:
        List of term strings, in file order.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    terms: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        terms.append(stripped)
    return terms


def load_default_terms(directory: str | Path) -> list[str]:
    """Load project-wide default terms from the working directory.

    WHY: Some terms apply to all files in a project (e.g. company name,
    product names). A default-terms.txt in the working directory provides
    these without requiring per-file terms files.

    HOW: Look for default-terms.txt in the given directory. If it exists,
    load it using load_terms(). If not, return an empty list.

    RULES:
    - Looks for "default-terms.txt" in the specified directory
    - Returns empty list if the file doesn't exist
    - Same format as per-file terms (one per line, # comments, etc.)

    Args:
        directory: Directory to search for default-terms.txt.

    Returns:
        List of default term strings, or empty list if no file found.
    """
    default_path = Path(directory) / "default-terms.txt"
    if default_path.is_file():
        return load_terms(default_path)
    return []


def build_context(
    script_text: str | None = None,
    terms: list[str] | None = None,
    general: list[dict[str, str]] | None = None,
) -> dict | None:
    """Assemble the Soniox context dict from components.

    WHY: The Soniox context parameter has a specific structure with
    optional sections. This function assembles the components into
    the correct format and validates total size.

    HOW: Build a dict with non-None sections. Estimate total character
    count and raise ValueError if it exceeds MAX_CONTEXT_SIZE.

    RULES:
    - Only include sections that have content (skip None/empty)
    - text: string (the script)
    - terms: list of strings (domain vocabulary)
    - general: list of {key, value} dicts (domain metadata)
    - Total size must be ≤ 10,000 characters
    - Returns None if all inputs are None/empty (no context to send)

    Args:
        script_text: Optional reference script text.
        terms: Optional list of domain vocabulary terms.
        general: Optional list of general metadata dicts.

    Returns:
        Context dict ready for the Soniox API, or None if empty.

    Raises:
        ValueError: If the total context size exceeds 10,000 characters.
    """
    context: dict = {}

    if script_text:
        context["text"] = script_text

    if terms:
        context["terms"] = terms

    if general:
        context["general"] = general

    if not context:
        return None

    # Estimate total size: stringify and measure characters
    total_size = _estimate_context_size(context)
    if total_size > MAX_CONTEXT_SIZE:
        raise ValueError(
            f"Context size ({total_size:,} chars) exceeds the "
            f"{MAX_CONTEXT_SIZE:,}-character limit. "
            f"Reduce the script text or number of terms."
        )

    return context


def _estimate_context_size(context: dict) -> int:
    """Estimate the total character count of a context dict.

    WHY: Soniox limits context to ~8,000 tokens ≈ 10,000 characters.
    We need a quick estimate before sending.

    HOW: Sum the character lengths of all string values in the context.
    For lists of strings (terms), sum all string lengths.
    For lists of dicts (general), sum all key and value lengths.

    RULES:
    - Conservative estimate — counts only content characters
    - Does not account for JSON structural overhead (brackets, quotes)
    """
    total = 0

    if "text" in context:
        total += len(context["text"])

    if "terms" in context:
        total += sum(len(t) for t in context["terms"])

    if "general" in context:
        for item in context["general"]:
            total += len(item.get("key", "")) + len(item.get("value", ""))

    return total
