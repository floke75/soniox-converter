"""Command-line interface for the Soniox Transcript Converter.

WHY: Users need a simple way to transcribe audio/video files from the
terminal. The CLI wires together the full pipeline — file validation,
Soniox API upload/transcribe/poll/fetch, token assembly into the IR,
pluggable formatter output, and file saving — behind a single command.

HOW: Uses argparse to accept an input file, language/diarization options,
context file paths (script, terms, default-terms), output format selection,
and output directory. Runs the async pipeline via asyncio.run(). Status
messages go to stderr; output files are saved next to the source (or to
--output-dir). Soniox resources are cleaned up after completion.

RULES:
- Positional argument: input audio/video file path
- Validates file extension against SONIOX_SUPPORTED_FORMATS before any API call
- Context files: --script, --terms (repeatable), --default-terms; auto-discovers
  companion files ({stem}-script.txt, {stem}-terms.txt) when not explicitly given
- --formats: comma-separated formatter keys (default: all registered)
- Output naming: {stem}{suffix}, numeric suffix for conflicts (-transcript-2.json)
- Status output goes to stderr (not stdout)
- Always cleans up Soniox file and transcription after processing
- Python 3.9.6 compatible — no match/case, no X | Y unions, no slots=True
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import List, Optional

from soniox_converter.api.client import SonioxClient
from soniox_converter.config import (
    DEFAULT_DIARIZATION,
    DEFAULT_PRIMARY_LANGUAGE,
    DEFAULT_SECONDARY_LANGUAGE,
    SONIOX_SUPPORTED_FORMATS,
)
from soniox_converter.core.assembler import assemble_tokens, filter_translation_tokens
from soniox_converter.core.context import (
    build_context,
    load_default_terms,
    load_script,
    load_terms,
    resolve_companion_files,
)
from soniox_converter.core.ir import (
    AssembledWord,
    Segment,
    SpeakerInfo,
    Transcript,
)
from soniox_converter.formatters import FORMATTERS
from soniox_converter.formatters.base import FormatterOutput


def _status(msg: str) -> None:
    """Print a status message to stderr.

    WHY: Status output must not pollute stdout so the CLI can be piped.

    HOW: Writes to sys.stderr with a flush to ensure immediate display.

    RULES:
    - All status messages go to stderr
    - Always flush after writing
    """
    print(msg, file=sys.stderr, flush=True)


def _build_transcript(
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
    segments: List[Segment] = []
    current_speaker: Optional[str] = words[0].speaker
    current_words: List[AssembledWord] = [words[0]]

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
    seen_speakers: dict = {}
    speaker_list: List[SpeakerInfo] = []
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
    lang_counts: Counter = Counter()
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
    lang_counts: Counter = Counter()
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


def _resolve_output_path(
    stem: str,
    suffix: str,
    output_dir: Path,
) -> Path:
    """Resolve the output file path, adding numeric suffix on conflict.

    WHY: Users may run the converter multiple times on the same file.
    Overwriting previous output would lose work. Numeric suffixes
    (-transcript-2.json) prevent data loss.

    HOW: Check if {stem}{suffix} exists. If so, increment a counter
    and insert it before the file extension until a free name is found.

    RULES:
    - First attempt: {stem}{suffix} (e.g. interview-transcript.json)
    - Conflict: split suffix at last dot, insert counter before extension
      (e.g. interview-transcript-2.json)
    - Counter starts at 2 and increments

    Args:
        stem: Source filename stem (without extension).
        suffix: Formatter's suffix (e.g. "-transcript.json").
        output_dir: Directory to save the output file.

    Returns:
        A Path that does not yet exist.
    """
    base_path = output_dir / "{}{}".format(stem, suffix)
    if not base_path.exists():
        return base_path

    # Split suffix into name part and extension
    # e.g. "-transcript.json" → ("-transcript", ".json")
    dot_idx = suffix.rfind(".")
    if dot_idx > 0:
        suffix_name = suffix[:dot_idx]
        suffix_ext = suffix[dot_idx:]
    else:
        suffix_name = suffix
        suffix_ext = ""

    counter = 2
    while True:
        candidate = output_dir / "{}{}-{}{}".format(stem, suffix_name, counter, suffix_ext)
        if not candidate.exists():
            return candidate
        counter += 1


def _save_output(
    output: FormatterOutput,
    stem: str,
    output_dir: Path,
) -> Path:
    """Save a single formatter output to disk.

    WHY: Each formatter produces one or more FormatterOutput objects.
    This function handles the file I/O, path resolution, and conflict
    avoidance for each output file.

    HOW: Resolves a conflict-free path, then writes content as text
    (UTF-8) or bytes depending on the content type.

    RULES:
    - String content written as UTF-8 text
    - Bytes content written in binary mode
    - Returns the resolved output path for status reporting

    Args:
        output: The FormatterOutput to save.
        stem: Source filename stem.
        output_dir: Directory to save into.

    Returns:
        The Path where the file was saved.
    """
    path = _resolve_output_path(stem, output.suffix, output_dir)

    if isinstance(output.content, bytes):
        path.write_bytes(output.content)
    else:
        path.write_text(output.content, encoding="utf-8")

    return path


def _load_context(
    audio_path: Path,
    script_path: Optional[str],
    terms_paths: Optional[List[str]],
    default_terms_path: Optional[str],
) -> tuple:
    """Load context files for Soniox transcription.

    WHY: Context (script, terms) improves transcription accuracy. The CLI
    supports both explicit paths and auto-discovery of companion files.

    HOW: If explicit paths are given, use those. Otherwise, auto-discover
    companion files next to the audio file. Merge per-file terms with
    project-wide default terms. Build the context dict.

    RULES:
    - Explicit --script overrides auto-discovered {stem}-script.txt
    - Explicit --terms overrides auto-discovered {stem}-terms.txt
    - --default-terms overrides auto-discovered default-terms.txt
    - If --default-terms not given, looks for default-terms.txt in CWD
    - Terms from all sources are merged (deduplicated)

    Args:
        audio_path: Path to the source audio/video file.
        script_path: Explicit path to a script file, or None.
        terms_paths: Explicit paths to terms files, or None.
        default_terms_path: Explicit path to default-terms file, or None.

    Returns:
        Tuple of (script_text, all_terms) ready for build_context().
    """
    script_text: Optional[str] = None
    all_terms: List[str] = []

    # Auto-discover companion files
    companion = resolve_companion_files(audio_path)

    # Script: explicit flag or auto-discovered
    if script_path:
        script_text = load_script(script_path)
        _status("  Script: {} (explicit)".format(script_path))
    elif companion.script_path:
        script_text = load_script(companion.script_path)
        _status("  Script: {} (auto-discovered)".format(companion.script_path))

    # Terms: explicit flag(s) or auto-discovered
    if terms_paths:
        for tp in terms_paths:
            all_terms.extend(load_terms(tp))
            _status("  Terms: {} (explicit)".format(tp))
    elif companion.terms_path:
        all_terms.extend(load_terms(companion.terms_path))
        _status("  Terms: {} (auto-discovered)".format(companion.terms_path))

    # Default terms: explicit flag, auto-discovered next to audio, or CWD
    if default_terms_path:
        dt = load_terms(default_terms_path)
        all_terms.extend(dt)
        if dt:
            _status("  Default terms: {} ({} terms)".format(default_terms_path, len(dt)))
    else:
        # Check companion's default-terms first, then CWD
        if companion.default_terms_path:
            dt = load_terms(companion.default_terms_path)
            all_terms.extend(dt)
            if dt:
                _status("  Default terms: {} ({} terms, auto-discovered)".format(
                    companion.default_terms_path, len(dt)
                ))
        else:
            dt = load_default_terms(Path.cwd())
            all_terms.extend(dt)
            if dt:
                _status("  Default terms: default-terms.txt ({} terms, from CWD)".format(len(dt)))

    # Deduplicate terms while preserving order
    seen: set = set()
    unique_terms: List[str] = []
    for term in all_terms:
        if term not in seen:
            seen.add(term)
            unique_terms.append(term)

    return script_text, unique_terms if unique_terms else None


async def _run_pipeline(args: argparse.Namespace) -> None:
    """Execute the full transcription pipeline.

    WHY: This is the async core of the CLI — it orchestrates all steps
    from file upload through formatting and saving.

    HOW: Sequentially calls SonioxClient methods, then assembles tokens,
    builds the Transcript IR, runs selected formatters, and saves output
    files. Always cleans up Soniox resources in a finally block.

    RULES:
    - Validate file extension before any API call
    - Build language_hints from primary + optional secondary language
    - Status messages to stderr at each step
    - Clean up (delete file + transcription) even on error
    - Save each formatter's output files with conflict avoidance
    """
    input_path = Path(args.input_file).resolve()

    # Validate file exists
    if not input_path.is_file():
        print("Error: File not found: {}".format(input_path), file=sys.stderr)
        sys.exit(1)

    # Validate file extension
    ext = input_path.suffix.lower()
    if ext not in SONIOX_SUPPORTED_FORMATS:
        sorted_formats = sorted(SONIOX_SUPPORTED_FORMATS)
        print(
            "Error: Unsupported file type '{}'. Supported formats: {}".format(
                ext, ", ".join(sorted_formats)
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine output directory
    output_dir = Path(args.output_dir).resolve() if args.output_dir else input_path.parent
    if not output_dir.is_dir():
        print("Error: Output directory does not exist: {}".format(output_dir), file=sys.stderr)
        sys.exit(1)

    # Determine which formatters to run
    if args.formats:
        format_keys = [f.strip() for f in args.formats.split(",")]
        for key in format_keys:
            if key not in FORMATTERS:
                available = ", ".join(sorted(FORMATTERS.keys()))
                print(
                    "Error: Unknown format '{}'. Available formats: {}".format(key, available),
                    file=sys.stderr,
                )
                sys.exit(1)
    else:
        format_keys = list(FORMATTERS.keys())

    # Build language hints
    language_hints: List[str] = [args.language]
    if args.secondary_language:
        language_hints.append(args.secondary_language)

    # Load context files
    _status("Loading context files...")
    script_text, terms = _load_context(
        input_path,
        args.script,
        args.terms if args.terms else None,
        args.default_terms,
    )

    # Build and validate context
    context = build_context(script_text=script_text, terms=terms)
    if context:
        _status("  Context loaded ({} sections)".format(len(context)))

    # Source filename stem (strip all extensions)
    stem = input_path.stem

    file_id: Optional[str] = None
    transcription_id: Optional[str] = None

    try:
        async with SonioxClient() as client:
            # Step 1: Upload
            file_id = await client.upload_file(input_path, on_status=_status)

            # Step 2: Create transcription
            transcription_id = await client.create_transcription(
                file_id=file_id,
                language_hints=language_hints,
                enable_diarization=args.diarization,
                enable_language_identification=True,
                script_text=script_text,
                terms=terms,
                on_status=_status,
            )

            # Step 3: Poll until complete
            await client.poll_until_complete(transcription_id, on_status=_status)

            # Step 4: Fetch transcript tokens
            tokens = await client.fetch_transcript(transcription_id, on_status=_status)

            # Step 5: Assemble tokens into words
            _status("Assembling tokens...")
            token_dicts = [
                {
                    "text": t.text,
                    "start_ms": t.start_ms,
                    "end_ms": t.end_ms,
                    "confidence": t.confidence,
                    "speaker": t.speaker,
                    "language": t.language,
                    "translation_status": t.translation_status,
                }
                for t in tokens
            ]
            filtered = filter_translation_tokens(token_dicts)
            words = assemble_tokens(filtered)
            _status("  Assembled {} words".format(len(words)))

            # Step 6: Build Transcript IR
            transcript = _build_transcript(words, input_path.name)
            _status("  {} segments, {} speakers, primary language: {}".format(
                len(transcript.segments),
                len(transcript.speakers),
                transcript.primary_language,
            ))

            # Step 7: Run formatters and save output
            _status("Formatting output...")
            saved_files: List[Path] = []
            for key in format_keys:
                formatter = FORMATTERS[key]()
                _status("  Running {} formatter...".format(formatter.name))
                outputs = formatter.format(transcript)
                for output in outputs:
                    saved_path = _save_output(output, stem, output_dir)
                    saved_files.append(saved_path)
                    _status("  Saved: {}".format(saved_path.name))

            # Step 8: Cleanup
            await client.cleanup(transcription_id, file_id, on_status=_status)

            # Summary
            _status("")
            _status("Done! Saved {} file(s) to {}".format(len(saved_files), output_dir))
            for f in saved_files:
                _status("  {}".format(f.name))

    except KeyboardInterrupt:
        _status("\nCancelled by user.")
        # Still try to clean up
        if file_id and transcription_id:
            try:
                async with SonioxClient() as cleanup_client:
                    await cleanup_client.cleanup(transcription_id, file_id)
            except Exception:
                pass
        sys.exit(130)
    except ValueError as e:
        # Config errors (missing API key, context too large, etc.)
        print("Error: {}".format(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("Error: {}".format(e), file=sys.stderr)
        # Try to clean up
        if file_id and transcription_id:
            try:
                async with SonioxClient() as cleanup_client:
                    await cleanup_client.cleanup(transcription_id, file_id)
            except Exception:
                pass
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the CLI.

    WHY: Separating parser construction from main() makes the CLI
    testable — tests can inspect the parser without running the pipeline.

    HOW: Creates an ArgumentParser with all flags described in the PRD.

    RULES:
    - Positional: input_file (required)
    - Optional: --language, --secondary-language, --diarization/--no-diarization
    - Optional: --formats (comma-separated), --output-dir
    - Optional: --script, --terms (repeatable), --default-terms
    """
    parser = argparse.ArgumentParser(
        prog="soniox_converter",
        description="Transcribe audio/video files using Soniox ASR and produce "
                    "multiple output formats (Premiere Pro JSON, SRT, plain text, etc.).",
    )

    parser.add_argument(
        "input_file",
        help="Path to the audio or video file to transcribe.",
    )

    parser.add_argument(
        "--language",
        default=DEFAULT_PRIMARY_LANGUAGE,
        help="Primary language ISO 639-1 code (default: %(default)s).",
    )

    parser.add_argument(
        "--secondary-language",
        default=None,
        help="Secondary language ISO 639-1 code for code-switching detection.",
    )

    parser.add_argument(
        "--diarization",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_DIARIZATION,
        help="Enable speaker diarization (default: %(default)s).",
    )

    parser.add_argument(
        "--formats",
        default=None,
        help="Comma-separated list of output formats. "
             "Available: {}. Default: all.".format(", ".join(sorted(FORMATTERS.keys()))),
    )

    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to save output files (default: same as input file).",
    )

    parser.add_argument(
        "--script",
        default=None,
        help="Path to a reference script file (improves transcription accuracy).",
    )

    parser.add_argument(
        "--terms",
        action="append",
        default=None,
        help="Path to a terms file (one term per line). Can be specified multiple times.",
    )

    parser.add_argument(
        "--default-terms",
        default=None,
        help="Path to a project-wide default terms file. "
             "Defaults to 'default-terms.txt' in CWD if it exists.",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    """Entry point for the CLI.

    WHY: This is the function that __main__.py calls and that users
    invoke via ``python -m soniox_converter``.

    HOW: Parses arguments, then runs the async pipeline.

    RULES:
    - argv=None means use sys.argv (normal CLI invocation)
    - Explicit argv is for testing
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    asyncio.run(_run_pipeline(args))


if __name__ == "__main__":
    main()
