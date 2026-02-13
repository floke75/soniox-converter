"""Tkinter desktop GUI for the Soniox Transcript Converter.

WHY: EFN editors need a point-and-click way to transcribe audio/video files
without knowing about APIs, tokens, or CLIs. The GUI wraps the full pipeline
— file selection, Soniox API upload/transcribe/poll/fetch, token assembly,
and pluggable formatter output — behind a single window with Browse buttons,
language pickers, and a Transcribe button.

HOW: A single TranscriberApp class builds the tkinter UI in three logical
states: IDLE (file selection + settings), PROCESSING (status updates +
cancel), and DONE (file list + transcript preview). The async Soniox
pipeline runs in a background thread via asyncio.run() to avoid blocking
the tkinter main loop. Status updates flow from the background thread to
the UI via a thread-safe queue polled by tkinter's .after() mechanism.

RULES:
- Python 3.9.6 compatible — no slots=True, no match/case, no X | Y unions
- All async API work runs in a background thread (never on the main thread)
- Status queue is the ONLY communication channel between threads
- tkinter widgets are ONLY touched from the main thread
- File extension validation happens before any API call
- API key checked on startup; first-launch setup prompt if missing
- Context files (script, terms) auto-discovered and shown in the UI
- Output files saved next to source (or user-chosen directory)
"""

from __future__ import annotations

import asyncio
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

from soniox_converter.config import (
    DEFAULT_DIARIZATION,
    DEFAULT_PRIMARY_LANGUAGE,
    DEFAULT_SECONDARY_LANGUAGE,
    LANGUAGE_MAP,
    SONIOX_SUPPORTED_FORMATS,
    load_api_key,
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WINDOW_TITLE = "Soniox Transcript Converter"
_WINDOW_MIN_WIDTH = 620
_WINDOW_MIN_HEIGHT = 580
_PAD = 8

# Map of format key -> (display label, default checked)
_FORMAT_OPTIONS: List[Tuple[str, str, bool]] = [
    ("premiere_pro", "Premiere Pro JSON", True),
    ("srt_broadcast", "SRT Captions (Broadcast)", True),
    ("srt_social", "SRT Captions (Social)", False),
    ("kinetic_words", "Kinetic Word Reveal", False),
    ("plain_text", "Plain Text", False),
]

# Language display names for dropdown
_LANGUAGE_CHOICES: List[Tuple[str, str]] = [
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

# Status message types
_STATUS_MSG = "status"
_DONE_MSG = "done"
_ERROR_MSG = "error"
_CANCELLED_MSG = "cancelled"


# ---------------------------------------------------------------------------
# Pipeline helpers (reused from cli.py pattern)
# ---------------------------------------------------------------------------

def _build_transcript(
    words: List[AssembledWord],
    source_filename: str,
) -> Transcript:
    """Build a Transcript IR from assembled words.

    WHY: The assembler produces a flat list of AssembledWord objects.
    Formatters expect a Transcript with speaker-grouped segments,
    speaker metadata, and language info.

    HOW: Walks through words and creates a new Segment whenever the
    speaker label changes. Collects unique speakers and assigns UUIDs.

    RULES:
    - New segment whenever speaker changes
    - SpeakerInfo gets a UUID v4 and "Speaker N" display name
    - Primary language is the most frequent language among words
    """
    if not words:
        return Transcript(
            segments=[],
            speakers=[],
            primary_language="",
            source_filename=source_filename,
            duration_s=0.0,
        )

    segments: List[Segment] = []
    current_speaker: Optional[str] = words[0].speaker
    current_words: List[AssembledWord] = [words[0]]

    for word in words[1:]:
        if word.speaker != current_speaker and word.word_type == "word":
            segments.append(_build_segment(current_words, current_speaker))
            current_words = [word]
            current_speaker = word.speaker
        else:
            current_words.append(word)

    if current_words:
        segments.append(_build_segment(current_words, current_speaker))

    seen_speakers: Dict[str, SpeakerInfo] = {}
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

    lang_counts: Counter = Counter()
    for word in words:
        if word.language:
            lang_counts[word.language] += 1
    primary_language = lang_counts.most_common(1)[0][0] if lang_counts else ""

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
    """Build a single Segment from a list of words."""
    first = words[0]
    last_w = words[-1]
    start_s = first.start_s
    duration_s = (last_w.start_s + last_w.duration_s) - start_s

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


def _resolve_output_path(stem: str, suffix: str, output_dir: Path) -> Path:
    """Resolve the output file path, adding numeric suffix on conflict."""
    base_path = output_dir / "{}{}".format(stem, suffix)
    if not base_path.exists():
        return base_path

    dot_idx = suffix.rfind(".")
    if dot_idx > 0:
        suffix_name = suffix[:dot_idx]
        suffix_ext = suffix[dot_idx:]
    else:
        suffix_name = suffix
        suffix_ext = ""

    counter = 2
    while True:
        candidate = output_dir / "{}{}-{}{}".format(
            stem, suffix_name, counter, suffix_ext
        )
        if not candidate.exists():
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# Main GUI Application
# ---------------------------------------------------------------------------

class TranscriberApp:
    """Main tkinter application for the Soniox Transcript Converter.

    WHY: Provides a visual interface so editors can transcribe files
    without using the command line.

    HOW: Builds a single-window UI with three states — idle (settings),
    processing (status), done (results). The async API pipeline runs
    in a background thread; status updates flow through a queue.

    RULES:
    - All tkinter widget access happens on the main thread only
    - Background thread communicates via self._status_queue
    - .after() polls the queue every 100ms during processing
    - Cancel sets self._cancel_event which the pipeline checks
    """

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._root.title(_WINDOW_TITLE)
        self._root.minsize(_WINDOW_MIN_WIDTH, _WINDOW_MIN_HEIGHT)

        # Thread communication
        self._status_queue: queue.Queue = queue.Queue()
        self._cancel_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        # State
        self._input_path: Optional[Path] = None
        self._output_dir: Optional[Path] = None
        self._script_path: Optional[Path] = None
        self._terms_path: Optional[Path] = None
        self._saved_files: List[Path] = []
        self._transcript_preview: str = ""

        # Build UI
        self._build_ui()

        # Check API key on startup
        self._root.after(100, self._check_api_key)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Build the main window layout."""
        # Main container with padding
        main = ttk.Frame(self._root, padding=_PAD)
        main.pack(fill=tk.BOTH, expand=True)

        # --- File Selection ---
        file_frame = ttk.LabelFrame(main, text="Input File", padding=_PAD)
        file_frame.pack(fill=tk.X, pady=(0, _PAD))

        self._file_label = ttk.Label(
            file_frame, text="No file selected", foreground="gray"
        )
        self._file_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._browse_btn = ttk.Button(
            file_frame, text="Browse...", command=self._browse_file
        )
        self._browse_btn.pack(side=tk.RIGHT)

        # --- Context Files ---
        ctx_frame = ttk.LabelFrame(main, text="Context Files (Optional)", padding=_PAD)
        ctx_frame.pack(fill=tk.X, pady=(0, _PAD))

        # Script file
        script_row = ttk.Frame(ctx_frame)
        script_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(script_row, text="Script:").pack(side=tk.LEFT)
        self._script_label = ttk.Label(
            script_row, text="None", foreground="gray"
        )
        self._script_label.pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)
        self._script_browse_btn = ttk.Button(
            script_row, text="Browse...", command=self._browse_script
        )
        self._script_browse_btn.pack(side=tk.RIGHT, padx=(4, 0))
        self._script_clear_btn = ttk.Button(
            script_row, text="Clear", command=self._clear_script
        )
        self._script_clear_btn.pack(side=tk.RIGHT)

        # Terms file
        terms_row = ttk.Frame(ctx_frame)
        terms_row.pack(fill=tk.X)
        ttk.Label(terms_row, text="Terms:").pack(side=tk.LEFT)
        self._terms_label = ttk.Label(
            terms_row, text="None", foreground="gray"
        )
        self._terms_label.pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)
        self._terms_browse_btn = ttk.Button(
            terms_row, text="Browse...", command=self._browse_terms
        )
        self._terms_browse_btn.pack(side=tk.RIGHT, padx=(4, 0))
        self._terms_clear_btn = ttk.Button(
            terms_row, text="Clear", command=self._clear_terms
        )
        self._terms_clear_btn.pack(side=tk.RIGHT)

        # --- Settings ---
        settings_frame = ttk.LabelFrame(main, text="Settings", padding=_PAD)
        settings_frame.pack(fill=tk.X, pady=(0, _PAD))

        # Language row
        lang_row = ttk.Frame(settings_frame)
        lang_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(lang_row, text="Primary language:").pack(side=tk.LEFT)
        self._primary_lang_var = tk.StringVar(value=DEFAULT_PRIMARY_LANGUAGE)
        lang_names = ["{} ({})".format(name, code) for code, name in _LANGUAGE_CHOICES]
        self._primary_lang_combo = ttk.Combobox(
            lang_row,
            textvariable=self._primary_lang_var,
            values=[code for code, _ in _LANGUAGE_CHOICES],
            state="readonly",
            width=6,
        )
        # Set display to the default
        for i, (code, _) in enumerate(_LANGUAGE_CHOICES):
            if code == DEFAULT_PRIMARY_LANGUAGE:
                self._primary_lang_combo.current(i)
                break
        self._primary_lang_combo.pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(lang_row, text="Secondary:").pack(side=tk.LEFT)
        secondary_codes = ["(None)"] + [code for code, _ in _LANGUAGE_CHOICES]
        self._secondary_lang_var = tk.StringVar(value="(None)")
        self._secondary_lang_combo = ttk.Combobox(
            lang_row,
            textvariable=self._secondary_lang_var,
            values=secondary_codes,
            state="readonly",
            width=8,
        )
        # Default secondary to English if configured
        if DEFAULT_SECONDARY_LANGUAGE:
            for i, (code, _) in enumerate(_LANGUAGE_CHOICES):
                if code == DEFAULT_SECONDARY_LANGUAGE:
                    self._secondary_lang_combo.current(i + 1)  # +1 for "(None)"
                    break
        else:
            self._secondary_lang_combo.current(0)
        self._secondary_lang_combo.pack(side=tk.LEFT, padx=(4, 0))

        # Diarization
        diar_row = ttk.Frame(settings_frame)
        diar_row.pack(fill=tk.X, pady=(0, 4))
        self._diarization_var = tk.BooleanVar(value=DEFAULT_DIARIZATION)
        ttk.Checkbutton(
            diar_row,
            text="Speaker diarization",
            variable=self._diarization_var,
        ).pack(side=tk.LEFT)

        # --- Output Formats ---
        fmt_frame = ttk.LabelFrame(main, text="Output Formats", padding=_PAD)
        fmt_frame.pack(fill=tk.X, pady=(0, _PAD))

        self._format_vars: Dict[str, tk.BooleanVar] = {}
        for key, label, default in _FORMAT_OPTIONS:
            var = tk.BooleanVar(value=default)
            self._format_vars[key] = var
            ttk.Checkbutton(fmt_frame, text=label, variable=var).pack(
                anchor=tk.W
            )

        # --- Output Directory ---
        outdir_frame = ttk.LabelFrame(main, text="Output Directory", padding=_PAD)
        outdir_frame.pack(fill=tk.X, pady=(0, _PAD))

        self._outdir_label = ttk.Label(
            outdir_frame, text="Same as input file", foreground="gray"
        )
        self._outdir_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._outdir_browse_btn = ttk.Button(
            outdir_frame, text="Browse...", command=self._browse_output_dir
        )
        self._outdir_browse_btn.pack(side=tk.RIGHT, padx=(4, 0))
        self._outdir_reset_btn = ttk.Button(
            outdir_frame, text="Reset", command=self._reset_output_dir
        )
        self._outdir_reset_btn.pack(side=tk.RIGHT)

        # --- Action Buttons ---
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(0, _PAD))

        self._transcribe_btn = ttk.Button(
            btn_frame,
            text="Transcribe",
            command=self._start_transcription,
            state=tk.DISABLED,
        )
        self._transcribe_btn.pack(side=tk.LEFT)

        self._cancel_btn = ttk.Button(
            btn_frame,
            text="Cancel",
            command=self._cancel_transcription,
            state=tk.DISABLED,
        )
        self._cancel_btn.pack(side=tk.LEFT, padx=(_PAD, 0))

        self._new_btn = ttk.Button(
            btn_frame,
            text="New Transcription",
            command=self._reset_ui,
        )
        self._new_btn.pack(side=tk.RIGHT)
        self._new_btn.pack_forget()  # Hidden initially

        self._open_folder_btn = ttk.Button(
            btn_frame,
            text="Open Folder",
            command=self._open_output_folder,
        )
        self._open_folder_btn.pack(side=tk.RIGHT, padx=(_PAD, 0))
        self._open_folder_btn.pack_forget()  # Hidden initially

        # --- Status Area ---
        status_frame = ttk.LabelFrame(main, text="Status", padding=_PAD)
        status_frame.pack(fill=tk.BOTH, expand=True)

        self._status_text = tk.Text(
            status_frame,
            height=10,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("TkDefaultFont", 11),
        )
        scrollbar = ttk.Scrollbar(
            status_frame, orient=tk.VERTICAL, command=self._status_text.yview
        )
        self._status_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._status_text.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # API Key Check
    # ------------------------------------------------------------------

    def _check_api_key(self) -> None:
        """Check if the API key is configured; prompt if missing."""
        try:
            load_api_key()
        except ValueError:
            self._show_api_key_dialog()

    def _show_api_key_dialog(self) -> None:
        """Show a dialog to enter and save the Soniox API key."""
        dialog = tk.Toplevel(self._root)
        dialog.title("API Key Setup")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frame,
            text="Soniox API key not configured.",
            font=("TkDefaultFont", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 8))

        ttk.Label(
            frame,
            text="Get your API key at soniox.com/account",
        ).pack(anchor=tk.W, pady=(0, 8))

        ttk.Label(frame, text="API Key:").pack(anchor=tk.W)
        key_var = tk.StringVar()
        key_entry = ttk.Entry(frame, textvariable=key_var, width=50, show="*")
        key_entry.pack(fill=tk.X, pady=(0, 12))
        key_entry.focus_set()

        def _save_key() -> None:
            key = key_var.get().strip()
            if not key:
                messagebox.showwarning(
                    "Missing Key", "Please enter an API key.", parent=dialog
                )
                return
            # Write/update .env file
            env_path = Path.cwd() / ".env"
            lines: List[str] = []
            found = False
            if env_path.is_file():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("SONIOX_API_KEY="):
                        lines.append("SONIOX_API_KEY={}".format(key))
                        found = True
                    else:
                        lines.append(line)
            if not found:
                lines.append("SONIOX_API_KEY={}".format(key))
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            # Set in current process
            os.environ["SONIOX_API_KEY"] = key
            dialog.destroy()

        ttk.Button(frame, text="Save", command=_save_key).pack(anchor=tk.E)

        # Center dialog on parent
        dialog.update_idletasks()
        x = self._root.winfo_x() + (self._root.winfo_width() - dialog.winfo_width()) // 2
        y = self._root.winfo_y() + (self._root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry("+{}+{}".format(x, y))

    # ------------------------------------------------------------------
    # File Browsing
    # ------------------------------------------------------------------

    def _browse_file(self) -> None:
        """Open a file dialog to select an audio/video file."""
        # Build file type filter
        extensions = sorted(SONIOX_SUPPORTED_FORMATS)
        ext_pattern = " ".join(["*{}".format(e) for e in extensions])
        path = filedialog.askopenfilename(
            title="Select Audio/Video File",
            filetypes=[
                ("Audio/Video files", ext_pattern),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._set_input_file(Path(path))

    def _set_input_file(self, path: Path) -> None:
        """Set the selected input file and update UI."""
        ext = path.suffix.lower()
        if ext not in SONIOX_SUPPORTED_FORMATS:
            sorted_formats = sorted(SONIOX_SUPPORTED_FORMATS)
            messagebox.showerror(
                "Unsupported File",
                "This file type ({}) is not supported.\n\n"
                "Supported formats: {}".format(ext, ", ".join(sorted_formats)),
            )
            return

        self._input_path = path
        self._file_label.configure(
            text=str(path.name), foreground="white"
        )
        self._transcribe_btn.configure(state=tk.NORMAL)

        # Auto-discover context files
        self._auto_discover_context(path)

        # Default output dir to input file's directory
        if self._output_dir is None:
            self._outdir_label.configure(
                text=str(path.parent), foreground="white"
            )

    def _auto_discover_context(self, audio_path: Path) -> None:
        """Auto-discover companion script and terms files."""
        companion = resolve_companion_files(audio_path)

        # Script auto-detection
        if self._script_path is None and companion.script_path:
            self._script_path = companion.script_path
            self._script_label.configure(
                text="{} (auto-detected)".format(companion.script_path.name),
                foreground="green",
            )

        # Terms auto-detection
        if self._terms_path is None and companion.terms_path:
            self._terms_path = companion.terms_path
            terms = load_terms(companion.terms_path)
            self._terms_label.configure(
                text="{} ({} terms, auto-detected)".format(
                    companion.terms_path.name, len(terms)
                ),
                foreground="green",
            )

    def _browse_script(self) -> None:
        """Browse for a script file."""
        path = filedialog.askopenfilename(
            title="Select Script File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self._script_path = Path(path)
            self._script_label.configure(
                text=Path(path).name, foreground="white"
            )

    def _clear_script(self) -> None:
        """Clear the selected script file."""
        self._script_path = None
        self._script_label.configure(text="None", foreground="gray")

    def _browse_terms(self) -> None:
        """Browse for a terms file."""
        path = filedialog.askopenfilename(
            title="Select Terms File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self._terms_path = Path(path)
            terms = load_terms(path)
            self._terms_label.configure(
                text="{} ({} terms)".format(Path(path).name, len(terms)),
                foreground="white",
            )

    def _clear_terms(self) -> None:
        """Clear the selected terms file."""
        self._terms_path = None
        self._terms_label.configure(text="None", foreground="gray")

    def _browse_output_dir(self) -> None:
        """Browse for an output directory."""
        path = filedialog.askdirectory(title="Select Output Directory")
        if path:
            self._output_dir = Path(path)
            self._outdir_label.configure(text=str(path), foreground="white")

    def _reset_output_dir(self) -> None:
        """Reset output directory to same as input file."""
        self._output_dir = None
        if self._input_path:
            self._outdir_label.configure(
                text=str(self._input_path.parent), foreground="white"
            )
        else:
            self._outdir_label.configure(
                text="Same as input file", foreground="gray"
            )

    # ------------------------------------------------------------------
    # Transcription Control
    # ------------------------------------------------------------------

    def _start_transcription(self) -> None:
        """Start the transcription pipeline in a background thread."""
        if not self._input_path:
            return

        # Check API key
        try:
            load_api_key()
        except ValueError:
            self._show_api_key_dialog()
            return

        # Determine which formats to run
        selected_formats = self._get_selected_formats()
        if not selected_formats:
            messagebox.showwarning(
                "No Formats", "Please select at least one output format."
            )
            return

        # Prepare parameters
        primary_lang = self._primary_lang_var.get()
        secondary_lang = self._secondary_lang_var.get()
        if secondary_lang == "(None)":
            secondary_lang = None
        diarization = self._diarization_var.get()
        output_dir = self._output_dir or self._input_path.parent

        # Validate output directory
        if not output_dir.is_dir():
            messagebox.showerror(
                "Invalid Directory",
                "Cannot save to this folder. Check permissions.",
            )
            return

        # Switch to processing state
        self._set_processing_state()

        # Clear cancel event
        self._cancel_event.clear()

        # Start background thread
        self._worker_thread = threading.Thread(
            target=self._run_pipeline_thread,
            args=(
                self._input_path,
                primary_lang,
                secondary_lang,
                diarization,
                selected_formats,
                output_dir,
                self._script_path,
                self._terms_path,
            ),
            daemon=True,
        )
        self._worker_thread.start()

        # Start polling the status queue
        self._poll_status()

    def _get_selected_formats(self) -> List[str]:
        """Get the list of selected format keys."""
        selected: List[str] = []
        for key, _, _ in _FORMAT_OPTIONS:
            if self._format_vars[key].get():
                # Map GUI keys to FORMATTERS keys
                if key == "srt_broadcast" or key == "srt_social":
                    # SRT formatter produces both; we track selection for output filtering
                    if "srt_captions" not in selected:
                        selected.append("srt_captions")
                else:
                    selected.append(key)
        return selected

    def _cancel_transcription(self) -> None:
        """Signal the background thread to cancel."""
        self._cancel_event.set()
        self._cancel_btn.configure(state=tk.DISABLED)
        self._append_status("Cancelling...")

    def _set_processing_state(self) -> None:
        """Switch UI to processing state."""
        self._transcribe_btn.configure(state=tk.DISABLED)
        self._browse_btn.configure(state=tk.DISABLED)
        self._script_browse_btn.configure(state=tk.DISABLED)
        self._script_clear_btn.configure(state=tk.DISABLED)
        self._terms_browse_btn.configure(state=tk.DISABLED)
        self._terms_clear_btn.configure(state=tk.DISABLED)
        self._outdir_browse_btn.configure(state=tk.DISABLED)
        self._outdir_reset_btn.configure(state=tk.DISABLED)
        self._cancel_btn.configure(state=tk.NORMAL)
        self._clear_status()
        self._append_status("Starting transcription...")

    def _set_done_state(self) -> None:
        """Switch UI to done state."""
        self._cancel_btn.configure(state=tk.DISABLED)
        self._browse_btn.configure(state=tk.NORMAL)
        self._script_browse_btn.configure(state=tk.NORMAL)
        self._script_clear_btn.configure(state=tk.NORMAL)
        self._terms_browse_btn.configure(state=tk.NORMAL)
        self._terms_clear_btn.configure(state=tk.NORMAL)
        self._outdir_browse_btn.configure(state=tk.NORMAL)
        self._outdir_reset_btn.configure(state=tk.NORMAL)

        # Show done buttons
        self._open_folder_btn.pack(side=tk.RIGHT, padx=(_PAD, 0))
        self._new_btn.pack(side=tk.RIGHT)

    def _set_idle_state(self) -> None:
        """Switch UI to idle state."""
        self._browse_btn.configure(state=tk.NORMAL)
        self._script_browse_btn.configure(state=tk.NORMAL)
        self._script_clear_btn.configure(state=tk.NORMAL)
        self._terms_browse_btn.configure(state=tk.NORMAL)
        self._terms_clear_btn.configure(state=tk.NORMAL)
        self._outdir_browse_btn.configure(state=tk.NORMAL)
        self._outdir_reset_btn.configure(state=tk.NORMAL)
        self._cancel_btn.configure(state=tk.DISABLED)
        if self._input_path:
            self._transcribe_btn.configure(state=tk.NORMAL)

    def _reset_ui(self) -> None:
        """Reset the UI to initial idle state for a new transcription."""
        self._input_path = None
        self._output_dir = None
        self._script_path = None
        self._terms_path = None
        self._saved_files = []
        self._transcript_preview = ""

        self._file_label.configure(text="No file selected", foreground="gray")
        self._script_label.configure(text="None", foreground="gray")
        self._terms_label.configure(text="None", foreground="gray")
        self._outdir_label.configure(text="Same as input file", foreground="gray")

        self._transcribe_btn.configure(state=tk.DISABLED)
        self._cancel_btn.configure(state=tk.DISABLED)
        self._open_folder_btn.pack_forget()
        self._new_btn.pack_forget()

        self._set_idle_state()
        self._clear_status()

    # ------------------------------------------------------------------
    # Status Display
    # ------------------------------------------------------------------

    def _clear_status(self) -> None:
        """Clear the status text area."""
        self._status_text.configure(state=tk.NORMAL)
        self._status_text.delete("1.0", tk.END)
        self._status_text.configure(state=tk.DISABLED)

    def _append_status(self, text: str) -> None:
        """Append a line to the status text area."""
        self._status_text.configure(state=tk.NORMAL)
        self._status_text.insert(tk.END, text + "\n")
        self._status_text.see(tk.END)
        self._status_text.configure(state=tk.DISABLED)

    def _poll_status(self) -> None:
        """Poll the status queue and update the UI."""
        try:
            while True:
                msg_type, msg_data = self._status_queue.get_nowait()

                if msg_type == _STATUS_MSG:
                    self._append_status(msg_data)

                elif msg_type == _DONE_MSG:
                    saved_files, preview = msg_data
                    self._saved_files = saved_files
                    self._transcript_preview = preview
                    self._show_done(saved_files, preview)
                    self._set_done_state()
                    return

                elif msg_type == _ERROR_MSG:
                    self._append_status("ERROR: {}".format(msg_data))
                    self._set_idle_state()
                    messagebox.showerror("Transcription Error", str(msg_data))
                    return

                elif msg_type == _CANCELLED_MSG:
                    self._append_status("Transcription cancelled.")
                    self._set_idle_state()
                    return

        except queue.Empty:
            pass

        # Continue polling
        self._root.after(100, self._poll_status)

    def _show_done(self, saved_files: List[Path], preview: str) -> None:
        """Show the completion results."""
        output_dir = saved_files[0].parent if saved_files else ""
        self._append_status("")
        self._append_status("Done! Saved {} file(s) to {}".format(
            len(saved_files), output_dir
        ))
        for f in saved_files:
            self._append_status("  {}".format(f.name))

        if preview:
            self._append_status("")
            self._append_status("--- Transcript Preview ---")
            # Show first ~20 lines of preview
            lines = preview.split("\n")
            for line in lines[:20]:
                self._append_status(line)
            if len(lines) > 20:
                self._append_status("... ({} more lines)".format(len(lines) - 20))

    def _open_output_folder(self) -> None:
        """Open the output folder in the system file manager."""
        if self._saved_files:
            folder = str(self._saved_files[0].parent)
        elif self._input_path:
            folder = str(self._output_dir or self._input_path.parent)
        else:
            return

        if sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    # ------------------------------------------------------------------
    # Background Pipeline
    # ------------------------------------------------------------------

    def _run_pipeline_thread(
        self,
        input_path: Path,
        primary_lang: str,
        secondary_lang: Optional[str],
        diarization: bool,
        format_keys: List[str],
        output_dir: Path,
        script_path: Optional[Path],
        terms_path: Optional[Path],
    ) -> None:
        """Run the transcription pipeline in a background thread.

        WHY: The Soniox API client is async and long-running. Running it
        on the main thread would freeze the tkinter UI.

        HOW: Uses asyncio.run() in the background thread. Status updates
        are posted to self._status_queue for the main thread to display.

        RULES:
        - NEVER touch tkinter widgets from this thread
        - All status updates go through self._status_queue
        - Check self._cancel_event periodically for cancellation
        """
        try:
            asyncio.run(self._run_pipeline_async(
                input_path,
                primary_lang,
                secondary_lang,
                diarization,
                format_keys,
                output_dir,
                script_path,
                terms_path,
            ))
        except Exception as e:
            self._status_queue.put((_ERROR_MSG, str(e)))

    async def _run_pipeline_async(
        self,
        input_path: Path,
        primary_lang: str,
        secondary_lang: Optional[str],
        diarization: bool,
        format_keys: List[str],
        output_dir: Path,
        script_path: Optional[Path],
        terms_path: Optional[Path],
    ) -> None:
        """Async pipeline implementation."""
        from soniox_converter.api.client import SonioxClient

        def on_status(msg: str) -> None:
            self._status_queue.put((_STATUS_MSG, msg))

        def check_cancel() -> None:
            if self._cancel_event.is_set():
                raise KeyboardInterrupt("Cancelled by user")

        # Load context files
        on_status("Loading context files...")
        script_text: Optional[str] = None
        all_terms: List[str] = []

        companion = resolve_companion_files(input_path)

        if script_path:
            script_text = load_script(script_path)
            on_status("  Script: {} (explicit, {} chars)".format(
                script_path.name, len(script_text)))
        elif companion.script_path:
            script_text = load_script(companion.script_path)
            on_status("  Script: {} (auto-discovered, {} chars)".format(
                companion.script_path.name, len(script_text)))
        else:
            on_status("  Script: (none found)")

        if terms_path:
            all_terms.extend(load_terms(terms_path))
            on_status("  Terms: {} (explicit)".format(terms_path.name))
        elif companion.terms_path:
            all_terms.extend(load_terms(companion.terms_path))
            on_status("  Terms: {} (auto-discovered)".format(companion.terms_path.name))

        # Default terms
        if companion.default_terms_path:
            dt = load_terms(companion.default_terms_path)
            all_terms.extend(dt)
            if dt:
                on_status("  Default terms: {} ({} terms)".format(
                    companion.default_terms_path.name, len(dt)
                ))
        else:
            dt = load_default_terms(Path.cwd())
            all_terms.extend(dt)
            if dt:
                on_status("  Default terms: default-terms.txt ({} terms)".format(len(dt)))

        # Deduplicate terms
        seen: set = set()
        unique_terms: List[str] = []
        for term in all_terms:
            if term not in seen:
                seen.add(term)
                unique_terms.append(term)

        final_terms: Optional[List[str]] = unique_terms if unique_terms else None

        # Build and validate context
        context = build_context(script_text=script_text, terms=final_terms)
        if context:
            on_status("  Context loaded ({} sections)".format(len(context)))

        check_cancel()

        # Build language hints
        language_hints: List[str] = [primary_lang]
        if secondary_lang:
            language_hints.append(secondary_lang)

        stem = input_path.stem
        file_id: Optional[str] = None
        transcription_id: Optional[str] = None

        try:
            async with SonioxClient() as client:
                # Upload
                check_cancel()
                file_id = await client.upload_file(input_path, on_status=on_status)

                # Create transcription
                check_cancel()
                transcription_id = await client.create_transcription(
                    file_id=file_id,
                    language_hints=language_hints,
                    enable_diarization=diarization,
                    enable_language_identification=True,
                    script_text=script_text,
                    terms=final_terms,
                    on_status=on_status,
                )

                # Poll until complete (check cancel periodically)
                check_cancel()
                await client.poll_until_complete(
                    transcription_id, on_status=on_status
                )

                # Fetch transcript
                check_cancel()
                tokens = await client.fetch_transcript(
                    transcription_id, on_status=on_status
                )

                # Assemble tokens
                on_status("Assembling tokens...")
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
                on_status("  Assembled {} words".format(len(words)))

                # Build Transcript IR
                transcript = _build_transcript(words, input_path.name)
                on_status("  {} segments, {} speakers, primary language: {}".format(
                    len(transcript.segments),
                    len(transcript.speakers),
                    transcript.primary_language,
                ))

                # Run formatters
                check_cancel()
                on_status("Formatting output...")
                saved_files: List[Path] = []

                # Determine which SRT variants the user wants
                want_broadcast = self._format_vars.get("srt_broadcast", tk.BooleanVar(value=False)).get()
                want_social = self._format_vars.get("srt_social", tk.BooleanVar(value=False)).get()

                for key in format_keys:
                    formatter = FORMATTERS[key]()
                    on_status("  Running {} formatter...".format(formatter.name))
                    outputs = formatter.format(transcript)

                    for output in outputs:
                        # Filter SRT outputs based on user selection
                        if key == "srt_captions":
                            if output.suffix == "-broadcast.srt" and not want_broadcast:
                                continue
                            if output.suffix == "-social.srt" and not want_social:
                                continue

                        path = _resolve_output_path(stem, output.suffix, output_dir)
                        if isinstance(output.content, bytes):
                            path.write_bytes(output.content)
                        else:
                            path.write_text(output.content, encoding="utf-8")
                        saved_files.append(path)
                        on_status("  Saved: {}".format(path.name))

                # Cleanup
                await client.cleanup(transcription_id, file_id, on_status=on_status)

                # Build transcript preview
                preview = self._build_preview(transcript)

                self._status_queue.put((_DONE_MSG, (saved_files, preview)))

        except KeyboardInterrupt:
            on_status("Cancelled by user.")
            if file_id and transcription_id:
                try:
                    async with SonioxClient() as cleanup_client:
                        await cleanup_client.cleanup(transcription_id, file_id)
                except Exception:
                    pass
            self._status_queue.put((_CANCELLED_MSG, None))

        except ValueError as e:
            self._status_queue.put((_ERROR_MSG, str(e)))

        except Exception as e:
            # Try cleanup
            if file_id and transcription_id:
                try:
                    async with SonioxClient() as cleanup_client:
                        await cleanup_client.cleanup(transcription_id, file_id)
                except Exception:
                    pass
            self._status_queue.put((_ERROR_MSG, str(e)))

    def _build_preview(self, transcript: Transcript) -> str:
        """Build a plain-text transcript preview for the done state.

        WHY: Users want to see a quick preview of the transcript content
        after processing completes.

        HOW: Iterates segments and formats each speaker turn as a labeled
        paragraph.

        RULES:
        - Format: "Speaker N: <words>"
        - One paragraph per speaker turn
        - Max 50 lines for preview
        """
        lines: List[str] = []
        for segment in transcript.segments:
            speaker = segment.speaker or "Speaker"
            # Find display name
            display_name = "Speaker"
            for si in transcript.speakers:
                if si.soniox_label == speaker:
                    display_name = si.display_name
                    break

            # Merge punctuation onto preceding word (same as plain text formatter)
            parts: List[str] = []
            for w in segment.words:
                if w.word_type == "punctuation" and parts:
                    parts[-1] += w.text
                elif w.word_type == "word":
                    # Suppress space after comma/dash for decimal numbers
                    if parts and parts[-1][-1:] in (",", "-") and w.text.isdigit():
                        parts[-1] += w.text
                    else:
                        parts.append(w.text)
            words_text = " ".join(parts)
            if words_text:
                lines.append("{}: {}".format(display_name, words_text))

        return "\n".join(lines[:50])

    # ------------------------------------------------------------------
    # Utility (accessed from background thread via var.get())
    # ------------------------------------------------------------------
    # Note: tk.BooleanVar.get() is thread-safe in CPython due to the GIL.
    # We read format_vars from the background thread which is acceptable.


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Launch the Tkinter GUI application.

    WHY: Provides a standalone entry point for the GUI, callable via
    ``python -m soniox_converter.gui`` or the --gui CLI flag.

    HOW: Creates the root Tk window, instantiates TranscriberApp,
    and starts the main loop.

    RULES:
    - This function blocks until the window is closed
    - Must be called from the main thread
    """
    root = tk.Tk()
    TranscriberApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
