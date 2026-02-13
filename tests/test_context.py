"""Unit tests for the context module.

WHY: Context files (script, terms, default-terms) directly affect Soniox
transcription accuracy. Incorrect discovery, parsing, or size validation
could silently degrade quality or cause API rejections.

HOW: Tests cover companion file discovery by naming convention, terms file
parsing (blank lines, comments, whitespace), default terms loading, context
size validation, and the build_context() assembly function.

RULES:
- All file I/O tests use tmp_path fixtures for isolation.
- Size validation uses the MAX_CONTEXT_SIZE constant from the module.
"""

import pytest

from soniox_converter.core.context import (
    MAX_CONTEXT_SIZE,
    ContextFiles,
    build_context,
    load_default_terms,
    load_script,
    load_terms,
    resolve_companion_files,
)


class TestCompanionFileDiscovery:
    """resolve_companion_files discovers {stem}-script.txt and {stem}-terms.txt."""

    def test_finds_script_and_terms(self, tmp_path):
        audio = tmp_path / "interview.mp4"
        audio.touch()
        script = tmp_path / "interview-script.txt"
        script.write_text("Script content", encoding="utf-8")
        terms = tmp_path / "interview-terms.txt"
        terms.write_text("term1\nterm2", encoding="utf-8")

        result = resolve_companion_files(audio)
        assert result.script_path == script
        assert result.terms_path == terms

    def test_finds_nothing_when_no_companions(self, tmp_path):
        audio = tmp_path / "interview.mp4"
        audio.touch()

        result = resolve_companion_files(audio)
        assert result.script_path is None
        assert result.terms_path is None

    def test_finds_default_terms(self, tmp_path):
        audio = tmp_path / "interview.mp4"
        audio.touch()
        default = tmp_path / "default-terms.txt"
        default.write_text("default_term", encoding="utf-8")

        result = resolve_companion_files(audio)
        assert result.default_terms_path == default

    def test_strips_multiple_extensions(self, tmp_path):
        """'interview.mp4.wav' should have stem 'interview'."""
        audio = tmp_path / "interview.mp4.wav"
        audio.touch()
        script = tmp_path / "interview-script.txt"
        script.write_text("Script", encoding="utf-8")

        result = resolve_companion_files(audio)
        assert result.script_path == script

    def test_partial_companions(self, tmp_path):
        """Only script exists, no terms."""
        audio = tmp_path / "clip.mp4"
        audio.touch()
        script = tmp_path / "clip-script.txt"
        script.write_text("Script text", encoding="utf-8")

        result = resolve_companion_files(audio)
        assert result.script_path == script
        assert result.terms_path is None


class TestTermsFileParsing:
    """load_terms parses one term per line, skipping blanks and comments."""

    def test_basic_terms(self, tmp_path):
        f = tmp_path / "terms.txt"
        f.write_text("Melodifestivalen\nSVT\nEurovision", encoding="utf-8")
        terms = load_terms(f)
        assert terms == ["Melodifestivalen", "SVT", "Eurovision"]

    def test_blank_lines_skipped(self, tmp_path):
        f = tmp_path / "terms.txt"
        f.write_text("term1\n\n\nterm2\n", encoding="utf-8")
        terms = load_terms(f)
        assert terms == ["term1", "term2"]

    def test_comment_lines_skipped(self, tmp_path):
        f = tmp_path / "terms.txt"
        f.write_text("# This is a comment\nterm1\n# Another comment\nterm2", encoding="utf-8")
        terms = load_terms(f)
        assert terms == ["term1", "term2"]

    def test_whitespace_stripped(self, tmp_path):
        f = tmp_path / "terms.txt"
        f.write_text("  term1  \n\tterm2\t\n", encoding="utf-8")
        terms = load_terms(f)
        assert terms == ["term1", "term2"]

    def test_empty_file(self, tmp_path):
        f = tmp_path / "terms.txt"
        f.write_text("", encoding="utf-8")
        terms = load_terms(f)
        assert terms == []

    def test_only_comments_and_blanks(self, tmp_path):
        f = tmp_path / "terms.txt"
        f.write_text("# comment\n\n# another\n  \n", encoding="utf-8")
        terms = load_terms(f)
        assert terms == []


class TestDefaultTermsLoading:
    """load_default_terms reads project-wide default-terms.txt."""

    def test_loads_when_present(self, tmp_path):
        f = tmp_path / "default-terms.txt"
        f.write_text("Brand\nProduct", encoding="utf-8")
        terms = load_default_terms(tmp_path)
        assert terms == ["Brand", "Product"]

    def test_returns_empty_when_missing(self, tmp_path):
        terms = load_default_terms(tmp_path)
        assert terms == []


class TestContextSizeValidation:
    """build_context validates total size <= MAX_CONTEXT_SIZE."""

    def test_valid_context_passes(self):
        result = build_context(
            script_text="Short script",
            terms=["term1", "term2"],
        )
        assert result is not None
        assert "text" in result
        assert "terms" in result

    def test_oversized_context_raises(self):
        huge_script = "x" * (MAX_CONTEXT_SIZE + 1)
        with pytest.raises(ValueError, match="exceeds"):
            build_context(script_text=huge_script)

    def test_exactly_at_limit(self):
        """Context at exactly MAX_CONTEXT_SIZE should pass."""
        script = "x" * MAX_CONTEXT_SIZE
        result = build_context(script_text=script)
        assert result is not None

    def test_one_over_limit(self):
        """Context at MAX_CONTEXT_SIZE + 1 should fail."""
        script = "x" * (MAX_CONTEXT_SIZE + 1)
        with pytest.raises(ValueError):
            build_context(script_text=script)


class TestBuildContextAssembly:
    """build_context assembles the Soniox context dict from components."""

    def test_all_components(self):
        result = build_context(
            script_text="Test script",
            terms=["term1"],
            general=[{"key": "domain", "value": "testing"}],
        )
        assert result["text"] == "Test script"
        assert result["terms"] == ["term1"]
        assert result["general"] == [{"key": "domain", "value": "testing"}]

    def test_only_script(self):
        result = build_context(script_text="Script only")
        assert result == {"text": "Script only"}

    def test_only_terms(self):
        result = build_context(terms=["a", "b"])
        assert result == {"terms": ["a", "b"]}

    def test_empty_returns_none(self):
        result = build_context()
        assert result is None

    def test_empty_terms_returns_none(self):
        result = build_context(terms=[])
        assert result is None

    def test_none_script_returns_none(self):
        result = build_context(script_text=None)
        assert result is None
