"""End-to-end integration test with the real Soniox API.

WHY: Unit tests verify individual components in isolation, but only an E2E
test confirms the full pipeline works: upload audio → transcribe with context
→ assemble tokens → format all outputs. This catches integration bugs that
unit tests miss.

HOW: Uses the real Soniox API with a test audio file from test-assets/.
Skipped automatically if SONIOX_API_KEY is not set in the environment.

RULES:
- Marked with pytest.mark.skipif when no API key is available.
- Uses test-assets/SOME_260213_melodifestivalen_lowq.mp4 as input.
- Validates Premiere Pro JSON against the schema.
- Validates SRT output has correct format.
- Cleans up Soniox resources (transcription + file) after test.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

# Check for API key before importing modules that trigger dotenv
_HAS_API_KEY = bool(os.getenv("SONIOX_API_KEY", "").strip())

# Test asset paths
_TEST_ASSETS = Path(__file__).resolve().parent.parent / "test-assets"
_TEST_AUDIO = _TEST_ASSETS / "SOME_260213_melodifestivalen_lowq.mp4"
_TEST_AUDIO_WAV = _TEST_ASSETS / "SOME_260213_melodifestivalen_lowq.mp4.wav"
_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "soniox_converter" / "formatters" / "PremierePro_transcript_format_spec.json"


def _get_test_audio():
    """Return the best available test audio file."""
    if _TEST_AUDIO.is_file():
        return _TEST_AUDIO
    if _TEST_AUDIO_WAV.is_file():
        return _TEST_AUDIO_WAV
    return None


@pytest.mark.skipif(
    not _HAS_API_KEY,
    reason="SONIOX_API_KEY not set in environment — skipping real API test",
)
@pytest.mark.skipif(
    _get_test_audio() is None,
    reason="No test audio file found in test-assets/",
)
class TestRealAPIEndToEnd:
    """Full pipeline test with the real Soniox API."""

    def test_real_api_pipeline(self):
        """Upload → transcribe → assemble → format all outputs."""
        # Lazy imports to avoid import errors when API key is missing
        import jsonschema

        from soniox_converter.api.client import SonioxClient
        from soniox_converter.core.assembler import assemble_tokens, filter_translation_tokens
        from soniox_converter.core.context import (
            build_context,
            load_script,
            load_terms,
            resolve_companion_files,
        )
        from soniox_converter.core.ir import Segment, SpeakerInfo, Transcript

        import uuid as uuid_mod

        audio_path = _get_test_audio()
        assert audio_path is not None

        async def _run():
            # Discover context files
            companion = resolve_companion_files(audio_path)
            script_text = None
            terms = None

            if companion.script_path:
                script_text = load_script(companion.script_path)
            if companion.terms_path:
                terms = load_terms(companion.terms_path)

            context = build_context(script_text=script_text, terms=terms)

            # Build context kwargs for create_transcription
            context_kwargs = {}
            if script_text:
                context_kwargs["script_text"] = script_text
            if terms:
                context_kwargs["terms"] = terms

            file_id = None
            transcription_id = None

            async with SonioxClient() as client:
                try:
                    # Step 1: Upload
                    file_id = await client.upload_file(audio_path)
                    assert file_id, "Upload should return a file_id"

                    # Step 2: Create transcription with context
                    transcription_id = await client.create_transcription(
                        file_id=file_id,
                        language_hints=["sv", "en"],
                        enable_diarization=True,
                        enable_language_identification=True,
                        **context_kwargs,
                    )
                    assert transcription_id, "Create should return a transcription_id"

                    # Step 3: Poll until complete
                    status = await client.poll_until_complete(transcription_id)
                    assert status.status == "completed"

                    # Step 4: Fetch transcript
                    soniox_tokens = await client.fetch_transcript(transcription_id)
                    assert len(soniox_tokens) > 0, "Should have tokens"

                    # Convert SonioxToken objects to dicts for the assembler
                    token_dicts = []
                    for t in soniox_tokens:
                        d = {
                            "text": t.text,
                            "start_ms": t.start_ms,
                            "end_ms": t.end_ms,
                            "confidence": t.confidence,
                        }
                        if t.speaker is not None:
                            d["speaker"] = t.speaker
                        if t.language is not None:
                            d["language"] = t.language
                        if t.translation_status is not None:
                            d["translation_status"] = t.translation_status
                        token_dicts.append(d)

                    # Filter and assemble
                    filtered = filter_translation_tokens(token_dicts)
                    words = assemble_tokens(filtered)
                    assert len(words) > 0, "Should have assembled words"

                    # Build IR
                    speaker_map = {}
                    for w in words:
                        if w.speaker and w.speaker not in speaker_map:
                            speaker_map[w.speaker] = SpeakerInfo(
                                soniox_label=w.speaker,
                                display_name="Speaker {}".format(w.speaker),
                                uuid=str(uuid_mod.uuid4()),
                            )

                    # Build segments by speaker
                    segments = []
                    current_speaker = None
                    current_words = []
                    for w in words:
                        if w.speaker != current_speaker:
                            if current_words:
                                first_w = current_words[0]
                                last_w = current_words[-1]
                                seg_lang = first_w.language or "sv"
                                segments.append(Segment(
                                    speaker=current_speaker,
                                    language=seg_lang,
                                    start_s=first_w.start_s,
                                    duration_s=(last_w.start_s + last_w.duration_s - first_w.start_s),
                                    words=list(current_words),
                                ))
                            current_speaker = w.speaker
                            current_words = [w]
                        else:
                            current_words.append(w)

                    if current_words:
                        first_w = current_words[0]
                        last_w = current_words[-1]
                        seg_lang = first_w.language or "sv"
                        segments.append(Segment(
                            speaker=current_speaker,
                            language=seg_lang,
                            start_s=first_w.start_s,
                            duration_s=(last_w.start_s + last_w.duration_s - first_w.start_s),
                            words=list(current_words),
                        ))

                    # Determine primary language
                    lang_counts = {}
                    for w in words:
                        if w.language:
                            lang_counts[w.language] = lang_counts.get(w.language, 0) + 1
                    primary_lang = max(lang_counts, key=lang_counts.get) if lang_counts else "sv"

                    last_word = words[-1]
                    transcript = Transcript(
                        segments=segments,
                        speakers=list(speaker_map.values()),
                        primary_language=primary_lang,
                        source_filename=audio_path.name,
                        duration_s=last_word.start_s + last_word.duration_s,
                    )

                    # Format with Premiere Pro
                    from soniox_converter.formatters.premiere_pro import PremiereProFormatter
                    pp_formatter = PremiereProFormatter()
                    pp_outputs = pp_formatter.format(transcript)
                    assert len(pp_outputs) == 1

                    # Validate against schema
                    pp_data = json.loads(pp_outputs[0].content)
                    with open(_SCHEMA_PATH) as f:
                        schema = json.load(f)
                    jsonschema.validate(instance=pp_data, schema=schema)

                    # Format with SRT
                    from soniox_converter.formatters.srt_captions import SRTCaptionFormatter
                    srt_formatter = SRTCaptionFormatter()
                    srt_outputs = srt_formatter.format(transcript)
                    assert len(srt_outputs) == 2

                    for srt_output in srt_outputs:
                        assert len(srt_output.content) > 0
                        assert " --> " in srt_output.content

                finally:
                    # Cleanup
                    if transcription_id and file_id:
                        await client.cleanup(transcription_id, file_id)

        asyncio.run(_run())
