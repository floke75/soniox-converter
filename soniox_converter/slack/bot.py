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
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

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

# Script file tracking: thread_ts -> {"file_id": str, "filename": str, "size": int, "ts": float}
_thread_scripts: Dict[str, Dict[str, Any]] = {}

# Max age for tracked scripts (1 hour)
_SCRIPT_MAX_AGE_S = 3600.0


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

    # Modal handlers
    app.action(ACTION_OPEN_MODAL)(handle_open_modal)
    app.view(MODAL_CALLBACK_ID)(handle_modal_submit)

    return app


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def handle_file_shared(event: Dict[str, Any], client: Any, logger: Any) -> None:
    """Handle file_shared events — post button message or track script files.

    WHY: When a user uploads an audio/video file, the bot posts a compact
    message with a "Transkribera" button. Clicking the button opens the
    modal. When a .txt file is uploaded in the same thread, it's tracked
    as a potential script/prompter file for context.

    RULES:
    - Audio/video files → post compact button message in-thread
    - .txt files → store in _thread_scripts for later modal use
    - If SLACK_CHANNEL_ID is set, only watch that channel
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

    # Determine the thread timestamp
    thread_ts = (
        event.get("event_ts")
        or file_data.get("timestamp")
        or event.get("ts")
    )
    thread_ts_str = str(thread_ts) if thread_ts else ""

    # Check if this is a .txt file → track as script
    if filename.lower().endswith(".txt"):
        file_size = file_data.get("size", 0)
        _track_script_file(thread_ts_str, file_id, filename, file_size)
        return

    if not is_supported_file(filename):
        return

    # Clean up stale script entries periodically
    _cleanup_stale_scripts()

    # Build and post the compact button message
    blocks = build_open_modal_message(filename, file_id)

    try:
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts_str if thread_ts_str else None,
            blocks=blocks,
            text="Transkribera {}".format(filename),
        )
    except Exception:
        logger.exception("Failed to post transcription button for %s", filename)


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
# Modal handlers
# ---------------------------------------------------------------------------


def handle_open_modal(ack: Any, body: Any, client: Any, logger: Any) -> None:
    """Handle the Transkribera button click — open the transcription modal.

    WHY: The button click provides a trigger_id which is required to open
    a modal. We use the file_id from the button value and look up any
    tracked script files for the thread.

    RULES:
    - ack() FIRST
    - Use trigger_id from body to open the modal
    """
    ack()

    trigger_id = body.get("trigger_id", "")
    file_id = ""
    actions = body.get("actions", [])
    for action in actions:
        if action.get("action_id") == ACTION_OPEN_MODAL:
            file_id = action.get("value", "")
            break

    channel = body.get("channel", {}).get("id", "")
    message = body.get("message", {})
    thread_ts = message.get("thread_ts") or message.get("ts", "")

    # Get filename from Slack
    filename = "unknown"
    try:
        file_info_resp = client.files_info(file=file_id)
        filename = file_info_resp.get("file", {}).get("name", "unknown")
    except Exception:
        logger.exception("Failed to fetch file info for modal %s", file_id)

    # Look up tracked script file for this thread
    script_info = _thread_scripts.get(thread_ts)

    # Build and open modal
    view = build_transcription_modal(
        filename=filename,
        file_id=file_id,
        channel=channel,
        thread_ts=thread_ts,
        script_info=script_info,
    )

    try:
        client.views_open(trigger_id=trigger_id, view=view)
    except Exception:
        logger.exception("Failed to open transcription modal")


def handle_modal_submit(ack: Any, body: Any, client: Any, view: Any, logger: Any) -> None:
    """Handle modal submission — validate, ack, and start pipeline.

    WHY: When user submits the transcription modal, we extract all form
    values including context fields, validate context size, and spawn
    the transcription pipeline.

    RULES:
    - Validate context size BEFORE ack — return inline errors if exceeded
    - ack() closes the modal on success
    - Heavy work in a background thread after ack
    """
    values = view.get("state", {}).get("values", {})
    metadata = json.loads(view.get("private_metadata", "{}"))

    # Extract form values
    config = _extract_modal_config(values)

    # Parse context fields
    terms_raw = values.get("terms", {}).get(ACTION_MODAL_TERMS, {}).get("value") or ""
    general_raw = values.get("general_context", {}).get(ACTION_MODAL_GENERAL_CONTEXT, {}).get("value") or ""

    terms_list = [t.strip() for t in terms_raw.split(",") if t.strip()] if terms_raw.strip() else None
    general_list = None  # type: Optional[List[Dict[str, str]]]
    if general_raw.strip():
        general_list = []
        for pair in general_raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                key, val = pair.split(":", 1)
                general_list.append({"key": key.strip(), "value": val.strip()})

    # If script file was tracked, we'll download it later in the pipeline
    script_file_id = metadata.get("script_file_id")
    script_filename = metadata.get("script_filename")

    # Validate context size including tracked script file size
    script_size = 0
    if script_file_id:
        thread_ts = metadata.get("thread_ts", "")
        if thread_ts in _thread_scripts:
            script_size = _thread_scripts[thread_ts].get("size", 0)

    total_context_chars = len(terms_raw) + len(general_raw) + script_size
    if total_context_chars > 10000:
        ack(
            response_action="errors",
            errors={"terms": "Kontexten ar for stor ({:,} tecken, max 10 000)".format(total_context_chars)},
        )
        return

    # Validation passed — close modal
    ack()

    file_id = metadata.get("file_id", "")
    channel = metadata.get("channel", "")
    thread_ts = metadata.get("thread_ts", "")

    # Add context info to config
    config["terms"] = terms_list
    config["general_context_raw"] = general_raw
    config["script_file_id"] = script_file_id
    config["script_filename"] = script_filename

    # Run the pipeline in a background thread
    # We don't have a message_ts to update for modal flow — we'll post a new progress message
    t = threading.Thread(
        target=_run_modal_transcription_pipeline,
        args=(client, file_id, channel, thread_ts, config),
        daemon=True,
    )
    t.start()


def _extract_modal_config(values: Dict[str, Any]) -> Dict[str, Any]:
    """Extract transcription config from modal state values.

    WHY: Modal values have a different structure than Block Kit action state.
    Each block_id maps to action_id → value.
    """
    # Primary language
    primary_opt = values.get("primary_language", {}).get(
        ACTION_MODAL_PRIMARY_LANG, {}
    ).get("selected_option")
    primary_language = primary_opt.get("value", "sv") if primary_opt else "sv"

    # Secondary language
    secondary_opt = values.get("secondary_language", {}).get(
        ACTION_MODAL_SECONDARY_LANG, {}
    ).get("selected_option")
    secondary_language = None  # type: Optional[str]
    if secondary_opt:
        val = secondary_opt.get("value", "none")
        secondary_language = None if val == "none" else val

    # Diarization
    diarization_opts = values.get("diarization", {}).get(
        ACTION_MODAL_DIARIZATION, {}
    ).get("selected_options", [])
    diarization = len(diarization_opts) > 0

    # Output formats
    format_opts = values.get("output_formats", {}).get(
        ACTION_MODAL_FORMATS, {}
    ).get("selected_options", [])
    output_formats = [opt.get("value", "") for opt in format_opts if opt.get("value")]
    if not output_formats:
        output_formats = ["premiere_pro", "srt_captions"]

    return {
        "primary_language": primary_language,
        "secondary_language": secondary_language,
        "diarization": diarization,
        "output_formats": output_formats,
    }


# ---------------------------------------------------------------------------
# Script file tracking
# ---------------------------------------------------------------------------


def _track_script_file(
    thread_ts: str, file_id: str, filename: str, size: int
) -> None:
    """Track a .txt file upload as a potential script for context."""
    if not thread_ts:
        return
    _thread_scripts[thread_ts] = {
        "file_id": file_id,
        "filename": filename,
        "size": size,
        "ts": time.time(),
    }


def _cleanup_stale_scripts() -> None:
    """Remove script entries older than _SCRIPT_MAX_AGE_S."""
    now = time.time()
    stale = [
        ts for ts, info in _thread_scripts.items()
        if now - info.get("ts", 0) > _SCRIPT_MAX_AGE_S
    ]
    for ts in stale:
        del _thread_scripts[ts]


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


def _run_modal_transcription_pipeline(
    client: Any,
    file_id: str,
    channel: str,
    thread_ts: str,
    config: Dict[str, Any],
) -> None:
    """Run the transcription pipeline from a modal submission.

    WHY: The modal flow doesn't have a message_ts to edit — we post a new
    progress message in the thread, then edit that message for updates.

    HOW: Posts initial progress message, downloads file + optional script
    from Slack, POSTs to API with context fields, polls for completion.
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

        # 2. Post initial progress message in thread
        progress_blocks = build_progress_blocks(filename, "uploading", 0.0)
        try:
            progress_resp = client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                blocks=progress_blocks,
                text="Processing {}...".format(filename),
            )
            message_ts = progress_resp.get("ts", "")
        except Exception:
            logger.exception("Failed to post progress message")
            message_ts = ""

        # 3. Download file from Slack CDN
        bot_token = client.token
        with httpx.Client(timeout=120.0) as http:
            dl_resp = http.get(
                url_private,
                headers={"Authorization": "Bearer {}".format(bot_token)},
            )
            dl_resp.raise_for_status()
            file_bytes = dl_resp.content

        # 4. Download script file if tracked
        script_bytes = None  # type: Optional[bytes]
        script_file_id = config.get("script_file_id")
        if script_file_id:
            try:
                script_info_resp = client.files_info(file=script_file_id)
                script_url = script_info_resp.get("file", {}).get("url_private", "")
                if script_url:
                    with httpx.Client(timeout=30.0) as http:
                        script_resp = http.get(
                            script_url,
                            headers={"Authorization": "Bearer {}".format(bot_token)},
                        )
                        script_resp.raise_for_status()
                        script_bytes = script_resp.content
            except Exception:
                logger.exception("Failed to download script file %s", script_file_id)
                try:
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text="\u26a0\ufe0f Kunde inte ladda ner manusfilen. Transkriberingen forts\u00e4tter utan manuskontext.",
                    )
                except Exception:
                    pass  # Best-effort warning

        # 5. Submit to the local transcription API
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

        # Context fields
        terms_list = config.get("terms")
        if terms_list:
            form_data["terms"] = ",".join(terms_list)

        general_raw = config.get("general_context_raw", "")
        if general_raw.strip():
            form_data["general_context"] = general_raw

        # Build files dict for multipart upload
        files_dict = {"file": (filename, file_bytes)}  # type: Dict[str, Any]
        if script_bytes:
            script_filename = config.get("script_filename", "script.txt")
            files_dict["context_file"] = (script_filename, script_bytes)

        with httpx.Client(timeout=120.0) as http:
            submit_resp = http.post(
                "{}/transcriptions".format(api_url),
                data=form_data,
                files=files_dict,
            )
            submit_resp.raise_for_status()
            job_data = submit_resp.json()

        job_id = job_data.get("id", "")
        if not job_id:
            _post_error(client, channel, thread_ts, filename, "API did not return a job ID")
            return

        # 6. Poll for completion
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
        logger.exception("Modal transcription pipeline error")
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
