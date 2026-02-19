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
    ACTION_PRIMARY_LANG,
    ACTION_SECONDARY_LANG,
    ACTION_TRANSCRIBE,
    build_error_blocks,
    build_progress_blocks,
    build_summary_blocks,
    build_transcription_form,
    format_progress,
    is_supported_file,
    _format_elapsed,
)
from soniox_converter.slack.bot import (
    _extract_form_config,
    handle_file_shared,
    handle_language_select,
    handle_diarization_toggle,
    handle_formats_select,
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
