# Soniox Transcript Converter — Product Requirements Document

**Version**: 1.0  
**Date**: 2026-02-13  
**Status**: Ready for implementation

---

## 1. Purpose

EFN editors need speech transcripts in various formats for video editing (Premiere Pro), captioning (SRT/SDH), and archival (plain text). Soniox provides superior multilingual ASR with speaker diarization — especially for Swedish-dominant content with English code-switching — but its output is a flat sub-word token array that no editing tool can ingest directly.

This tool is an **extensible transcription format hub**: Soniox tokens in, any structured transcript format out. Premiere Pro JSON is the first and primary output format, but the architecture supports pluggable formatters for SRT, SDH captions, plain text, sentence-level JSON, and formats not yet defined.

---

## 2. Users

**Primary users**: Any editor on the EFN team. The tool must be foolproof — users know nothing about APIs, tokens, or JSON schemas. They have an audio or video file and need a transcript they can use.

**Secondary users**: Coding agents and developers extending the tool with new output formats. The codebase must be legible and modifiable by LLMs through well-documented, intent-infused docstrings.

**Content profile**:

- Primary language: Swedish
- Common secondary: English (code-switching within interviews, presentations)
- Typical file lengths: 5 minutes to 2 hours, occasionally up to 5 hours (Soniox limit: 300 minutes)
- Speaker count: typically 2–6 speakers per recording

---

## 3. Architecture

### 3.1 Core Principle: Intermediate Representation

The system is built around a three-stage pipeline:

```
┌──────────────┐     ┌──────────────────────┐     ┌───────────────────┐
│  Soniox API  │────▶│  Intermediate Repr.  │────▶│  Output Formatter │
│  (flat tokens)│     │  (assembled words,   │     │  (pluggable)      │
│              │     │   speakers, segments) │     │                   │
└──────────────┘     └──────────────────────┘     └───────────────────┘
```

**Stage 1 — Ingest**: Call Soniox async API, receive flat token array.

**Stage 2 — Assemble**: Build the intermediate representation (IR) by assembling sub-word tokens into words, classifying punctuation, inferring sentence boundaries, grouping by speaker, and mapping language codes. This is the stable core of the system. The IR is a well-defined Python data structure that all formatters consume.

**Stage 3 — Format**: Pluggable output formatters each take the IR and produce a specific output format. Adding a new format means writing one new formatter module — no changes to ingest or assembly.

### 3.2 Module Structure

```
soniox_converter/
├── __init__.py
├── api/                        # Stage 1: Soniox API interaction
│   ├── __init__.py
│   ├── client.py               # Upload, create transcription, poll, fetch
│   └── models.py               # Soniox request/response dataclasses
├── core/                       # Stage 2: Assembly & IR
│   ├── __init__.py
│   ├── assembler.py            # Sub-word → word assembly, punctuation, EOS
│   ├── segmenter.py            # Speaker grouping, segment construction
│   └── ir.py                   # Intermediate representation dataclasses
├── formatters/                 # Stage 3: Output formatters (pluggable)
│   ├── __init__.py
│   ├── base.py                 # Abstract base formatter
│   ├── premiere_pro.py         # Premiere Pro Audio Transcript JSON
│   ├── srt_captions.py         # SRT captions via caption formatting lib (broadcast + social)
│   ├── kinetic_words.py        # Kinetic word reveal — 3 Premiere JSONs for social video
│   └── plain_text.py           # Plain text transcript
├── config.py                   # Language mappings, defaults, .env loading
├── cli.py                      # Command-line interface
└── gui.py                      # Tkinter desktop GUI (Phase 1)

format_captions/                # Refactored caption formatting library (separate package)
├── __init__.py                 # Public API: format_srt(words, preset) → str
├── core.py                     # DP segmentation, line breaking, SRT generation
├── presets.py                  # PRESET_BROADCAST, PRESET_SOCIAL, WEAK_END_WORDS
├── models.py                   # Word dataclass
└── cli.py                      # CLI wrapper (preserves existing CLI behavior)
```

### 3.3 LLM-Optimized Codebase

Every module, class, and public function must have docstrings that follow this pattern:

```python
def assemble_tokens(tokens: list[SonioxToken]) -> list[AssembledWord]:
    """Assemble Soniox sub-word tokens into whole words.

    WHY: Soniox uses BPE tokenization, splitting words like "fantastic"
    into [" fan", "tastic"]. Downstream formatters need whole words with
    unified timing, confidence, and speaker attribution.

    HOW: A leading space in token.text signals a new word boundary.
    Continuation tokens (no leading space) are appended to the current
    word. Punctuation-only tokens become standalone items.

    RULES:
    - Leading space → new word (strip the space from output text)
    - No leading space + existing word → continuation (extend end_ms, append confidence)
    - Punctuation-only token → standalone (type="punctuation")
    - First token in array → new word (even without leading space)

    Args:
        tokens: Flat list of SonioxToken objects from the async API response.
                Translation tokens (translation_status="translation") must
                already be filtered out before calling this function.

    Returns:
        List of AssembledWord objects with unified text, timing, confidence,
        speaker, and language fields ready for segmentation and formatting.
    """
```

The goal: a coding agent reading any single module should understand what it does, why it exists, and how it connects to the rest of the system — without needing to read other files.

### 3.4 Possible OpenAPI Spec

When the tool is deployed as a service (Phase 2 and beyond), it should expose an OpenAPI-documented HTTP interface for its conversion capabilities. This allows n8n, Slack bots, or any other integration layer to call it as a standard REST API. The OpenAPI spec should be auto-generated from the codebase (e.g., via FastAPI) rather than manually maintained.

---

## 4. Intermediate Representation (IR)

The IR is the contract between assembly and formatting. All formatters depend on it. It must be stable, well-typed, and thoroughly documented.

### 4.1 Core Data Structures

```python
@dataclass
class AssembledWord:
    """A single word or punctuation mark assembled from one or more Soniox sub-word tokens."""
    text: str               # The assembled word text, e.g. "fantastic" or "?"
    start_s: float          # Start time in seconds (converted from start_ms)
    duration_s: float       # Duration in seconds (converted from end_ms - start_ms)
    confidence: float       # Aggregated confidence score, 0.0–1.0
    word_type: str          # "word" or "punctuation"
    eos: bool               # True if this word ends a sentence
    speaker: str | None     # Soniox speaker label ("1", "2", ...) or None
    language: str | None    # ISO 639-1 code ("en", "sv", ...) or None
    tags: list[str]         # Always empty for Soniox input; reserved for future use

@dataclass
class Segment:
    """A contiguous group of words from a single speaker."""
    speaker: str | None     # Soniox speaker label
    language: str           # Dominant language of the segment (ISO 639-1)
    start_s: float          # Start time of first word in segment
    duration_s: float       # End of last word minus start of first word
    words: list[AssembledWord]

@dataclass
class SpeakerInfo:
    """Metadata for a unique speaker in the transcript."""
    soniox_label: str       # Original Soniox label ("1", "2", ...)
    display_name: str       # Human-readable name ("Speaker 1", "Speaker 2", ...)
    uuid: str               # Generated UUID v4 (for formats that need it)

@dataclass
class Transcript:
    """The complete intermediate representation of an assembled transcript."""
    segments: list[Segment]
    speakers: list[SpeakerInfo]
    primary_language: str   # ISO 639-1 code
    source_filename: str    # Original audio/video filename
    duration_s: float       # Total audio duration (end of last word)
```

---

## 5. Conversion Logic

### 5.1 Sub-Word Token Assembly

Soniox uses BPE tokenization. Words are split into sub-word tokens. The leading space character in `token.text` is the sole word-boundary signal.

**Rules**:

| Rule | Signal | Meaning | Example |
|------|--------|---------|---------|
| Leading space = new word | `text` starts with `" "` | Begin a new word | `" are"`, `" fan"` |
| No leading space = continuation | `text` has no leading space, not first token | Append to current word | `"tastic"` after `" fan"` → "fantastic" |
| Punctuation = standalone | `text` is only punctuation (`"."`, `","`, `"?"`, `"!"`, `";"`, `":"`, `"…"`, `"—"`) | Separate item | `"?"` after `" you"` |
| First token, no space | First token in array | Starts a new word | `"How"`, `"I"` |

**Example assembly**:

Soniox tokens: `"How"`, `" are"`, `" you"`, `" do"`, `"ing"`, `" to"`, `"day"`, `"?"`

Result: `How` | `are` | `you` | `doing` (520–720ms) | `today` (730–920ms) | `?`

### 5.2 Timestamp Conversion

Soniox uses integer milliseconds. The IR and most output formats use float seconds.

```
start_s  = start_ms / 1000.0
duration_s = (end_ms - start_ms) / 1000.0
```

For assembled multi-token words: `start_ms` from the first sub-word, `end_ms` from the last.

### 5.3 Confidence Aggregation

When multiple sub-word tokens form a word, their confidence scores are aggregated using the **minimum** (conservative) strategy:

```
word_confidence = min(token.confidence for token in sub_word_tokens)
```

This is the conservative approach — the word is only as confident as its weakest token. Downstream consumers can trust that a word with confidence 0.95 had *all* sub-words at ≥0.95.

### 5.4 End-of-Sentence (EOS) Inference

Soniox provides no explicit sentence boundary. EOS is inferred from punctuation:

- Sentence-ending punctuation: `.`, `?`, `!`
- The **word immediately before** a sentence-ending punctuation token gets `eos: true`
- All other words get `eos: false`
- Commas, colons, semicolons are NOT sentence-ending

### 5.5 Segmentation

The Transcript IR stores words in a flat list with per-word speaker, timing, and EOS metadata. Segmentation into groups happens at the **formatter level**, not in the core — different output formats need different segmentation strategies:

- **Premiere Pro JSON**: One segment per sentence (split at EOS boundaries). Each segment carries its speaker UUID. A 10-sentence monologue from one speaker = 10 segments.
- **SRT captions**: Segmentation handled entirely by the caption formatting library (DP-based, Swedish-aware). The converter's SRT formatter is an adapter that feeds words into the caption lib.
- **Kinetic word reveal**: Segmentation is bucket-based (groups of 3 words within sentences). Single speaker.
- **Plain text**: Groups by speaker — a new paragraph starts when the speaker changes.

Speaker metadata mapping (shared across all formatters):
- `"1"` → `SpeakerInfo(soniox_label="1", display_name="Speaker 1", uuid=<generated UUID v4>)`
- Up to 15 speakers supported by Soniox

When diarization is disabled, all words are attributed to a single default speaker.

### 5.6 Language Code Mapping

Soniox uses ISO 639-1 (e.g., `"en"`, `"sv"`). Output formats often need BCP-47 locale codes. The mapping is maintained in `config.py`:

```python
LANGUAGE_MAP = {
    "sv": "sv-se",
    "en": "en-us",
    "da": "da-dk",
    "no": "nb-no",
    "fi": "fi-fi",
    "de": "de-de",
    "fr": "fr-fr",
    "es": "es-es",
    "nl": "nl-nl",
    "it": "it-it",
    "pt": "pt-br",
    "ja": "ja-jp",
    "ko": "ko-kr",
    "zh": "cmn-hans",
    "ar": "ar-sa",
    "ru": "ru-ru",
    "pl": "pl-pl",
    "tr": "tr-tr",
    "hi": "hi-in",
}
```

Unmapped languages fall back to `"??-??"` (Premiere Pro's unknown language code).

### 5.7 Translation Token Filtering

If translation is enabled in the Soniox request, the token array contains interleaved translation tokens. These must be filtered out before assembly:

- **Keep**: tokens where `translation_status` is `"original"`, `"none"`, or the field is absent
- **Discard**: tokens where `translation_status` is `"translation"`

---

## 6. Output Formatters

### 6.1 Base Formatter Interface

```python
@dataclass
class FormatterOutput:
    """One output file from a formatter."""
    suffix: str             # File suffix, e.g. "-transcript.json" or "-kinetic-row1.json"
    content: str | bytes    # File content

class BaseFormatter(ABC):
    """Abstract base for all output formatters.

    WHY: Every output format consumes the same Transcript IR but produces
    different file content. This base class enforces a consistent interface
    so the GUI, CLI, and API layers can work with any formatter generically.

    MULTI-FILE SUPPORT: Most formatters produce one file, but some (e.g.,
    Kinetic Word Reveal) produce multiple. The format() method returns a
    list of FormatterOutput objects. Single-file formatters return a list
    of one.

    To add a new output format:
    1. Create a new file in formatters/
    2. Subclass BaseFormatter
    3. Implement format() and name
    4. Register in FORMATTERS dict in formatters/__init__.py
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable format name, e.g. 'Premiere Pro JSON'."""

    @abstractmethod
    def format(self, transcript: Transcript) -> list[FormatterOutput]:
        """Convert the Transcript IR into one or more output files."""
```

Output file naming: the CLI/GUI takes the source filename stem and appends each `FormatterOutput.suffix`:
```
source: interview.mp4
Premiere Pro formatter output:  [FormatterOutput("-transcript.json", ...)]
  → interview-transcript.json

Kinetic formatter output:       [FormatterOutput("-kinetic-row1.json", ...),
                                 FormatterOutput("-kinetic-row2.json", ...),
                                 FormatterOutput("-kinetic-row3.json", ...)]
  → interview-kinetic-row1.json
  → interview-kinetic-row2.json
  → interview-kinetic-row3.json
```

### 6.2 Premiere Pro JSON Formatter

Produces files conforming to the Adobe Premiere Pro Audio Transcript schema (`v1.0.0`).

**Key mapping**:

| IR field | Premiere Pro field | Notes |
|----------|-------------------|-------|
| `Transcript.primary_language` | root `language` | Mapped to BCP-47 via `LANGUAGE_MAP` |
| `Transcript.segments` | `segments[]` | One segment per sentence (split at EOS) |
| `Segment.start_s` | `segment.start` | Float seconds |
| `Segment.duration_s` | `segment.duration` | Float seconds |
| `SpeakerInfo.uuid` | `segment.speaker` | UUID v4 string |
| `Segment.language` | `segment.language` | BCP-47 code |
| `AssembledWord.*` | `segment.words[]` | See word mapping below |
| `AssembledWord.text` | `word.text` | Assembled word text |
| `AssembledWord.start_s` | `word.start` | Float seconds |
| `AssembledWord.duration_s` | `word.duration` | Float seconds |
| `AssembledWord.confidence` | `word.confidence` | 0.0–1.0 |
| `AssembledWord.eos` | `word.eos` | Boolean |
| `AssembledWord.word_type` | `word.type` | `"word"` or `"punctuation"` |
| `AssembledWord.tags` | `word.tags` | Always `[]` |

**File extension**: `-transcript.json`

### 6.3 SRT Caption Formatter

All SRT output goes through the caption formatting library. There is no separate "simple SRT" — SRT always means properly formatted, linguistically optimized Swedish SDH captions.

Two presets:

- **Broadcast** (standard): 2×42 character limit, DP segmentation with Swedish weak-word avoidance, speaker markers (em-dash)
- **Social media** (9:16 vertical): 1×25 character limit, single-line captions

**File extensions**: `-broadcast.srt` (broadcast), `-social.srt` (social)

#### 6.3.1 Caption Formatter Refactor

The existing `format_captions.py` is a standalone CLI script with a module-level mutable `CONFIG` global. It must be refactored into a library with a clean API before integration:

**Current state** (problems for library use):
- `CONFIG` is a module-level global dict mutated by `main()`
- `segment_words()`, `best_line_break()`, `generate_srt()` all read from `CONFIG` implicitly
- No way to run broadcast and social concurrently or from external code safely

**Target state**:
```
format_captions/
├── __init__.py          # Public API: format_srt(words, preset="broadcast") → str
├── core.py              # segment_words(), best_line_break(), generate_srt()
│                        #   — all accept a config dict as explicit parameter
├── presets.py           # PRESET_BROADCAST, PRESET_SOCIAL, WEAK_END_WORDS
├── models.py            # Word dataclass
└── cli.py               # CLI wrapper (python -m format_captions)
```

Key changes:
- All functions accept a `config: dict` parameter instead of reading the global
- `Word` dataclass moves to `models.py`
- Presets become importable constants
- `format_srt(words, preset="broadcast")` is the single public entry point
- CLI wrapper in `cli.py` calls the library API, preserving existing CLI behavior
- Architecture supports future language-specific presets (English weak words, etc.) but only Swedish is implemented now

#### 6.3.2 IR → Caption Formatter Adapter

The SRT caption formatter module in `soniox_converter/formatters/srt_captions.py` must transform the Transcript IR into the `Word` list that the caption library expects. This involves four non-trivial mappings:

**1. Timing conversion**:
```
Caption Word.start = AssembledWord.start_s
Caption Word.end   = AssembledWord.start_s + AssembledWord.duration_s
```

**2. Punctuation merging**: The converter IR keeps punctuation as separate tokens (`"today"` + `"?"`). The caption formatter expects punctuation attached to words (`"today?"`). The adapter must merge punctuation tokens onto the preceding word:
```
IR:      [Word("today", ...), Word("?", type="punctuation")]
Caption: [Word("today?", start=today.start, end=punctuation.end)]
```

Rules:
- Sentence-ending punctuation (`.`, `?`, `!`) and commas (`,`, `;`, `:`) merge onto the preceding word
- The merged word inherits the preceding word's `start` and the punctuation token's `end`
- Ellipsis (`…`) and em-dash (`—`) merge onto the preceding word

**3. Speaker change → em-dash injection**: The caption formatter uses `is_speaker_marker=True` on synthetic em-dash words to force caption breaks and trigger "– " prefixes. The converter IR uses speaker labels. The adapter detects speaker changes and injects em-dash markers:
```
IR:      [...words with speaker="1"..., ...words with speaker="2"...]
Caption: [...words..., Word("–", is_speaker_marker=True), ...words...]
```

An em-dash Word is injected before the first word of each new speaker (except the first speaker in the transcript). The em-dash inherits the timestamp of the following word.

**4. EOS → segment_start flip**: The converter IR marks the *last word* of a sentence with `eos=True`. The caption formatter marks the *first word* of a new sentence with `is_segment_start=True`. The adapter must shift the signal forward:
```
IR:      [..., Word("today", eos=True), Word("?", type="punctuation"), Word("I", ...)]
Caption: [..., Word("today?", ...), Word("I", is_segment_start=True, ...)]
```

After punctuation merging, scan the word list: any word following a merged sentence-ending punctuation gets `is_segment_start=True`.

### 6.5 Kinetic Word Reveal Formatter (Social Video)

Produces animated word-by-word captions for vertical social media video (TikTok, Reels, Shorts). Words pop onto screen one at a time at their spoken timestamps, grouped into "buckets" of up to 3 words that appear and clear together.

**Output**: Three separate Premiere Pro JSON files — one per row position. The editor imports all three as stacked caption tracks and positions them vertically. Premiere Pro handles the display; the timing in the JSON handles the animation.

**File naming**:
```
interview-kinetic-row1.json
interview-kinetic-row2.json
interview-kinetic-row3.json
```

**Single speaker only**: This format is designed for social clips with one presenter. The formatter ignores speaker segmentation and treats the entire transcript as one speaker.

#### 6.5.1 Bucketing Algorithm

Words are grouped into sentences (using EOS markers from the IR), then each sentence is divided into buckets:

1. Split sentence into groups of 3 words ("buckets"), left to right
2. The final bucket gets whatever remains: 1, 2, or 3 words
3. Within each bucket, words are assigned to rows: first word → row 1, second → row 2, third → row 3

**Examples**:

6 words → `[3, 3]`
5 words → `[3, 2]`
4 words → `[3, 1]`
7 words → `[3, 3, 1]`
8 words → `[3, 3, 2]`
2 words → `[2]`
1 word  → `[1]`

#### 6.5.2 Timing Rules

Each word has two timestamps: when it *appears* and when it *disappears*.

**Appear**: At the word's spoken timestamp from Soniox (`start_s`).

**Disappear**: All words in a bucket share the same end time, determined by:

1. **Normal case**: The next bucket's first word's `start_s` — the current bucket clears the instant the next bucket begins. Buckets never overlap.
2. **Last bucket in sentence**: The next sentence's first word's `start_s` (i.e., the next bucket in the *next* sentence clears it).
3. **Last bucket in transcript**: `start_s + duration_s` of the bucket's last word, plus a configurable hold (e.g., 1.5 seconds), capped at `max_hold`.
4. **Max hold cap**: If the gap to the next bucket exceeds `max_hold` (e.g., 3 seconds), the bucket clears at `last_word.start_s + max_hold` instead. Prevents "stuck" words during long pauses.

In Premiere Pro JSON terms:
```
word.start    = word's spoken start_s
word.duration = bucket_end_time - word.start_s
```

So the first word in a bucket has the longest duration (it appears first and disappears with everyone else), and the last word has the shortest.

#### 6.5.3 Example Walkthrough

Sentence: "Hello this is a new world!" — 6 words, spoken timestamps:

| Word | start_s | Bucket | Row |
|------|---------|--------|-----|
| Hello | 0.50 | 1 | 1 |
| this | 0.80 | 1 | 2 |
| is | 1.10 | 1 | 3 |
| a | 1.50 | 2 | 1 |
| new | 1.80 | 2 | 2 |
| world! | 2.10 | 2 | 3 |

Next sentence starts at 3.50s. Bucket 1 ends at 1.50 (when "a" appears). Bucket 2 ends at 3.50 (next sentence).

**Row 1 JSON** (words appearing in row position 1):
```
"Hello"  → start: 0.50, duration: 1.00  (1.50 - 0.50)
"a"      → start: 1.50, duration: 2.00  (3.50 - 1.50)
```

**Row 2 JSON** (words appearing in row position 2):
```
"this"   → start: 0.80, duration: 0.70  (1.50 - 0.80)
"new"    → start: 1.80, duration: 1.70  (3.50 - 1.80)
```

**Row 3 JSON** (words appearing in row position 3):
```
"is"     → start: 1.10, duration: 0.40  (1.50 - 1.10)
"world!" → start: 2.10, duration: 1.40  (3.50 - 2.10)
```

On screen over time:
```
0.50s   Hello                 ← word pops in
0.80s   Hello                 ← word pops in
        this
1.10s   Hello                 ← bucket 1 full
        this
        is
1.50s   a                     ← bucket 2 starts, bucket 1 gone
1.80s   a
        new
2.10s   a                     ← bucket 2 full
        new
        world!
3.50s   (next sentence starts)
```

#### 6.5.4 Premiere Pro JSON Structure

Each of the three row files is a valid Premiere Pro transcript JSON with:
- A single speaker
- One segment per bucket (containing just the one word that appears on that row)
- `word.type = "word"` (punctuation merged onto words, never standalone)
- `word.eos = true` on the last word of each sentence

For buckets with fewer than 3 words (sentence remainders), the missing row files simply have no segment for that bucket. Row 2 and Row 3 files will have gaps wherever a 1-word or 2-word bucket occurs.

#### 6.5.5 Punctuation Handling

Punctuation must be merged onto the preceding word before bucketing (same as SRT caption adapter — Section 6.3.2). Each bucket slot is one visual word with its punctuation attached: `"world!"` not `"world"` + `"!"`.

#### 6.5.6 Configurable Parameters

```python
KINETIC_CONFIG = {
    "max_bucket_size": 3,          # Words per bucket (could be 2 for very short-form)
    "max_hold_s": 3.0,             # Maximum time a bucket stays visible after last word
    "final_hold_s": 1.5,           # Hold time for the very last bucket in transcript
    "min_word_display_s": 0.15,    # Minimum display time for any word (prevents flicker)
}
```

### 6.6 Plain Text Formatter

Simple readable transcript with speaker labels and paragraph breaks at speaker changes.

```
Speaker 1:
How are you doing today?

Speaker 2:
I am fantastic, thank you.
```

**File extension**: `-transcript.txt`

### 6.7 Future Formatters (Not in Scope, But Anticipated)

These are formats the architecture should accommodate without changes to the core:

- **Sentence-level JSON**: Array of `{speaker, text, start, end}` objects, one per sentence
- **Compact JSON**: Minimal key names, no extra metadata, optimized for LLM context windows
- **WebVTT**: For web video players
- **Word-level JSON**: Raw IR serialized as JSON for custom downstream processing

---

## 7. Phase 1 — Local Desktop App

### 7.1 Tech Stack

- **Language**: Python 3.11+
- **GUI**: tkinter (native, no additional dependencies, cross-platform)
- **API key**: `.env` file in the app folder (`SONIOX_API_KEY=...`)
- **HTTP client**: `httpx` (async support for polling)
- **Platforms**: macOS (primary), Windows (secondary)

### 7.2 User Flow

```
┌─────────────────────────────────────────────────────────┐
│  1. LAUNCH                                              │
│     App opens. Main window shows:                       │
│     - Drop zone / "Browse" button                       │
│     - Language picker (primary + optional secondary)    │
│     - Diarization toggle (on by default)                │
│     - Output format checkboxes:                         │
│       [✓] Premiere Pro JSON                             │
│       [✓] SRT captions (broadcast)                      │
│       [ ] SRT captions (social)                         │
│       [ ] Kinetic word reveal                           │
│       [ ] Plain text                                    │
│     - "Transcribe" button (disabled until file chosen)  │
└──────────────────────┬──────────────────────────────────┘
                       │ User drops/picks file,
                       │ sets language, hits Transcribe
                       ▼
┌─────────────────────────────────────────────────────────┐
│  2. PROCESSING                                          │
│     Status area shows sequential steps:                 │
│     - "Uploading file..."              [████░░░░░░]     │
│     - "Transcription queued..."        [████░░░░░░]     │
│     - "Transcribing... (elapsed: 2m)"  [██████░░░░]     │
│     - "Converting to Premiere format..." [████████░░]   │
│     Cancel button available throughout.                 │
└──────────────────────┬──────────────────────────────────┘
                       │ Transcription completes
                       ▼
┌─────────────────────────────────────────────────────────┐
│  3. DONE                                                │
│     - ✓ "Saved 2 files to /path/to/output/"            │
│         interview-transcript.json                       │
│         interview-broadcast.srt                         │
│     - Speaker-labeled transcript preview:               │
│         Speaker 1: How are you doing today?             │
│         Speaker 2: I am fantastic, thank you.           │
│     - "Open Folder" button                              │
│     - "New Transcription" button (resets to step 1)     │
└─────────────────────────────────────────────────────────┘
```

### 7.3 Language Picker

Two dropdowns:

- **Primary language** (required): Swedish is the default. Full list of Soniox-supported languages.
- **Secondary language** (optional): "None" by default. When set, Soniox's `language_hints` is sent with both codes, and `enable_language_identification` is set to `true`.

When only a primary language is selected and no secondary, `enable_language_identification` may still be set to `true` to handle unexpected code-switching, but `language_hints` contains only the primary code.

### 7.4 Diarization Toggle

- On by default
- When on: `enable_speaker_diarization: true` in the Soniox request
- When off: all words assigned to a single default speaker ("Speaker 1")

### 7.5 File Handling

**Input**: Drag-drop or file picker. Accepted extensions: `.aac`, `.aiff`, `.amr`, `.flac`, `.mp3`, `.ogg`, `.wav`, `.webm`, `.m4a`, `.mp4`, `.asf`. The app validates the extension before proceeding.

**Output**: Saved to the same directory as the source file. Filename convention:

```
source:  interview_2026-02-13.mp4
outputs: interview_2026-02-13-transcript.json       (Premiere Pro)
         interview_2026-02-13-broadcast.srt          (SRT broadcast)
         interview_2026-02-13-social.srt             (SRT social)
         interview_2026-02-13-kinetic-row1.json      (Kinetic row 1)
         interview_2026-02-13-kinetic-row2.json      (Kinetic row 2)
         interview_2026-02-13-kinetic-row3.json      (Kinetic row 3)
         interview_2026-02-13-transcript.txt         (Plain text)
```

Rule: strip the source extension, append the formatter's suffix. If the output file already exists, append a numeric suffix: `-transcript-2.json`, etc.

### 7.6 Error Handling

| Error | User sees | Recovery |
|-------|-----------|----------|
| No API key in `.env` | "Soniox API key not configured. Add SONIOX_API_KEY to the .env file in the app folder." | Link to setup instructions |
| Unsupported file type | "This file type is not supported. Supported formats: ..." | Return to file selection |
| Upload fails (network) | "Upload failed. Check your internet connection and try again." | Retry button |
| Soniox returns error status | "Transcription failed: [error_message from API]" | Retry button |
| File too long (>300 min) | "File exceeds the 5-hour limit. Please use a shorter file." | Return to file selection |
| Output folder not writable | "Cannot save to this folder. Check permissions." | Offer to pick alternative location |

### 7.7 API Key Setup

On first launch, if no `.env` file exists or `SONIOX_API_KEY` is missing, the app shows a one-time setup prompt:

- Text field to paste the API key
- "Save" button that writes/updates the `.env` file
- Brief instruction text: "Get your API key at soniox.com/account"

---

## 8. Phase 2 — Slack Integration

### 8.1 Overview

A Slack bot watches a configurable channel (starting with one dedicated channel, e.g., `#transcripts`). When an audio/video file is dropped, the bot posts an interactive Block Kit form. The user configures options and submits. The bot processes the file and replies in the same thread with the output file(s) attached.

### 8.2 Architecture

```
┌──────────┐     ┌──────────────┐     ┌─────────────────────┐     ┌──────────┐
│  Slack   │────▶│  Slack Bot   │────▶│  Converter Service  │────▶│  Slack   │
│  (file   │     │  (event +    │     │  (same Python core  │     │  (thread │
│   drop)  │     │   Block Kit) │     │   as Phase 1)       │     │   reply) │
└──────────┘     └──────────────┘     └─────────────────────┘     └──────────┘
```

The converter core (`api/`, `core/`, `formatters/`) is identical in both phases. Phase 2 adds a Slack event handler and a thin HTTP wrapper. The service is self-contained and works locally — n8n Cloud is an optional orchestration layer, not a dependency.

### 8.3 Slack Flow

**Step 1 — File detected**: Bot receives `file_shared` event in the watched channel. Bot validates the file type. If unsupported, bot replies with a brief error in thread.

**Step 2 — Configuration form**: Bot posts a Block Kit message in a thread on the original file message:

```
┌─────────────────────────────────────────────┐
│  🎙 Transcription Settings                  │
│                                             │
│  Primary language:    [Swedish ▾]           │
│  Secondary language:  [English ▾]           │
│  Speaker diarization: [✓ On]                │
│  Expected speakers:   [Auto-detect ▾]       │
│                                             │
│  Output formats:                            │
│  [✓] Premiere Pro JSON                      │
│  [✓] SRT captions (broadcast 16:9)          │
│  [ ] SRT captions (social 9:16)             │
│  [ ] Kinetic word reveal (3-row social)     │
│  [ ] Plain text                             │
│                                             │
│  [Transcribe]                               │
└─────────────────────────────────────────────┘
```

**Smart defaults**: Swedish + English, diarization on, auto-detect speakers, Premiere Pro JSON + SRT checked. Most of the time users just hit "Transcribe."

**Step 3 — Processing**: Bot updates the message with status:
- "⏳ Uploading to Soniox..."
- "⏳ Transcribing... (elapsed: 2m 15s)"
- "⏳ Converting..."

**Step 4 — Delivery**: Bot replies in the thread with the generated file(s) attached. One file per selected output format. Brief summary message:

> ✅ Transcription complete (3m 24s processing time)
> • 2 speakers detected
> • 847 words, 4m 12s audio
> • Swedish (primary), English (secondary)

### 8.4 Deployment

The converter service is a standalone Python application that exposes an HTTP endpoint. It can run:

- **Locally** for development and testing
- **On any server/VM** as a persistent service
- **Behind n8n Cloud** as a webhook-triggered workflow step

The Slack bot component can be a separate lightweight process or integrated into the same service. When running behind n8n, n8n handles the Slack event subscription and calls the converter service's HTTP endpoint.

### 8.5 File Size Considerations

Slack file uploads can be large (up to 1GB on paid plans). The bot should:

- Download the file from Slack's CDN using the provided `url_private`
- Upload it to Soniox (or pass a URL if Soniox can fetch from Slack — likely not, so download-then-upload)
- Clean up temporary files after processing
- Reply with output files attached (transcript JSONs are small, typically <1MB)

---

## 9. Soniox API Integration

### 9.1 Authentication

```
Base URL: https://api.soniox.com/v1
Header:   Authorization: Bearer <SONIOX_API_KEY>
```

### 9.2 Async Workflow

```
Step 1:  POST /v1/files                         → Upload audio file, get file_id
Step 2:  POST /v1/transcriptions                 → Create transcription job
Step 3:  GET  /v1/transcriptions/{id}            → Poll until status == "completed"
Step 4:  GET  /v1/transcriptions/{id}/transcript  → Fetch result (flat token array)
Step 5:  DELETE /v1/transcriptions/{id}           → Clean up (and DELETE /v1/files/{id})
```

### 9.3 Create Transcription Request

```json
{
  "model": "stt-async-v4",
  "file_id": "<uuid-from-upload>",
  "language_hints": ["sv", "en"],
  "enable_speaker_diarization": true,
  "enable_language_identification": true
}
```

- **Model**: Always `stt-async-v4` (latest, released Jan 29, 2026)
- **`language_hints`**: Populated from the user's language picker selections
- **`enable_speaker_diarization`**: Matches the user's diarization toggle
- **`enable_language_identification`**: `true` whenever a secondary language is selected; recommended `true` even with single language for robustness

### 9.4 Polling Strategy

Poll `GET /v1/transcriptions/{id}` with exponential backoff:

- Initial interval: 2 seconds
- Maximum interval: 15 seconds
- Backoff factor: 1.5×
- Timeout: 60 minutes (for very long files)

Status progression: `"queued"` → `"processing"` → `"completed"` (or `"error"`)

Update the UI status text at each poll response.

### 9.5 Response Shape

The transcript endpoint returns:

```json
{
  "id": "uuid",
  "text": "full plaintext transcript",
  "tokens": [
    {
      "text": "How",
      "start_ms": 120,
      "end_ms": 250,
      "confidence": 0.97,
      "speaker": "1",
      "language": "en"
    }
  ]
}
```

Key facts:

- `tokens` is a flat array — no segments, sentences, or groupings
- `speaker` and `language` fields are only present when the corresponding feature is enabled
- Sub-word tokens (BPE): words like "fantastic" arrive as `[" fan", "tastic"]`
- Punctuation arrives as separate tokens: `"."`, `","`, `"?"`
- There is no `is_final` field (that's realtime-only)
- Translation tokens may be interleaved if translation was configured — filter them out

### 9.6 Cleanup

After successful conversion, delete both the transcription and the uploaded file:

```
DELETE /v1/transcriptions/{id}
DELETE /v1/files/{file_id}
```

Soniox has storage limits (10GB files, 2000 transcriptions). Always clean up.

---

## 10. Configuration

### 10.1 `.env` File

```env
SONIOX_API_KEY=your-api-key-here

# Optional overrides
SONIOX_BASE_URL=https://api.soniox.com/v1
SONIOX_MODEL=stt-async-v4
DEFAULT_PRIMARY_LANGUAGE=sv
DEFAULT_SECONDARY_LANGUAGE=en
DEFAULT_DIARIZATION=true
```

### 10.2 Language Configuration

The `config.py` module maintains:

- `LANGUAGE_MAP`: ISO 639-1 → BCP-47 mapping (Section 5.6)
- `PREMIERE_PRO_LANGUAGES`: Set of valid Premiere Pro language codes (from the schema's `LanguageCode` enum)
- `SONIOX_SUPPORTED_FORMATS`: Set of accepted audio/video file extensions

All mappings are defined as plain data structures (dicts/lists), not buried in logic, so they're easy for both humans and coding agents to update.

---

## 11. Testing Strategy

### 11.1 Unit Tests

- **Token assembly**: Given a flat token array, verify correct word assembly, punctuation classification, EOS inference, and speaker grouping. Use the verified examples from the Soniox API reference document.
- **Timestamp conversion**: Verify ms → seconds conversion and duration calculation.
- **Confidence aggregation**: Verify min-based aggregation across sub-word tokens.
- **Language mapping**: Verify all mapped codes, and that unmapped codes fall back to `"??-??"`.
- **Each formatter**: Given a known Transcript IR, verify the output matches the expected format exactly.

### 11.2 Integration Tests

- **Soniox API round-trip**: Upload a known audio file, create transcription, poll to completion, fetch transcript, verify token structure matches expectations.
- **End-to-end**: Audio file → Premiere Pro JSON → validate against the Premiere Pro JSON schema.

### 11.3 Validation

The Premiere Pro JSON formatter should validate its output against the Premiere Pro schema (`PremierePro_transcript_format_spec.json`) before writing to disk. Any validation failure is a bug in the formatter.

---

## 12. Reference Documents

These documents are the ground truth for implementation:

| Document | Contains | Location |
|----------|----------|----------|
| `soniox_async_api_reference.md` | Complete Soniox async API schema, token assembly rules, field mapping, verified examples | Project file |
| `PremierePro_transcript_format_spec.json` | Premiere Pro Audio Transcript JSON schema (JSON Schema draft-07) | Project file |
| `format_captions.py` | Existing Swedish SDH caption formatter — standalone CLI, to be refactored into library | Project file |
| `format_captions_llm_documentation.md` | LLM-optimized docs for the caption formatter: architecture, data structures, scoring weights, edge cases | Project file |
| Soniox official docs | API reference, guides, examples | `soniox.com/docs/llms-full.txt` |

---

## 13. Resolved Design Decisions

These questions were open in v1.0 and have been resolved:

| # | Question | Resolution |
|---|----------|------------|
| 1 | Segment splitting | Premiere Pro segments split at **sentence boundaries** (EOS), not silence gaps. No configurable silence threshold needed. |
| 2 | File extension | `-transcript.json` (hyphen, not dot-separated). Avoids double-extension compatibility issues. |
| 3 | SRT formatter | **No standalone SRT formatter.** All SRT output goes through the caption formatting library with DP segmentation and Swedish linguistic heuristics. |
| 4 | n8n Cloud timeout | 40-minute max execution. n8n is a thin dispatcher only; converter runs independently. |
| 5 | Slack file access | Bot downloads file with token (`files:read` scope), re-uploads to Soniox. No direct Soniox→Slack URL access. |
| 6 | Caption lib packaging | Tightly integrated separate modules, same repo. Easily portable, independently usable, no version drift. |
| 7 | Punctuation merge | Merge consecutive punctuation onto preceding word. Cap at 3 consecutive merges. Handle obvious edge cases in first implementation; address others as they emerge from real transcripts. |

---

## 14. Implementation Sequence

### Phase 1 — Local App

1. **Core first**: `api/`, `core/` (assembler, segmenter, IR) — the pipeline without any UI
2. **Premiere Pro formatter**: First output formatter, validated against the JSON schema
3. **CLI**: Command-line interface for testing and power-user access
4. **Plain text formatter**: Simple second formatter to prove the pluggable pattern works
5. **Kinetic word reveal formatter**: Three-file Premiere JSON output for social video (Section 6.5) — reuses Premiere JSON structure, exercises multi-file output pattern
6. **Caption formatter refactor**: Extract `format_captions.py` into a library package with clean API (Section 6.3.1)
7. **SRT caption formatter + adapter**: Build the IR → caption Word adapter (Section 6.3.2), wire up broadcast and social presets
8. **GUI**: Tkinter app wrapping the CLI functionality
9. **Testing and polish**: Schema validation, error handling, edge cases

### Phase 2 — Slack Integration

10. **HTTP wrapper**: FastAPI (or similar) around the converter core, with OpenAPI auto-generation
11. **Slack bot**: Event subscription, Block Kit form, file download, thread replies
12. **n8n integration**: Webhook-triggered workflow calling the converter HTTP endpoint
13. **Deployment**: Containerize, deploy, monitor
