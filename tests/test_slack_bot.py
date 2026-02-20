"""Tests for the Slack bot event handlers, Block Kit forms, and formatters.

WHY: Validates that the Slack bot correctly handles file_shared events,
Block Kit form submissions, progress updates, error handling, and message
formatting. All Slack API calls and HTTP API calls are mocked.

HOW: Uses unittest.mock to mock the Slack WebClient and httpx HTTP calls.
Tests exercise event handlers, action handlers, form config extraction,
message builders, and the polling loop.

RULES:
- Slack WebClient is always mocked (no real Slack API calls)
- HTTP API calls are mocked (no real FastAPI server needed)
- Each test is independent
- Tests cover: event handling, form submission, progress, errors, formatting
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, call

import pytest

from soniox_converter.slack.messages import (
    ACTION_DIARIZATION,
    ACTION_FORMATS,
    ACTION_MODAL_DIARIZATION,
    ACTION_MODAL_FORMATS,
    ACTION_MODAL_GENERAL_CONTEXT,
    ACTION_MODAL_PRIMARY_LANG,
    ACTION_MODAL_SECONDARY_LANG,
    ACTION_MODAL_TERMS,
    ACTION_OPEN_MODAL,
    ACTION_PRIMARY_LANG,
    ACTION_SECONDARY_LANG,
    ACTION_TRANSCRIBE,
    MODAL_CALLBACK_ID,
    build_error_blocks,
    build_open_modal_message,
    build_progress_blocks,
    build_summary_blocks,
    build_transcription_form,
    build_transcription_modal,
    format_progress,
    is_supported_file,
    _format_elapsed,
)
from soniox_converter.slack.bot import (
    _extract_form_config,
    _extract_modal_config,
    _thread_scripts,
    _track_script_file,
    _cleanup_stale_scripts,
    handle_file_shared,
    handle_language_select,
    handle_diarization_toggle,
    handle_formats_select,
    handle_open_modal,
    handle_modal_submit,
    handle_transcribe_submit,
    _poll_and_update,
    _update_progress,
    _post_error,
)


# ---------------------------------------------------------------------------
# Tests: is_supported_file
# ---------------------------------------------------------------------------


class TestIsSupportedFile:
    """Tests for the file type validation helper."""

    def test_supported_mp3(self):
        assert is_supported_file("interview.mp3") is True

    def test_supported_wav(self):
        assert is_supported_file("audio.wav") is True

    def test_supported_mp4(self):
        assert is_supported_file("video.mp4") is True

    def test_supported_flac(self):
        assert is_supported_file("recording.flac") is True

    def test_supported_m4a(self):
        assert is_supported_file("podcast.m4a") is True

    def test_unsupported_pdf(self):
        assert is_supported_file("document.pdf") is False

    def test_unsupported_txt(self):
        assert is_supported_file("notes.txt") is False

    def test_unsupported_jpg(self):
        assert is_supported_file("photo.jpg") is False

    def test_no_extension(self):
        assert is_supported_file("noext") is False

    def test_case_insensitive(self):
        assert is_supported_file("AUDIO.MP3") is True
        assert is_supported_file("Video.WAV") is True


# ---------------------------------------------------------------------------
# Tests: _format_elapsed
# ---------------------------------------------------------------------------


class TestFormatElapsed:
    """Tests for the elapsed time formatter."""

    def test_seconds_only(self):
        assert _format_elapsed(45) == "45s"

    def test_zero_seconds(self):
        assert _format_elapsed(0) == "0s"

    def test_minutes_and_seconds(self):
        assert _format_elapsed(135) == "2m 15s"

    def test_exact_minute(self):
        assert _format_elapsed(60) == "1m 0s"

    def test_hours(self):
        assert _format_elapsed(3723) == "1h 2m 3s"

    def test_fractional_seconds(self):
        assert _format_elapsed(45.7) == "45s"


# ---------------------------------------------------------------------------
# Tests: format_progress
# ---------------------------------------------------------------------------


class TestFormatProgress:
    """Tests for progress status formatting."""

    def test_pending_status(self):
        result = format_progress("pending", 10.0)
        assert "Queued" in result
        assert "10s" in result

    def test_uploading_status(self):
        result = format_progress("uploading", 30.0)
        assert "Uploading" in result

    def test_transcribing_status(self):
        result = format_progress("transcribing", 135.0)
        assert "Transcribing" in result
        assert "2m 15s" in result

    def test_converting_status(self):
        result = format_progress("converting", 200.0)
        assert "Converting" in result

    def test_unknown_status(self):
        result = format_progress("some_other", 10.0)
        assert "Processing" in result


# ---------------------------------------------------------------------------
# Tests: Block Kit form builder
# ---------------------------------------------------------------------------


class TestBuildTranscriptionForm:
    """Tests for the Block Kit transcription form."""

    def test_returns_list_of_blocks(self):
        blocks = build_transcription_form("test.mp3", "F12345")
        assert isinstance(blocks, list)
        assert len(blocks) > 0

    def test_header_contains_filename(self):
        blocks = build_transcription_form("interview.wav", "F12345")
        header = blocks[0]
        assert header["type"] == "header"
        assert "interview.wav" in header["text"]["text"]

    def test_primary_language_default_swedish(self):
        blocks = build_transcription_form("test.mp3", "F12345")
        # Find the primary language section
        lang_section = blocks[1]
        accessory = lang_section["accessory"]
        assert accessory["action_id"] == ACTION_PRIMARY_LANG
        assert accessory["initial_option"]["value"] == "sv"

    def test_secondary_language_default_english(self):
        blocks = build_transcription_form("test.mp3", "F12345")
        lang_section = blocks[2]
        accessory = lang_section["accessory"]
        assert accessory["action_id"] == ACTION_SECONDARY_LANG
        assert accessory["initial_option"]["value"] == "en"

    def test_diarization_checked_by_default(self):
        blocks = build_transcription_form("test.mp3", "F12345")
        diarization_section = blocks[3]
        accessory = diarization_section["accessory"]
        assert accessory["action_id"] == ACTION_DIARIZATION
        assert len(accessory["initial_options"]) == 1
        assert accessory["initial_options"][0]["value"] == "enabled"

    def test_format_checkboxes_have_defaults(self):
        blocks = build_transcription_form("test.mp3", "F12345")
        # Find the actions block with format checkboxes
        format_actions = blocks[5]
        checkboxes = format_actions["elements"][0]
        assert checkboxes["action_id"] == ACTION_FORMATS
        initial_values = {opt["value"] for opt in checkboxes["initial_options"]}
        assert "premiere_pro" in initial_values
        assert "srt_captions" in initial_values

    def test_transcribe_button_has_file_id(self):
        blocks = build_transcription_form("test.mp3", "F_ABC123")
        # Last block is the button actions
        button_block = blocks[-1]
        button = button_block["elements"][0]
        assert button["action_id"] == ACTION_TRANSCRIBE
        assert button["value"] == "F_ABC123"

    def test_transcribe_button_is_primary_style(self):
        blocks = build_transcription_form("test.mp3", "F12345")
        button_block = blocks[-1]
        button = button_block["elements"][0]
        assert button["style"] == "primary"


# ---------------------------------------------------------------------------
# Tests: Progress and summary blocks
# ---------------------------------------------------------------------------


class TestBuildProgressBlocks:
    """Tests for progress update message blocks."""

    def test_returns_blocks(self):
        blocks = build_progress_blocks("test.mp3", "transcribing", 60.0)
        assert isinstance(blocks, list)
        assert len(blocks) > 0

    def test_contains_filename(self):
        blocks = build_progress_blocks("interview.wav", "uploading", 10.0)
        text = blocks[0]["text"]["text"]
        assert "interview.wav" in text

    def test_contains_elapsed(self):
        blocks = build_progress_blocks("test.mp3", "transcribing", 135.0)
        text = blocks[0]["text"]["text"]
        assert "2m 15s" in text


class TestBuildSummaryBlocks:
    """Tests for completion summary blocks."""

    def test_basic_summary(self):
        blocks = build_summary_blocks(
            filename="test.mp3",
            elapsed_s=222.0,
        )
        assert isinstance(blocks, list)
        assert len(blocks) >= 2

    def test_contains_filename(self):
        blocks = build_summary_blocks(filename="test.mp3", elapsed_s=100.0)
        header_text = blocks[0]["text"]["text"]
        assert "test.mp3" in header_text

    def test_contains_processing_time(self):
        blocks = build_summary_blocks(filename="test.mp3", elapsed_s=222.0)
        stats_text = blocks[1]["text"]["text"]
        assert "3m 42s" in stats_text

    def test_includes_speakers_when_provided(self):
        blocks = build_summary_blocks(
            filename="test.mp3", elapsed_s=100.0, speakers=3,
        )
        stats_text = blocks[1]["text"]["text"]
        assert "Speakers detected: 3" in stats_text

    def test_includes_word_count_when_provided(self):
        blocks = build_summary_blocks(
            filename="test.mp3", elapsed_s=100.0, word_count=1500,
        )
        stats_text = blocks[1]["text"]["text"]
        assert "1,500" in stats_text

    def test_includes_duration_when_provided(self):
        blocks = build_summary_blocks(
            filename="test.mp3", elapsed_s=100.0, duration_s=3600.0,
        )
        stats_text = blocks[1]["text"]["text"]
        assert "1h 0m 0s" in stats_text

    def test_includes_output_files(self):
        blocks = build_summary_blocks(
            filename="test.mp3",
            elapsed_s=100.0,
            output_files=["test-transcript.json", "test.srt"],
        )
        assert len(blocks) == 3
        files_text = blocks[2]["text"]["text"]
        assert "test-transcript.json" in files_text
        assert "test.srt" in files_text

    def test_no_output_files_block_when_empty(self):
        blocks = build_summary_blocks(filename="test.mp3", elapsed_s=100.0)
        assert len(blocks) == 2


class TestBuildErrorBlocks:
    """Tests for error message blocks."""

    def test_contains_filename(self):
        blocks = build_error_blocks("test.mp3", "Something went wrong")
        text = blocks[0]["text"]["text"]
        assert "test.mp3" in text

    def test_contains_error_message(self):
        blocks = build_error_blocks("test.mp3", "API timeout")
        text = blocks[0]["text"]["text"]
        assert "API timeout" in text


# ---------------------------------------------------------------------------
# Tests: Event handler — file_shared
# ---------------------------------------------------------------------------


class TestHandleFileShared:
    """Tests for the file_shared event handler."""

    def _make_event(self, file_id="F12345", channel_id="C12345", event_ts="1234567890.123"):
        return {
            "file_id": file_id,
            "channel_id": channel_id,
            "event_ts": event_ts,
        }

    def test_posts_form_for_supported_file(self):
        client = MagicMock()
        client.files_info.return_value = {
            "file": {
                "name": "interview.mp3",
                "url_private": "https://files.slack.com/xyz",
            }
        }
        mock_logger = MagicMock()
        event = self._make_event()

        handle_file_shared(event, client, mock_logger)

        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args[1]
        assert call_kwargs["channel"] == "C12345"
        assert call_kwargs["thread_ts"] == "1234567890.123"
        assert isinstance(call_kwargs["blocks"], list)

    def test_ignores_unsupported_file(self):
        client = MagicMock()
        client.files_info.return_value = {
            "file": {"name": "document.pdf"}
        }
        mock_logger = MagicMock()
        event = self._make_event()

        handle_file_shared(event, client, mock_logger)

        client.chat_postMessage.assert_not_called()

    @patch("soniox_converter.slack.bot.SLACK_CHANNEL_ID", "C99999")
    def test_ignores_wrong_channel(self):
        client = MagicMock()
        mock_logger = MagicMock()
        event = self._make_event(channel_id="C12345")

        handle_file_shared(event, client, mock_logger)

        client.files_info.assert_not_called()
        client.chat_postMessage.assert_not_called()

    @patch("soniox_converter.slack.bot.SLACK_CHANNEL_ID", "C12345")
    def test_processes_matching_channel(self):
        client = MagicMock()
        client.files_info.return_value = {
            "file": {"name": "audio.wav"}
        }
        mock_logger = MagicMock()
        event = self._make_event(channel_id="C12345")

        handle_file_shared(event, client, mock_logger)

        client.files_info.assert_called_once()
        client.chat_postMessage.assert_called_once()

    def test_handles_files_info_failure(self):
        client = MagicMock()
        client.files_info.side_effect = Exception("Slack API error")
        mock_logger = MagicMock()
        event = self._make_event()

        # Should not raise
        handle_file_shared(event, client, mock_logger)

        client.chat_postMessage.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Action handlers
# ---------------------------------------------------------------------------


class TestActionHandlers:
    """Tests for no-op action handlers that just ack."""

    def test_language_select_acks(self):
        ack = MagicMock()
        handle_language_select(ack, {}, MagicMock())
        ack.assert_called_once()

    def test_diarization_toggle_acks(self):
        ack = MagicMock()
        handle_diarization_toggle(ack, {}, MagicMock())
        ack.assert_called_once()

    def test_formats_select_acks(self):
        ack = MagicMock()
        handle_formats_select(ack, {}, MagicMock())
        ack.assert_called_once()


class TestHandleTranscribeSubmit:
    """Tests for the Transcribe button handler."""

    def test_acks_immediately(self):
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()

        body = {
            "actions": [{"action_id": ACTION_TRANSCRIBE, "value": "F123"}],
            "channel": {"id": "C123"},
            "message": {"ts": "111.222", "thread_ts": "111.000"},
            "state": {"values": {}},
        }

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            handle_transcribe_submit(ack, body, client, mock_logger)

        ack.assert_called_once()

    def test_starts_background_thread(self):
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()

        body = {
            "actions": [{"action_id": ACTION_TRANSCRIBE, "value": "F123"}],
            "channel": {"id": "C123"},
            "message": {"ts": "111.222", "thread_ts": "111.000"},
            "state": {"values": {}},
        }

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            mock_thread = MagicMock()
            mock_threading.Thread.return_value = mock_thread

            handle_transcribe_submit(ack, body, client, mock_logger)

            mock_threading.Thread.assert_called_once()
            mock_thread.start.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Form config extraction
# ---------------------------------------------------------------------------


class TestExtractFormConfig:
    """Tests for extracting form values from the Slack action body."""

    def test_defaults_when_empty_state(self):
        body = {"state": {"values": {}}}
        config = _extract_form_config(body)
        assert config["primary_language"] == "sv"
        assert config["secondary_language"] == "en"
        assert config["diarization"] is True
        assert config["output_formats"] == ["premiere_pro", "srt_captions"]

    def test_extracts_primary_language(self):
        body = {
            "state": {
                "values": {
                    "block1": {
                        ACTION_PRIMARY_LANG: {
                            "selected_option": {"value": "en"},
                        }
                    }
                }
            }
        }
        config = _extract_form_config(body)
        assert config["primary_language"] == "en"

    def test_extracts_secondary_language_none(self):
        body = {
            "state": {
                "values": {
                    "block1": {
                        ACTION_SECONDARY_LANG: {
                            "selected_option": {"value": "none"},
                        }
                    }
                }
            }
        }
        config = _extract_form_config(body)
        assert config["secondary_language"] is None

    def test_extracts_secondary_language(self):
        body = {
            "state": {
                "values": {
                    "block1": {
                        ACTION_SECONDARY_LANG: {
                            "selected_option": {"value": "de"},
                        }
                    }
                }
            }
        }
        config = _extract_form_config(body)
        assert config["secondary_language"] == "de"

    def test_extracts_diarization_enabled(self):
        body = {
            "state": {
                "values": {
                    "block1": {
                        ACTION_DIARIZATION: {
                            "selected_options": [{"value": "enabled"}],
                        }
                    }
                }
            }
        }
        config = _extract_form_config(body)
        assert config["diarization"] is True

    def test_extracts_diarization_disabled(self):
        body = {
            "state": {
                "values": {
                    "block1": {
                        ACTION_DIARIZATION: {
                            "selected_options": [],
                        }
                    }
                }
            }
        }
        config = _extract_form_config(body)
        assert config["diarization"] is False

    def test_extracts_output_formats(self):
        body = {
            "state": {
                "values": {
                    "block1": {
                        ACTION_FORMATS: {
                            "selected_options": [
                                {"value": "plain_text"},
                                {"value": "kinetic_words"},
                            ],
                        }
                    }
                }
            }
        }
        config = _extract_form_config(body)
        assert config["output_formats"] == ["plain_text", "kinetic_words"]

    def test_full_form_extraction(self):
        body = {
            "state": {
                "values": {
                    "block_lang": {
                        ACTION_PRIMARY_LANG: {
                            "selected_option": {"value": "de"},
                        },
                        ACTION_SECONDARY_LANG: {
                            "selected_option": {"value": "fr"},
                        },
                    },
                    "block_diar": {
                        ACTION_DIARIZATION: {
                            "selected_options": [],
                        },
                    },
                    "block_fmt": {
                        ACTION_FORMATS: {
                            "selected_options": [
                                {"value": "premiere_pro"},
                            ],
                        },
                    },
                }
            }
        }
        config = _extract_form_config(body)
        assert config["primary_language"] == "de"
        assert config["secondary_language"] == "fr"
        assert config["diarization"] is False
        assert config["output_formats"] == ["premiere_pro"]


# ---------------------------------------------------------------------------
# Tests: Poll and update
# ---------------------------------------------------------------------------


class TestPollAndUpdate:
    """Tests for the polling loop."""

    def test_updates_on_status_change(self):
        """Polling should update the Slack message when status changes."""
        client = MagicMock()

        # Mock httpx responses: first transcribing, then completed
        mock_responses = [
            MagicMock(
                json=MagicMock(return_value={
                    "status": "transcribing",
                    "output_files": [],
                    "config": {},
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "status": "completed",
                    "output_files": ["test.srt"],
                    "config": {},
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        # Mock the file download for completion handling
        file_response = MagicMock(
            content=b"fake srt content",
            raise_for_status=MagicMock(),
        )

        with patch("soniox_converter.slack.bot.POLL_INTERVAL_S", 0.01), \
             patch("soniox_converter.slack.bot.time") as mock_time, \
             patch("soniox_converter.slack.bot.httpx") as mock_httpx:

            mock_time.time.return_value = 100.0
            mock_time.sleep = MagicMock()

            mock_http_client = MagicMock()
            mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
            mock_http_client.__exit__ = MagicMock(return_value=False)

            # get() returns poll responses then file download
            mock_http_client.get.side_effect = mock_responses + [file_response]

            mock_httpx.Client.return_value = mock_http_client

            _poll_and_update(
                client=client,
                channel="C123",
                message_ts="111.222",
                thread_ts="111.000",
                filename="test.mp3",
                job_id="job123",
                api_url="http://localhost:8000",
                start_time=100.0,
            )

        # Should have updated the message at least once (for progress)
        assert client.chat_update.call_count >= 1

    def test_posts_error_on_failure(self):
        """Polling should post error when job fails."""
        client = MagicMock()

        mock_response = MagicMock(
            json=MagicMock(return_value={
                "status": "failed",
                "error": "Soniox API error",
                "output_files": [],
                "config": {},
            }),
            raise_for_status=MagicMock(),
        )

        with patch("soniox_converter.slack.bot.time") as mock_time, \
             patch("soniox_converter.slack.bot.httpx") as mock_httpx:

            mock_time.time.return_value = 100.0
            mock_time.sleep = MagicMock()

            mock_http_client = MagicMock()
            mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
            mock_http_client.__exit__ = MagicMock(return_value=False)
            mock_http_client.get.return_value = mock_response

            mock_httpx.Client.return_value = mock_http_client

            _poll_and_update(
                client=client,
                channel="C123",
                message_ts="111.222",
                thread_ts="111.000",
                filename="test.mp3",
                job_id="job123",
                api_url="http://localhost:8000",
                start_time=100.0,
            )

        # Should have updated the message with error blocks
        client.chat_update.assert_called_once()
        update_kwargs = client.chat_update.call_args[1]
        blocks = update_kwargs["blocks"]
        block_text = blocks[0]["text"]["text"]
        assert "failed" in block_text.lower()
        assert "Soniox API error" in block_text

    def test_timeout_posts_error(self):
        """Polling should post timeout error if exceeded."""
        client = MagicMock()

        mock_response = MagicMock(
            json=MagicMock(return_value={
                "status": "transcribing",
                "output_files": [],
                "config": {},
            }),
            raise_for_status=MagicMock(),
        )

        with patch("soniox_converter.slack.bot.POLL_TIMEOUT_S", 0.0), \
             patch("soniox_converter.slack.bot.time") as mock_time, \
             patch("soniox_converter.slack.bot.httpx") as mock_httpx:

            # time.time() returns a value that exceeds the timeout
            mock_time.time.return_value = 100.0

            mock_http_client = MagicMock()
            mock_http_client.__enter__ = MagicMock(return_value=mock_http_client)
            mock_http_client.__exit__ = MagicMock(return_value=False)
            mock_http_client.get.return_value = mock_response

            mock_httpx.Client.return_value = mock_http_client

            _poll_and_update(
                client=client,
                channel="C123",
                message_ts="111.222",
                thread_ts="111.000",
                filename="test.mp3",
                job_id="job123",
                api_url="http://localhost:8000",
                start_time=0.0,  # start_time far in the past
            )

        # Should have posted an error about timeout
        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args[1]
        assert "timed out" in call_kwargs["text"].lower() or any(
            "timed out" in str(b).lower() for b in call_kwargs.get("blocks", [])
        )


# ---------------------------------------------------------------------------
# Tests: Helper functions
# ---------------------------------------------------------------------------


class TestUpdateProgress:
    """Tests for the progress update helper."""

    def test_calls_chat_update(self):
        client = MagicMock()
        with patch("soniox_converter.slack.bot.time") as mock_time:
            mock_time.time.return_value = 110.0
            _update_progress(client, "C123", "111.222", "test.mp3", "transcribing", 100.0)

        client.chat_update.assert_called_once()
        call_kwargs = client.chat_update.call_args[1]
        assert call_kwargs["channel"] == "C123"
        assert call_kwargs["ts"] == "111.222"


class TestPostError:
    """Tests for the error posting helper."""

    def test_posts_error_in_thread(self):
        client = MagicMock()
        _post_error(client, "C123", "111.000", "test.mp3", "Something broke")

        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args[1]
        assert call_kwargs["channel"] == "C123"
        assert call_kwargs["thread_ts"] == "111.000"

    def test_handles_post_failure_gracefully(self):
        client = MagicMock()
        client.chat_postMessage.side_effect = Exception("Slack error")

        # Should not raise
        _post_error(client, "C123", "111.000", "test.mp3", "Error")


# ---------------------------------------------------------------------------
# Tests: build_open_modal_message
# ---------------------------------------------------------------------------


class TestBuildOpenModalMessage:
    """Tests for the compact in-channel button message."""

    def test_returns_list_of_blocks(self):
        blocks = build_open_modal_message("test.mp3", "F12345")
        assert isinstance(blocks, list)
        assert len(blocks) == 2

    def test_contains_filename(self):
        blocks = build_open_modal_message("interview.wav", "F12345")
        section = blocks[0]
        assert section["type"] == "section"
        assert "interview.wav" in section["text"]["text"]

    def test_button_has_correct_action_id(self):
        blocks = build_open_modal_message("test.mp3", "F12345")
        actions_block = blocks[1]
        button = actions_block["elements"][0]
        assert button["action_id"] == ACTION_OPEN_MODAL
        assert button["type"] == "button"
        assert button["style"] == "primary"

    def test_button_carries_file_id(self):
        blocks = build_open_modal_message("test.mp3", "F_ABC123")
        button = blocks[1]["elements"][0]
        assert button["value"] == "F_ABC123"

    def test_button_text_is_swedish(self):
        blocks = build_open_modal_message("test.mp3", "F12345")
        button = blocks[1]["elements"][0]
        assert button["text"]["text"] == "Transkribera"


# ---------------------------------------------------------------------------
# Tests: build_transcription_modal
# ---------------------------------------------------------------------------


class TestBuildTranscriptionModal:
    """Tests for the Slack modal view builder."""

    def test_returns_modal_view(self):
        view = build_transcription_modal("test.mp3", "F12345", "C123", "111.000")
        assert view["type"] == "modal"
        assert view["callback_id"] == MODAL_CALLBACK_ID

    def test_title_is_transkribera(self):
        view = build_transcription_modal("test.mp3", "F12345", "C123", "111.000")
        assert view["title"]["text"] == "Transkribera"
        assert view["submit"]["text"] == "Transkribera"
        assert view["close"]["text"] == "Avbryt"

    def test_private_metadata_round_trip(self):
        import json
        view = build_transcription_modal(
            "test.mp3", "F12345", "C0AG614UEJE", "1234567890.123456",
        )
        metadata = json.loads(view["private_metadata"])
        assert metadata["file_id"] == "F12345"
        assert metadata["channel"] == "C0AG614UEJE"
        assert metadata["thread_ts"] == "1234567890.123456"
        assert metadata["script_file_id"] is None
        assert metadata["script_filename"] is None

    def test_private_metadata_with_script(self):
        import json
        script_info = {"file_id": "F67890", "filename": "prompter.txt", "size": 500}
        view = build_transcription_modal(
            "test.mp3", "F12345", "C123", "111.000",
            script_info=script_info,
        )
        metadata = json.loads(view["private_metadata"])
        assert metadata["script_file_id"] == "F67890"
        assert metadata["script_filename"] == "prompter.txt"

    def test_has_file_section(self):
        view = build_transcription_modal("interview.wav", "F12345", "C123", "111.000")
        blocks = view["blocks"]
        # First header should be "Fil"
        assert blocks[0]["type"] == "header"
        assert blocks[0]["text"]["text"] == "Fil"
        # Second block shows filename
        assert blocks[1]["type"] == "section"
        assert "interview.wav" in blocks[1]["text"]["text"]

    def test_has_language_inputs(self):
        view = build_transcription_modal("test.mp3", "F12345", "C123", "111.000")
        blocks = view["blocks"]
        # Find primary language input
        primary_block = None
        secondary_block = None
        for b in blocks:
            if b.get("block_id") == "primary_language":
                primary_block = b
            elif b.get("block_id") == "secondary_language":
                secondary_block = b
        assert primary_block is not None
        assert primary_block["type"] == "input"
        assert primary_block["element"]["type"] == "static_select"
        assert primary_block["element"]["initial_option"]["value"] == "sv"

        assert secondary_block is not None
        assert secondary_block["type"] == "input"
        assert secondary_block["optional"] is True
        assert secondary_block["element"]["initial_option"]["value"] == "en"

    def test_has_diarization_input(self):
        view = build_transcription_modal("test.mp3", "F12345", "C123", "111.000")
        blocks = view["blocks"]
        diar_block = None
        for b in blocks:
            if b.get("block_id") == "diarization":
                diar_block = b
                break
        assert diar_block is not None
        assert diar_block["type"] == "input"
        assert diar_block["optional"] is True
        element = diar_block["element"]
        assert element["type"] == "checkboxes"
        # Diarization should be pre-checked
        assert len(element["initial_options"]) == 1
        assert element["initial_options"][0]["value"] == "enabled"

    def test_has_output_formats_input(self):
        view = build_transcription_modal("test.mp3", "F12345", "C123", "111.000")
        blocks = view["blocks"]
        fmt_block = None
        for b in blocks:
            if b.get("block_id") == "output_formats":
                fmt_block = b
                break
        assert fmt_block is not None
        assert fmt_block["type"] == "input"
        element = fmt_block["element"]
        assert element["type"] == "multi_static_select"
        initial_values = {opt["value"] for opt in element["initial_options"]}
        assert "premiere_pro" in initial_values
        assert "srt_captions" in initial_values

    def test_has_terms_input(self):
        view = build_transcription_modal("test.mp3", "F12345", "C123", "111.000")
        blocks = view["blocks"]
        terms_block = None
        for b in blocks:
            if b.get("block_id") == "terms":
                terms_block = b
                break
        assert terms_block is not None
        assert terms_block["type"] == "input"
        assert terms_block["optional"] is True
        assert terms_block["element"]["type"] == "plain_text_input"
        assert terms_block["element"]["action_id"] == ACTION_MODAL_TERMS

    def test_has_general_context_input(self):
        view = build_transcription_modal("test.mp3", "F12345", "C123", "111.000")
        blocks = view["blocks"]
        ctx_block = None
        for b in blocks:
            if b.get("block_id") == "general_context":
                ctx_block = b
                break
        assert ctx_block is not None
        assert ctx_block["type"] == "input"
        assert ctx_block["optional"] is True
        assert ctx_block["element"]["type"] == "plain_text_input"
        assert ctx_block["element"]["multiline"] is True
        assert ctx_block["element"]["action_id"] == ACTION_MODAL_GENERAL_CONTEXT

    def test_no_script_context_block_without_script(self):
        view = build_transcription_modal("test.mp3", "F12345", "C123", "111.000")
        blocks = view["blocks"]
        context_blocks = [b for b in blocks if b.get("type") == "context"]
        assert len(context_blocks) == 0

    def test_script_context_block_when_script_provided(self):
        script_info = {"file_id": "F67890", "filename": "manus.txt", "size": 1234}
        view = build_transcription_modal(
            "test.mp3", "F12345", "C123", "111.000",
            script_info=script_info,
        )
        blocks = view["blocks"]
        context_blocks = [b for b in blocks if b.get("type") == "context"]
        assert len(context_blocks) == 1
        text = context_blocks[0]["elements"][0]["text"]
        assert "manus.txt" in text
        assert "1,234" in text

    def test_swedish_labels_throughout(self):
        view = build_transcription_modal("test.mp3", "F12345", "C123", "111.000")
        blocks = view["blocks"]
        headers = [b["text"]["text"] for b in blocks if b.get("type") == "header"]
        assert "Fil" in headers
        assert "Sprak" in headers
        assert "Installningar" in headers
        assert "Utdataformat" in headers
        assert "Kontext" in headers


# ---------------------------------------------------------------------------
# Tests: handle_open_modal
# ---------------------------------------------------------------------------


class TestHandleOpenModal:
    """Tests for the modal open handler."""

    def test_acks_immediately(self):
        ack = MagicMock()
        client = MagicMock()
        client.files_info.return_value = {"file": {"name": "test.mp3"}}
        mock_logger = MagicMock()

        body = {
            "trigger_id": "TRIGGER123",
            "actions": [{"action_id": ACTION_OPEN_MODAL, "value": "F12345"}],
            "channel": {"id": "C123"},
            "message": {"ts": "111.222", "thread_ts": "111.000"},
        }

        handle_open_modal(ack, body, client, mock_logger)
        ack.assert_called_once()

    def test_opens_modal_with_trigger_id(self):
        ack = MagicMock()
        client = MagicMock()
        client.files_info.return_value = {"file": {"name": "test.mp3"}}
        mock_logger = MagicMock()

        body = {
            "trigger_id": "TRIGGER123",
            "actions": [{"action_id": ACTION_OPEN_MODAL, "value": "F12345"}],
            "channel": {"id": "C123"},
            "message": {"ts": "111.222", "thread_ts": "111.000"},
        }

        handle_open_modal(ack, body, client, mock_logger)

        client.views_open.assert_called_once()
        call_kwargs = client.views_open.call_args[1]
        assert call_kwargs["trigger_id"] == "TRIGGER123"
        assert call_kwargs["view"]["type"] == "modal"
        assert call_kwargs["view"]["callback_id"] == MODAL_CALLBACK_ID

    def test_includes_script_info_when_tracked(self):
        ack = MagicMock()
        client = MagicMock()
        client.files_info.return_value = {"file": {"name": "audio.mp3"}}
        mock_logger = MagicMock()

        # Track a script file for this thread
        _thread_scripts["111.000"] = {
            "file_id": "F_SCRIPT",
            "filename": "prompter.txt",
            "size": 500,
            "ts": time.time(),
        }

        body = {
            "trigger_id": "TRIGGER123",
            "actions": [{"action_id": ACTION_OPEN_MODAL, "value": "F12345"}],
            "channel": {"id": "C123"},
            "message": {"ts": "111.222", "thread_ts": "111.000"},
        }

        try:
            handle_open_modal(ack, body, client, mock_logger)

            call_kwargs = client.views_open.call_args[1]
            import json
            metadata = json.loads(call_kwargs["view"]["private_metadata"])
            assert metadata["script_file_id"] == "F_SCRIPT"
            assert metadata["script_filename"] == "prompter.txt"
        finally:
            _thread_scripts.pop("111.000", None)


# ---------------------------------------------------------------------------
# Tests: handle_modal_submit
# ---------------------------------------------------------------------------


class TestHandleModalSubmit:
    """Tests for the modal submit handler."""

    def _make_view(
        self,
        primary_lang="sv",
        secondary_lang="en",
        diarization=True,
        formats=None,
        terms="",
        general_context="",
        script_file_id=None,
        script_filename=None,
    ):
        import json
        if formats is None:
            formats = ["premiere_pro", "srt_captions"]

        diar_selected = [{"value": "enabled"}] if diarization else []
        format_selected = [
            {"value": f, "text": {"type": "plain_text", "text": f}}
            for f in formats
        ]

        secondary_opt = (
            {"value": secondary_lang, "text": {"type": "plain_text", "text": secondary_lang}}
            if secondary_lang
            else None
        )

        metadata = json.dumps({
            "file_id": "F12345",
            "channel": "C123",
            "thread_ts": "111.000",
            "script_file_id": script_file_id,
            "script_filename": script_filename,
        })

        return {
            "private_metadata": metadata,
            "state": {
                "values": {
                    "primary_language": {
                        ACTION_MODAL_PRIMARY_LANG: {
                            "selected_option": {
                                "value": primary_lang,
                                "text": {"type": "plain_text", "text": primary_lang},
                            },
                        }
                    },
                    "secondary_language": {
                        ACTION_MODAL_SECONDARY_LANG: {
                            "selected_option": secondary_opt,
                        }
                    },
                    "diarization": {
                        ACTION_MODAL_DIARIZATION: {
                            "selected_options": diar_selected,
                        }
                    },
                    "output_formats": {
                        ACTION_MODAL_FORMATS: {
                            "selected_options": format_selected,
                        }
                    },
                    "terms": {
                        ACTION_MODAL_TERMS: {
                            "value": terms if terms else None,
                        }
                    },
                    "general_context": {
                        ACTION_MODAL_GENERAL_CONTEXT: {
                            "value": general_context if general_context else None,
                        }
                    },
                },
            },
        }

    def test_acks_on_valid_submission(self):
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()
        view = self._make_view()
        body = {"view": view}

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            handle_modal_submit(ack, body, client, view, mock_logger)

        ack.assert_called_once_with()

    def test_starts_background_thread(self):
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()
        view = self._make_view()
        body = {"view": view}

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            mock_thread = MagicMock()
            mock_threading.Thread.return_value = mock_thread

            handle_modal_submit(ack, body, client, view, mock_logger)

            mock_threading.Thread.assert_called_once()
            mock_thread.start.assert_called_once()

    def test_extracts_primary_language(self):
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()
        view = self._make_view(primary_lang="de")
        body = {"view": view}

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            handle_modal_submit(ack, body, client, view, mock_logger)

            call_args = mock_threading.Thread.call_args
            config = call_args[1]["args"][4] if "args" in call_args[1] else call_args[0][0] if call_args[0] else None
            if config is None:
                # args passed as positional
                config = call_args[1].get("args", (None, None, None, None, {}))[4]
            assert config["primary_language"] == "de"

    def test_extracts_terms(self):
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()
        view = self._make_view(terms="Melodifestivalen, SVT, EFN")
        body = {"view": view}

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            handle_modal_submit(ack, body, client, view, mock_logger)

            config = mock_threading.Thread.call_args[1]["args"][4]
            assert config["terms"] == ["Melodifestivalen", "SVT", "EFN"]

    def test_extracts_general_context(self):
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()
        view = self._make_view(general_context="doman:Media, amne:Musik")
        body = {"view": view}

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            handle_modal_submit(ack, body, client, view, mock_logger)

            config = mock_threading.Thread.call_args[1]["args"][4]
            assert config["general_context_raw"] == "doman:Media, amne:Musik"

    def test_returns_validation_error_when_context_too_large(self):
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()
        # Create terms string > 10000 chars
        large_terms = ",".join(["term{}".format(i) for i in range(3000)])
        view = self._make_view(terms=large_terms)
        body = {"view": view}

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            handle_modal_submit(ack, body, client, view, mock_logger)

            # ack should be called with response_action="errors"
            ack.assert_called_once()
            call_kwargs = ack.call_args[1]
            assert call_kwargs["response_action"] == "errors"
            assert "terms" in call_kwargs["errors"]
            # No background thread should be started
            mock_threading.Thread.assert_not_called()

    def test_validation_includes_script_file_size(self):
        """Script file size from _thread_scripts should count toward context limit."""
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()

        # Terms + general_context alone are under limit, but script size pushes over
        terms = "a" * 4000
        general = "b" * 4000
        view = self._make_view(
            terms=terms,
            general_context=general,
            script_file_id="F_SCRIPT",
            script_filename="big_script.txt",
        )
        body = {"view": view}

        # Track a script with size that pushes total over 10000
        _thread_scripts["111.000"] = {
            "file_id": "F_SCRIPT",
            "filename": "big_script.txt",
            "size": 3000,
            "ts": time.time(),
        }

        try:
            with patch("soniox_converter.slack.bot.threading") as mock_threading:
                handle_modal_submit(ack, body, client, view, mock_logger)

                # Total = 4000 + 4000 + 3000 = 11000 > 10000
                ack.assert_called_once()
                call_kwargs = ack.call_args[1]
                assert call_kwargs["response_action"] == "errors"
                assert "terms" in call_kwargs["errors"]
                mock_threading.Thread.assert_not_called()
        finally:
            _thread_scripts.pop("111.000", None)

    def test_private_metadata_round_trip(self):
        import json
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()
        view = self._make_view(
            script_file_id="F_SCRIPT",
            script_filename="prompter.txt",
        )
        body = {"view": view}

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            handle_modal_submit(ack, body, client, view, mock_logger)

            call_args = mock_threading.Thread.call_args[1]["args"]
            # args: (client, file_id, channel, thread_ts, config)
            assert call_args[1] == "F12345"  # file_id
            assert call_args[2] == "C123"    # channel
            assert call_args[3] == "111.000" # thread_ts
            config = call_args[4]
            assert config["script_file_id"] == "F_SCRIPT"
            assert config["script_filename"] == "prompter.txt"


# ---------------------------------------------------------------------------
# Tests: _extract_modal_config
# ---------------------------------------------------------------------------


class TestExtractModalConfig:
    """Tests for extracting config from modal state values."""

    def test_defaults_when_empty(self):
        config = _extract_modal_config({})
        assert config["primary_language"] == "sv"
        assert config["secondary_language"] is None
        assert config["diarization"] is False
        assert config["output_formats"] == ["premiere_pro", "srt_captions"]

    def test_extracts_all_values(self):
        values = {
            "primary_language": {
                ACTION_MODAL_PRIMARY_LANG: {
                    "selected_option": {"value": "de"},
                }
            },
            "secondary_language": {
                ACTION_MODAL_SECONDARY_LANG: {
                    "selected_option": {"value": "fr"},
                }
            },
            "diarization": {
                ACTION_MODAL_DIARIZATION: {
                    "selected_options": [{"value": "enabled"}],
                }
            },
            "output_formats": {
                ACTION_MODAL_FORMATS: {
                    "selected_options": [{"value": "plain_text"}],
                }
            },
        }
        config = _extract_modal_config(values)
        assert config["primary_language"] == "de"
        assert config["secondary_language"] == "fr"
        assert config["diarization"] is True
        assert config["output_formats"] == ["plain_text"]

    def test_secondary_language_none(self):
        values = {
            "secondary_language": {
                ACTION_MODAL_SECONDARY_LANG: {
                    "selected_option": {"value": "none"},
                }
            },
        }
        config = _extract_modal_config(values)
        assert config["secondary_language"] is None

    def test_diarization_disabled(self):
        values = {
            "diarization": {
                ACTION_MODAL_DIARIZATION: {
                    "selected_options": [],
                }
            },
        }
        config = _extract_modal_config(values)
        assert config["diarization"] is False


# ---------------------------------------------------------------------------
# Tests: Script file tracking
# ---------------------------------------------------------------------------


class TestScriptFileTracking:
    """Tests for .txt file detection and tracking."""

    def setup_method(self):
        """Clean up thread scripts before each test."""
        _thread_scripts.clear()

    def test_txt_file_tracked(self):
        _track_script_file("111.000", "F_TXT", "prompter.txt", 1234)
        assert "111.000" in _thread_scripts
        info = _thread_scripts["111.000"]
        assert info["file_id"] == "F_TXT"
        assert info["filename"] == "prompter.txt"
        assert info["size"] == 1234

    def test_empty_thread_ts_not_tracked(self):
        _track_script_file("", "F_TXT", "script.txt", 100)
        assert len(_thread_scripts) == 0

    def test_cleanup_removes_stale_entries(self):
        _thread_scripts["old_thread"] = {
            "file_id": "F1",
            "filename": "old.txt",
            "size": 100,
            "ts": time.time() - 7200,  # 2 hours ago
        }
        _thread_scripts["new_thread"] = {
            "file_id": "F2",
            "filename": "new.txt",
            "size": 200,
            "ts": time.time(),
        }
        _cleanup_stale_scripts()
        assert "old_thread" not in _thread_scripts
        assert "new_thread" in _thread_scripts

    def test_handle_file_shared_tracks_txt(self):
        """file_shared for a .txt file should store it, not post a message."""
        client = MagicMock()
        client.files_info.return_value = {
            "file": {"name": "manus.txt", "size": 500}
        }
        mock_logger = MagicMock()
        event = {
            "file_id": "F_TXT",
            "channel_id": "C123",
            "event_ts": "111.000",
        }

        handle_file_shared(event, client, mock_logger)

        # Should NOT post a message
        client.chat_postMessage.assert_not_called()
        # Should track the script
        assert "111.000" in _thread_scripts
        assert _thread_scripts["111.000"]["file_id"] == "F_TXT"

        # Cleanup
        _thread_scripts.clear()

    def test_handle_file_shared_posts_button_for_audio(self):
        """file_shared for audio should post the compact button message."""
        client = MagicMock()
        client.files_info.return_value = {
            "file": {"name": "interview.mp3"}
        }
        mock_logger = MagicMock()
        event = {
            "file_id": "F_MP3",
            "channel_id": "C123",
            "event_ts": "111.000",
        }

        handle_file_shared(event, client, mock_logger)

        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args[1]
        blocks = call_kwargs["blocks"]
        # Should be the open-modal button message, not the old form
        button = blocks[1]["elements"][0]
        assert button["action_id"] == ACTION_OPEN_MODAL


# ---------------------------------------------------------------------------
# Tests: Context parsing in modal submit
# ---------------------------------------------------------------------------


class TestContextParsing:
    """Tests for context field parsing in modal submission."""

    def test_terms_split_correctly(self):
        """Terms should be split by comma and stripped."""
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()

        import json
        view = {
            "private_metadata": json.dumps({
                "file_id": "F1", "channel": "C1", "thread_ts": "1.0",
                "script_file_id": None, "script_filename": None,
            }),
            "state": {
                "values": {
                    "primary_language": {
                        ACTION_MODAL_PRIMARY_LANG: {"selected_option": {"value": "sv"}},
                    },
                    "secondary_language": {
                        ACTION_MODAL_SECONDARY_LANG: {"selected_option": None},
                    },
                    "diarization": {
                        ACTION_MODAL_DIARIZATION: {"selected_options": []},
                    },
                    "output_formats": {
                        ACTION_MODAL_FORMATS: {
                            "selected_options": [{"value": "plain_text"}],
                        },
                    },
                    "terms": {
                        ACTION_MODAL_TERMS: {"value": " SVT , EFN , Melodifestivalen "},
                    },
                    "general_context": {
                        ACTION_MODAL_GENERAL_CONTEXT: {"value": None},
                    },
                },
            },
        }

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            handle_modal_submit(ack, {}, client, view, mock_logger)

            config = mock_threading.Thread.call_args[1]["args"][4]
            assert config["terms"] == ["SVT", "EFN", "Melodifestivalen"]

    def test_general_context_key_value_parsing(self):
        """General context should be stored as raw string for API."""
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()

        import json
        view = {
            "private_metadata": json.dumps({
                "file_id": "F1", "channel": "C1", "thread_ts": "1.0",
                "script_file_id": None, "script_filename": None,
            }),
            "state": {
                "values": {
                    "primary_language": {
                        ACTION_MODAL_PRIMARY_LANG: {"selected_option": {"value": "sv"}},
                    },
                    "secondary_language": {
                        ACTION_MODAL_SECONDARY_LANG: {"selected_option": None},
                    },
                    "diarization": {
                        ACTION_MODAL_DIARIZATION: {"selected_options": []},
                    },
                    "output_formats": {
                        ACTION_MODAL_FORMATS: {
                            "selected_options": [{"value": "plain_text"}],
                        },
                    },
                    "terms": {
                        ACTION_MODAL_TERMS: {"value": None},
                    },
                    "general_context": {
                        ACTION_MODAL_GENERAL_CONTEXT: {
                            "value": "doman:Media, amne:Musik, organisation:SVT",
                        },
                    },
                },
            },
        }

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            handle_modal_submit(ack, {}, client, view, mock_logger)

            config = mock_threading.Thread.call_args[1]["args"][4]
            assert config["general_context_raw"] == "doman:Media, amne:Musik, organisation:SVT"

    def test_empty_terms_becomes_none(self):
        """Empty terms should become None, not empty list."""
        ack = MagicMock()
        client = MagicMock()
        mock_logger = MagicMock()

        import json
        view = {
            "private_metadata": json.dumps({
                "file_id": "F1", "channel": "C1", "thread_ts": "1.0",
                "script_file_id": None, "script_filename": None,
            }),
            "state": {
                "values": {
                    "primary_language": {
                        ACTION_MODAL_PRIMARY_LANG: {"selected_option": {"value": "sv"}},
                    },
                    "secondary_language": {
                        ACTION_MODAL_SECONDARY_LANG: {"selected_option": None},
                    },
                    "diarization": {
                        ACTION_MODAL_DIARIZATION: {"selected_options": []},
                    },
                    "output_formats": {
                        ACTION_MODAL_FORMATS: {
                            "selected_options": [{"value": "plain_text"}],
                        },
                    },
                    "terms": {
                        ACTION_MODAL_TERMS: {"value": ""},
                    },
                    "general_context": {
                        ACTION_MODAL_GENERAL_CONTEXT: {"value": ""},
                    },
                },
            },
        }

        with patch("soniox_converter.slack.bot.threading") as mock_threading:
            handle_modal_submit(ack, {}, client, view, mock_logger)

            config = mock_threading.Thread.call_args[1]["args"][4]
            assert config["terms"] is None
