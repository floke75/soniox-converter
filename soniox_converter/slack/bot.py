"""Slack bot: Socket Mode event handlers, Block Kit actions, and polling loop.

WHY: Users in Slack need to upload audio/video files and get transcriptions
delivered back in-thread. This module is the glue between Slack events and
the local FastAPI transcription API — it listens for file uploads, presents
a configuration form, submits jobs, polls for completion, and delivers
output files.

HOW: Uses slack-bolt with Socket Mode (no public URL needed). Event
handlers react to file_shared events, action handlers process Block Kit
form submissions. After submission, an async polling loop tracks job
progress and edits the Slack message with status updates. On completion
the bot uploads output files to the thread.

RULES:
- All Slack actions must be ack()'d within 3 seconds
- Heavy work runs after ack() in a background thread
- Uses httpx for HTTP calls to the local FastAPI API
- Uses files_upload_v2 (v1 is deprecated)
- Bot only watches for files in SLACK_CHANNEL_ID (if configured)
- Python 3.9+ compatible (no match/case, no PEP 604 unions)
- Runnable as: python -m soniox_converter.slack.bot
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

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
    is_supported_file,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONVERTER_API_URL = os.getenv("CONVERTER_API_URL", "http://localhost:8000")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

# Polling configuration
POLL_INTERVAL_S = 3.0
POLL_TIMEOUT_S = 1200.0  # 20 minutes max

# Terminal job statuses
_TERMINAL_STATUSES = frozenset({"completed", "failed"})


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


def create_app(bot_token: Optional[str] = None) -> App:
    """Create and configure the Slack Bolt app with all handlers.

    WHY: Factory function allows tests to inject a custom bot_token
    and avoids module-level side effects.

    HOW: Creates an App instance, registers event and action handlers,
    and returns the configured app.

    RULES:
    - If bot_token is None, reads from SLACK_BOT_TOKEN env var
    - All handlers are registered before returning
    """
    token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")

    app = App(token=token)

    # Register handlers
    app.event("file_shared")(handle_file_shared)
    app.action(ACTION_PRIMARY_LANG)(handle_language_select)
    app.action(ACTION_SECONDARY_LANG)(handle_language_select)
    app.action(ACTION_DIARIZATION)(handle_diarization_toggle)
    app.action(ACTION_FORMATS)(handle_formats_select)
    app.action(ACTION_TRANSCRIBE)(handle_transcribe_submit)

    return app


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def handle_file_shared(event: Dict[str, Any], client: Any, logger: Any) -> None:
    """Handle file_shared events — post Block Kit form for supported files.

    WHY: When a user uploads an audio/video file, the bot should offer
    a transcription configuration form in the thread.

    HOW: Fetches file info from Slack API, checks if the file type is
    supported, and posts the Block Kit form in-thread if so.

    RULES:
    - Only react to files with supported audio/video extensions
    - If SLACK_CHANNEL_ID is set, only watch that channel
    - Post form as a threaded reply to the file message
    """
    file_id = event.get("file_id", "")
    channel_id = event.get("channel_id", "")

    # Channel filter: if configured, only watch one channel
    if SLACK_CHANNEL_ID and channel_id != SLACK_CHANNEL_ID:
        return

    # Fetch file metadata from Slack
    try:
        file_info_resp = client.files_info(file=file_id)
    except Exception:
        logger.exception("Failed to fetch file info for %s", file_id)
        return

    file_data = file_info_resp.get("file", {})
    filename = file_data.get("name", "")

    if not is_supported_file(filename):
        return

    # Determine the thread timestamp — reply in the file's message thread
    # file_shared events include the message timestamp in different places
    thread_ts = (
        event.get("event_ts")
        or file_data.get("timestamp")
        or event.get("ts")
    )

    # Build and post the form
    blocks = build_transcription_form(filename, file_id)

    try:
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=str(thread_ts) if thread_ts else None,
            blocks=blocks,
            text="Configure transcription for {}".format(filename),
        )
    except Exception:
        logger.exception("Failed to post transcription form for %s", filename)


# ---------------------------------------------------------------------------
# Action handlers (Block Kit interactions)
# ---------------------------------------------------------------------------


def handle_language_select(ack: Any, body: Any, logger: Any) -> None:
    """Acknowledge language dropdown selection (no-op beyond ack).

    WHY: Slack requires all interactive element actions to be acknowledged
    within 3 seconds, even if no processing is needed.
    """
    ack()


def handle_diarization_toggle(ack: Any, body: Any, logger: Any) -> None:
    """Acknowledge diarization checkbox toggle (no-op beyond ack)."""
    ack()


def handle_formats_select(ack: Any, body: Any, logger: Any) -> None:
    """Acknowledge format checkbox selection (no-op beyond ack)."""
    ack()


def handle_transcribe_submit(ack: Any, body: Any, client: Any, logger: Any) -> None:
    """Handle the Transcribe button click — start transcription pipeline.

    WHY: When the user clicks Transcribe, we need to collect all form
    values, download the file from Slack, submit it to the local API,
    and start polling for progress.

    HOW: Ack immediately, then spawn a background thread for the heavy
    work (download + API call + polling + file upload).

    RULES:
    - ack() FIRST, before any processing
    - All heavy work in a background thread
    - Extract form values from body["state"]["values"]
    - Download file using Slack's url_private with bot token auth
    """
    ack()

    # Extract info from the action body
    file_id = ""
    actions = body.get("actions", [])
    for action in actions:
        if action.get("action_id") == ACTION_TRANSCRIBE:
            file_id = action.get("value", "")
            break

    channel = body.get("channel", {}).get("id", "")
    thread_ts = body.get("message", {}).get("thread_ts") or body.get("message", {}).get("ts", "")
    message_ts = body.get("message", {}).get("ts", "")

    # Parse form state
    config = _extract_form_config(body)

    # Run the pipeline in a background thread
    t = threading.Thread(
        target=_run_transcription_pipeline,
        args=(client, file_id, channel, thread_ts, message_ts, config),
        daemon=True,
    )
    t.start()


def _extract_form_config(body: Dict[str, Any]) -> Dict[str, Any]:
    """Extract transcription configuration from Block Kit form state.

    WHY: The form values are nested deep in the Slack action body
    under state.values. This helper centralizes the parsing logic.

    HOW: Walks the state.values dict looking for known action_ids
    and extracts the selected values.

    RULES:
    - Falls back to smart defaults if values are missing
    - secondary_language "none" → None
    """
    state_values = body.get("state", {}).get("values", {})

    primary_language = "sv"
    secondary_language = "en"  # type: Optional[str]
    diarization = True
    output_formats = ["premiere_pro", "srt_captions"]

    for block_values in state_values.values():
        for action_id, action_data in block_values.items():
            if action_id == ACTION_PRIMARY_LANG:
                selected = action_data.get("selected_option", {})
                if selected:
                    primary_language = selected.get("value", "sv")

            elif action_id == ACTION_SECONDARY_LANG:
                selected = action_data.get("selected_option", {})
                if selected:
                    val = selected.get("value", "en")
                    secondary_language = None if val == "none" else val

            elif action_id == ACTION_DIARIZATION:
                selected = action_data.get("selected_options", [])
                diarization = len(selected) > 0

            elif action_id == ACTION_FORMATS:
                selected = action_data.get("selected_options", [])
                if selected:
                    output_formats = [
                        opt.get("value", "") for opt in selected
                        if opt.get("value")
                    ]

    return {
        "primary_language": primary_language,
        "secondary_language": secondary_language,
        "diarization": diarization,
        "output_formats": output_formats,
    }


# ---------------------------------------------------------------------------
# Background transcription pipeline
# ---------------------------------------------------------------------------


def _run_transcription_pipeline(
    client: Any,
    file_id: str,
    channel: str,
    thread_ts: str,
    message_ts: str,
    config: Dict[str, Any],
) -> None:
    """Run the full transcription pipeline in a background thread.

    WHY: The Slack bot must ack actions within 3 seconds. The actual
    work (download, API call, polling, upload) takes minutes.

    HOW: Downloads the file from Slack, POSTs it to the local API,
    polls for status updates (editing the Slack message), and uploads
    output files on completion.

    RULES:
    - Updates the original Slack message with progress
    - On error, posts an error message in the thread
    - Uses httpx for API calls (async is not needed in a thread)
    """
    start_time = time.time()

    try:
        # 1. Get file info from Slack
        file_info_resp = client.files_info(file=file_id)
        file_data = file_info_resp.get("file", {})
        filename = file_data.get("name", "unknown")
        url_private = file_data.get("url_private", "")

        if not url_private:
            _post_error(client, channel, thread_ts, filename, "Could not get file download URL")
            return

        # 2. Download file from Slack CDN
        _update_progress(client, channel, message_ts, filename, "uploading", start_time)

        bot_token = client.token
        with httpx.Client(timeout=120.0) as http:
            dl_resp = http.get(
                url_private,
                headers={"Authorization": "Bearer {}".format(bot_token)},
            )
            dl_resp.raise_for_status()
            file_bytes = dl_resp.content

        # 3. Submit to the local transcription API
        api_url = CONVERTER_API_URL
        form_data = {
            "primary_language": config.get("primary_language", "sv"),
            "diarization": str(config.get("diarization", True)).lower(),
        }

        secondary = config.get("secondary_language")
        if secondary:
            form_data["secondary_language"] = secondary

        formats = config.get("output_formats", [])
        if formats:
            form_data["output_formats"] = ",".join(formats)

        with httpx.Client(timeout=120.0) as http:
            submit_resp = http.post(
                "{}/transcriptions".format(api_url),
                data=form_data,
                files={"file": (filename, file_bytes)},
            )
            submit_resp.raise_for_status()
            job_data = submit_resp.json()

        job_id = job_data.get("id", "")
        if not job_id:
            _post_error(client, channel, thread_ts, filename, "API did not return a job ID")
            return

        # 4. Poll for completion
        _poll_and_update(
            client=client,
            channel=channel,
            message_ts=message_ts,
            thread_ts=thread_ts,
            filename=filename,
            job_id=job_id,
            api_url=api_url,
            start_time=start_time,
        )

    except Exception as exc:
        logger.exception("Transcription pipeline error")
        elapsed = time.time() - start_time
        _post_error(
            client, channel, thread_ts,
            "file", "Unexpected error: {}".format(str(exc)),
        )


def _poll_and_update(
    client: Any,
    channel: str,
    message_ts: str,
    thread_ts: str,
    filename: str,
    job_id: str,
    api_url: str,
    start_time: float,
) -> None:
    """Poll the API for job status and update the Slack message.

    WHY: Transcription jobs take minutes. The user needs to see progress.

    HOW: Polls GET /transcriptions/{job_id} every POLL_INTERVAL_S seconds.
    Edits the original Slack message with the current status. On completion,
    downloads and uploads output files.

    RULES:
    - Polls until terminal status (completed/failed) or timeout
    - Updates Slack message on each status change
    - On completion, uploads files and posts summary
    - On failure, posts error message
    """
    last_status = ""

    with httpx.Client(timeout=30.0) as http:
        while True:
            elapsed = time.time() - start_time
            if elapsed > POLL_TIMEOUT_S:
                _post_error(
                    client, channel, thread_ts, filename,
                    "Transcription timed out after {}s".format(int(elapsed)),
                )
                return

            try:
                resp = http.get("{}/transcriptions/{}".format(api_url, job_id))
                resp.raise_for_status()
                job = resp.json()
            except Exception:
                logger.exception("Failed to poll job %s", job_id)
                time.sleep(POLL_INTERVAL_S)
                continue

            status = job.get("status", "")

            # Update progress message on status change
            if status != last_status:
                last_status = status
                if status not in _TERMINAL_STATUSES:
                    _update_progress(client, channel, message_ts, filename, status, start_time)

            if status == "completed":
                _handle_completion(
                    client=client,
                    http=http,
                    channel=channel,
                    message_ts=message_ts,
                    thread_ts=thread_ts,
                    filename=filename,
                    job_id=job_id,
                    job=job,
                    api_url=api_url,
                    start_time=start_time,
                )
                return

            if status == "failed":
                error_msg = job.get("error", "Unknown error")
                _update_message_with_blocks(
                    client, channel, message_ts,
                    build_error_blocks(filename, error_msg),
                    "Transcription failed: {}".format(filename),
                )
                return

            time.sleep(POLL_INTERVAL_S)


def _handle_completion(
    client: Any,
    http: httpx.Client,
    channel: str,
    message_ts: str,
    thread_ts: str,
    filename: str,
    job_id: str,
    job: Dict[str, Any],
    api_url: str,
    start_time: float,
) -> None:
    """Handle a completed transcription — upload files and post summary.

    WHY: On completion, the user expects output files delivered to the
    thread and a summary of what was produced.

    HOW: Downloads each output file from the API, uploads to Slack via
    files_upload_v2, and edits the original message with a summary.
    """
    elapsed = time.time() - start_time
    output_files = job.get("output_files", [])

    # Upload each output file to the Slack thread
    for out_filename in output_files:
        try:
            file_resp = http.get(
                "{}/transcriptions/{}/files/{}".format(api_url, job_id, out_filename)
            )
            file_resp.raise_for_status()

            client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                content=file_resp.content,
                filename=out_filename,
                title=out_filename,
            )
        except Exception:
            logger.exception("Failed to upload output file %s", out_filename)

    # Update original message with summary
    config = job.get("config", {})
    summary_blocks = build_summary_blocks(
        filename=filename,
        elapsed_s=elapsed,
        output_files=output_files,
    )

    _update_message_with_blocks(
        client, channel, message_ts,
        summary_blocks,
        "Transcription complete: {}".format(filename),
    )


# ---------------------------------------------------------------------------
# Slack message helpers
# ---------------------------------------------------------------------------


def _update_progress(
    client: Any,
    channel: str,
    message_ts: str,
    filename: str,
    status: str,
    start_time: float,
) -> None:
    """Edit the original Slack message with a progress update."""
    elapsed = time.time() - start_time
    blocks = build_progress_blocks(filename, status, elapsed)
    _update_message_with_blocks(
        client, channel, message_ts,
        blocks,
        "Processing {}...".format(filename),
    )


def _update_message_with_blocks(
    client: Any,
    channel: str,
    message_ts: str,
    blocks: List[Dict[str, Any]],
    text: str,
) -> None:
    """Edit a Slack message with new blocks."""
    try:
        client.chat_update(
            channel=channel,
            ts=message_ts,
            blocks=blocks,
            text=text,
        )
    except Exception:
        logger.exception("Failed to update message %s", message_ts)


def _post_error(
    client: Any,
    channel: str,
    thread_ts: str,
    filename: str,
    error: str,
) -> None:
    """Post an error message in the thread."""
    blocks = build_error_blocks(filename, error)
    try:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            blocks=blocks,
            text="Transcription failed: {}".format(filename),
        )
    except Exception:
        logger.exception("Failed to post error message")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the Slack bot in Socket Mode.

    WHY: The bot needs to be runnable as a standalone process via
    python -m soniox_converter.slack.bot.

    HOW: Creates the app and starts the Socket Mode handler, which
    maintains a WebSocket connection to Slack.

    RULES:
    - Requires SLACK_BOT_TOKEN and SLACK_APP_TOKEN environment variables
    - Blocks on the SocketModeHandler.start() call
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    app_token = os.environ.get("SLACK_APP_TOKEN", "")

    if not bot_token:
        raise ValueError("SLACK_BOT_TOKEN environment variable is required")
    if not app_token:
        raise ValueError("SLACK_APP_TOKEN environment variable is required")

    app = create_app(bot_token=bot_token)

    logger.info("Starting Slack bot in Socket Mode...")
    logger.info("Converter API URL: %s", CONVERTER_API_URL)
    if SLACK_CHANNEL_ID:
        logger.info("Watching channel: %s", SLACK_CHANNEL_ID)
    else:
        logger.info("Watching all channels the bot is in")

    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()
