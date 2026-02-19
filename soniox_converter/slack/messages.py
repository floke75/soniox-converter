"""Message templates, Block Kit builders, and formatters for Slack bot.

WHY: The Slack bot needs to send structured messages: a Block Kit form
for transcription options, progress updates during processing, and a
summary when complete. Centralizing these builders keeps bot.py focused
on event/action handling logic.

HOW: Each function returns a list of Block Kit block dicts ready to be
passed to say(blocks=...) or client.chat_update(blocks=...). Progress
and summary formatters take job status data and produce human-readable
Slack messages.

RULES:
- All functions return list[dict] (Block Kit blocks) or str (plain text)
- action_id values must match the handler registrations in bot.py
- Smart defaults: Swedish primary, English secondary, diarization on,
  Premiere Pro + SRT broadcast checked by default
- Python 3.9+ compatible (no match/case, no PEP 604 unions)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Action IDs — must match @app.action() registrations in bot.py
ACTION_TRANSCRIBE = "transcribe_submit"
ACTION_PRIMARY_LANG = "primary_language_select"
ACTION_SECONDARY_LANG = "secondary_language_select"
ACTION_DIARIZATION = "diarization_toggle"
ACTION_FORMATS = "output_formats_select"

# Language options for dropdowns
LANGUAGE_OPTIONS = [
    ("sv", "Swedish"),
    ("en", "English"),
    ("da", "Danish"),
    ("no", "Norwegian"),
    ("fi", "Finnish"),
    ("de", "German"),
    ("fr", "French"),
    ("es", "Spanish"),
    ("nl", "Dutch"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese"),
    ("ar", "Arabic"),
    ("ru", "Russian"),
    ("pl", "Polish"),
    ("tr", "Turkish"),
    ("hi", "Hindi"),
]

# Output format options
FORMAT_OPTIONS = [
    ("premiere_pro", "Premiere Pro JSON"),
    ("srt_captions", "SRT Broadcast (16:9)"),
    ("plain_text", "Plain Text"),
    ("kinetic_words", "Kinetic Words"),
]

# Formats checked by default
DEFAULT_FORMATS = {"premiere_pro", "srt_captions"}

# Audio/video extensions accepted (matches config.SONIOX_SUPPORTED_FORMATS)
SUPPORTED_EXTENSIONS = {
    ".aac", ".aiff", ".amr", ".asf", ".flac",
    ".mp3", ".ogg", ".wav", ".webm", ".m4a", ".mp4",
}


# ---------------------------------------------------------------------------
# Block Kit form builder
# ---------------------------------------------------------------------------


def build_transcription_form(filename: str, file_id: str) -> List[Dict[str, Any]]:
    """Build the Block Kit form for transcription configuration.

    WHY: When a user uploads an audio/video file, the bot replies with
    a form letting them pick language, diarization, and output formats
    before starting transcription.

    HOW: Constructs Block Kit blocks with static_select for languages,
    checkboxes for diarization and formats, and a button to submit.
    The file_id is stored in the button's value for retrieval on submit.

    RULES:
    - Smart defaults: Swedish primary, English secondary, diarization on
    - Default formats: Premiere Pro + SRT broadcast
    - action_id values must match bot.py handler registrations
    """
    # Language dropdown options
    lang_options = [
        {
            "text": {"type": "plain_text", "text": label},
            "value": code,
        }
        for code, label in LANGUAGE_OPTIONS
    ]

    # Secondary language options (includes "None" option)
    secondary_lang_options = [
        {
            "text": {"type": "plain_text", "text": "None"},
            "value": "none",
        }
    ] + lang_options

    # Format checkbox options
    format_options = [
        {
            "text": {"type": "plain_text", "text": label},
            "value": key,
        }
        for key, label in FORMAT_OPTIONS
    ]

    # Pre-selected format options (defaults)
    initial_formats = [
        opt for opt in format_options
        if opt["value"] in DEFAULT_FORMATS
    ]

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Transcribe: {}".format(filename),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Primary Language*",
            },
            "accessory": {
                "type": "static_select",
                "action_id": ACTION_PRIMARY_LANG,
                "placeholder": {"type": "plain_text", "text": "Select language"},
                "options": lang_options,
                "initial_option": {
                    "text": {"type": "plain_text", "text": "Swedish"},
                    "value": "sv",
                },
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Secondary Language* (optional)",
            },
            "accessory": {
                "type": "static_select",
                "action_id": ACTION_SECONDARY_LANG,
                "placeholder": {"type": "plain_text", "text": "Select language"},
                "options": secondary_lang_options,
                "initial_option": {
                    "text": {"type": "plain_text", "text": "English"},
                    "value": "en",
                },
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Speaker Diarization*",
            },
            "accessory": {
                "type": "checkboxes",
                "action_id": ACTION_DIARIZATION,
                "options": [
                    {
                        "text": {"type": "plain_text", "text": "Enable diarization"},
                        "value": "enabled",
                    }
                ],
                "initial_options": [
                    {
                        "text": {"type": "plain_text", "text": "Enable diarization"},
                        "value": "enabled",
                    }
                ],
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Output Formats*",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "checkboxes",
                    "action_id": ACTION_FORMATS,
                    "options": format_options,
                    "initial_options": initial_formats,
                }
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Transcribe"},
                    "style": "primary",
                    "action_id": ACTION_TRANSCRIBE,
                    "value": file_id,
                }
            ],
        },
    ]

    return blocks


# ---------------------------------------------------------------------------
# Progress message builders
# ---------------------------------------------------------------------------


def format_progress(status: str, elapsed_s: float) -> str:
    """Format a progress status line with elapsed time.

    WHY: Users need to see what stage their transcription is at and how
    long it has been running.

    HOW: Maps API job status strings to human-readable messages and
    appends formatted elapsed time.

    RULES:
    - Status strings match JobStatus enum values from server.jobs
    - Elapsed time formatted as Xm Ys
    """
    status_map = {
        "pending": "Queued...",
        "uploading": "Uploading to transcription service...",
        "transcribing": "Transcribing...",
        "converting": "Converting to output formats...",
    }

    message = status_map.get(status, "Processing...")
    elapsed_str = _format_elapsed(elapsed_s)

    return "{} (elapsed: {})".format(message, elapsed_str)


def build_progress_blocks(
    filename: str,
    status: str,
    elapsed_s: float,
) -> List[Dict[str, Any]]:
    """Build Block Kit blocks for a progress update message.

    WHY: The bot edits its original message to show transcription progress.
    Block Kit formatting makes it visually clear.

    HOW: A section block with the filename and current status/elapsed time.
    """
    progress_text = format_progress(status, elapsed_s)

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*{}*\n{}".format(filename, progress_text),
            },
        },
    ]


# ---------------------------------------------------------------------------
# Completion summary builder
# ---------------------------------------------------------------------------


def build_summary_blocks(
    filename: str,
    elapsed_s: float,
    speakers: Optional[int] = None,
    word_count: Optional[int] = None,
    duration_s: Optional[float] = None,
    output_files: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Build Block Kit blocks for the completion summary.

    WHY: When transcription completes, the user sees a summary with
    processing stats and a list of generated files.

    HOW: Constructs a rich summary section with processing time, speaker
    count, word count, and audio duration. Lists output files below.

    RULES:
    - Only include stats that are available (non-None)
    - Processing time is always shown
    """
    elapsed_str = _format_elapsed(elapsed_s)

    stats_lines = [
        "Processing time: {}".format(elapsed_str),
    ]

    if speakers is not None:
        stats_lines.append("Speakers detected: {}".format(speakers))

    if word_count is not None:
        stats_lines.append("Word count: {:,}".format(word_count))

    if duration_s is not None:
        stats_lines.append("Audio duration: {}".format(_format_elapsed(duration_s)))

    stats_text = "\n".join(stats_lines)

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Transcription complete: {}*".format(filename),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": stats_text,
            },
        },
    ]

    if output_files:
        file_list = "\n".join(
            "- {}".format(f) for f in output_files
        )
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Output files:*\n{}".format(file_list),
            },
        })

    return blocks


def build_error_blocks(filename: str, error: str) -> List[Dict[str, Any]]:
    """Build Block Kit blocks for an error message.

    WHY: When transcription fails, the user needs to see what went wrong
    with clear formatting.

    HOW: A section block with error details in a code block.
    """
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Transcription failed: {}*\n```{}```".format(
                    filename, error
                ),
            },
        },
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_elapsed(seconds: float) -> str:
    """Format seconds into a human-readable elapsed time string.

    RULES:
    - Under 60s: "Xs"
    - 60s+: "Xm Ys"
    - Over 1h: "Xh Xm Ys"
    """
    total = int(seconds)
    if total < 60:
        return "{}s".format(total)

    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60

    if hours > 0:
        return "{}h {}m {}s".format(hours, minutes, secs)

    return "{}m {}s".format(minutes, secs)


def is_supported_file(filename: str) -> bool:
    """Check if a filename has a supported audio/video extension.

    WHY: The bot should only react to files it can transcribe.
    Unsupported file types are silently ignored.

    RULES:
    - Extension check is case-insensitive
    - Matches SONIOX_SUPPORTED_FORMATS from config
    """
    dot_idx = filename.rfind(".")
    if dot_idx < 0:
        return False
    ext = filename[dot_idx:].lower()
    return ext in SUPPORTED_EXTENSIONS
