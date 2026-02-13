# format_captions.py — LLM Agent Documentation

> **Audience:** This documentation is written exclusively for LLM coding agents. It assumes you can parse code, understand algorithms, and need dense, precise information rather than tutorials or explanations of basic concepts.

---

## 1. Executive Summary

**What it does:** Converts word-level timestamped JSON transcripts → SRT subtitle files with optimized line breaks and caption segmentation for Swedish SDH (Subtitles for the Deaf and Hard of Hearing).

**Core algorithm:** Dynamic programming to find globally optimal caption boundaries, with scoring based on Swedish linguistic heuristics.

**Two format presets:**
- `broadcast`: 2 lines × 42 chars (TV/web 16:9)
- `social`/`some`: 1 line × 25 chars (vertical 9:16)

**Key constraint:** Text is sacred — never modify, paraphrase, or reorder words. Only decide where to break.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         PIPELINE                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  JSON Input ──► parse_input() ──► List[Word]                    │
│                                      │                           │
│                                      ▼                           │
│                            segment_words()                       │
│                                      │                           │
│                    ┌─────────────────┴─────────────────┐        │
│                    │    For each candidate segment:     │        │
│                    │    best_line_break() ──► score    │        │
│                    │    compute_segment_cost() ──► cost │        │
│                    └─────────────────┬─────────────────┘        │
│                                      │                           │
│                                      ▼                           │
│                              DP backtrack                        │
│                                      │                           │
│                                      ▼                           │
│                            List[Dict] segments                   │
│                                      │                           │
│                                      ▼                           │
│                            generate_srt()                        │
│                                      │                           │
│                                      ▼                           │
│                              SRT string output                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Data Structures

### 3.1 Word (dataclass)

```python
@dataclass
class Word:
    text: str                      # The word text (never modify)
    start: float                   # Start time in seconds
    end: float                     # End time in seconds
    is_speaker_marker: bool        # True if text is "–", "-", or "—"
    is_segment_start: bool         # True if first real word in source segment
```

**Usage notes:**
- `is_speaker_marker` words are NOT included in caption text; they trigger "– " prefix
- `is_segment_start` is used for DP scoring (prefer breaking at segment boundaries)

### 3.2 CONFIG (global dict)

```python
CONFIG = {
    # Hard limits
    "max_lines": int,              # 1 for social, 2 for broadcast
    "max_line_chars": int,         # 25 for social, 42 for broadcast
    "max_cue_chars": int,          # max_lines × max_line_chars
    
    # Soft targets
    "target_line_chars": int,      # Ideal line length
    "target_cue_chars": int,       # Ideal caption length
    "prefer_split_over": int,      # Single lines longer than this get split penalty
    "min_line_chars": int,         # Lines shorter than this get orphan penalty
    
    # Timing
    "target_cps": float,           # Target characters per second
    "max_cps": float,              # Maximum acceptable CPS
    "min_cue_dur": float,          # Minimum caption duration (seconds)
    "max_cue_dur": float,          # Maximum caption duration (seconds)
    "min_display_dur": float,      # Minimum display time (enforced in output)
    
    # Algorithm
    "max_lookback_words": int,     # Max words to consider per segment in DP
    
    # Scoring weights (see Section 6)
    "weights": Dict[str, float]
}
```

**Critical:** CONFIG is a module-level global. The `main()` function sets it via `global CONFIG` based on `--format` argument. All functions read from CONFIG directly.

### 3.3 Segment Dict (output of segmentation)

```python
{
    "text": str,           # Raw text (for reference)
    "start": float,        # Start timestamp
    "end": float,          # End timestamp
    "formatted": str,      # Text with \n inserted for line breaks
    "lines": List[str],    # Split lines
    "has_speaker": bool    # Whether segment has speaker marker prefix
}
```

---

## 4. Function Reference

### 4.1 Input Parsing

#### `try_parse_json(raw: str) -> Any`

**Purpose:** Parse JSON with fallback for incomplete input.

**Algorithm:**
1. Normalize line endings (CRLF → LF)
2. Try direct `json.loads()`
3. If fails, strip trailing comma
4. Try appending closing brackets: `]`, `}]`, `}]}`, `]}`, `]}}`, `]}]`
5. Raise `ValueError` if all attempts fail

**Why this exists:** Input files may be snippets from larger files with missing closing brackets.

#### `parse_input(data: Any) -> List[Word]`

**Purpose:** Convert parsed JSON to flat word list.

**Accepts two input shapes:**

1. **Segments with nested words:**
```json
[{"words": [{"word": "text", "start": 0.0, "end": 0.5}]}]
```

2. **Flat word list:**
```json
[{"word": "text", "start": 0.0, "end": 0.5}]
```

**Field name flexibility:** Accepts `word`/`text`/`t` for text, `start`/`s` for start time, `end`/`e` for end time.

**Speaker marker detection:** Words with text `–`, `-`, or `—` get `is_speaker_marker=True`.

**Segment boundary tracking:** First non-marker word in each segment gets `is_segment_start=True`.

---

### 4.2 Text Utilities

#### `visible_len(s: str) -> int`

Returns character count after stripping HTML/XML tags. Used for all length checks.

**Important:** This counts Unicode characters, not bytes. Swedish `ä` = 1 char (2 bytes in UTF-8).

#### `strip_tags(s: str) -> str`

Removes `<...>` tags. Pattern: `r"<[^>]+>"`

#### `strip_punct(w: str) -> str`

Removes leading `"'(` and trailing `.,!?…:;)]"'` from a word.

#### `last_word_clean(line: str) -> str`

Returns the last word of a line, lowercased, with punctuation stripped. Used for weak word detection.

#### `ends_sentence(line: str) -> bool`

True if line ends with `.`, `!`, `?`, or `…`

#### `ends_comma(line: str) -> bool`

True if line ends with `,`, `;`, or `:`

---

### 4.3 Line Breaking

#### `best_line_break(text: str, start: float, end: float) -> Dict[str, Any]`

**Purpose:** Find optimal 1 or 2 line layout for a caption.

**Input:**
- `text`: Caption text (will be whitespace-normalized)
- `start`, `end`: Timestamps for CPS calculation

**Output:**
```python
{
    "ok": bool,            # False if no valid layout found
    "formatted": str,      # Text with \n if 2-line
    "lines": List[str],    # 1 or 2 lines
    "score": float,        # Lower is better
    "break_at": int|None   # Word index for break (None = single line)
}
```

**Algorithm:**
1. Normalize whitespace, split into words
2. Generate single-line candidate if `visible_len(text) <= max_line_chars`
3. If `CONFIG["max_lines"] >= 2`: generate two-line candidates for each word boundary
4. Filter candidates where any line exceeds `max_line_chars`
5. Score each candidate
6. Return candidate with lowest score

**Constraint enforcement:** `CONFIG["max_lines"]` is respected — social format never generates 2-line candidates.

#### `score_single_line(text: str, start: float, end: float) -> float`

Scoring factors:
- `len_deviation × |length - target_line_chars|`
- `single_line_long × max(0, length - prefer_split_over)`
- CPS penalties if above target/max

#### `score_two_lines(line1: str, line2: str, full_text: str, start: float, end: float) -> float`

Scoring factors:
- `len_deviation × (|len1 - target| + |len2 - target|)`
- `balance × |len1 - len2|`
- `orphan × max(0, min_line_chars - min(len1, len2))`
- `weak_end` if line1 ends with weak word
- `short_end` if line1 ends with 1-2 char word
- `punct_bonus` (negative) if line1 ends with sentence punctuation
- `comma_bonus` (negative) if line1 ends with comma/colon/semicolon
- CPS penalties

---

### 4.4 Segmentation (Dynamic Programming)

#### `segment_words(words: List[Word]) -> List[Dict[str, Any]]`

**Purpose:** Find globally optimal caption boundaries using DP.

**Algorithm:**

```
dp[j] = minimum cost to segment words[0:j]
back[j] = starting index of last segment ending at j
info[j] = segment info for backtracking

Initialize: dp[0] = 0

For j = 1 to N:
    For i = j-1 down to max(0, j - max_lookback_words):
        Skip if crosses forced break (speaker marker)
        Build segment text from words[i:j]
        Skip if exceeds max_cue_chars
        Compute line break and segment cost
        If dp[i] + cost < dp[j]:
            dp[j] = dp[i] + cost
            back[j] = i
            info[j] = segment_info

Backtrack from j=N to reconstruct segments
```

**Forced breaks:** Speaker markers (`is_speaker_marker=True`) create mandatory boundaries. The algorithm tracks these and never allows segments to cross them.

**Preferred breaks:** Segment boundaries (`is_segment_start=True`) get a -2.0 cost bonus.

**Fallback:** If `dp[N]` is infinite (no valid path), calls `greedy_segment()`.

#### `compute_segment_cost(text: str, start: float, end: float, lb: Dict, has_speaker: bool) -> float`

**Inputs:**
- `text`: Segment text
- `start`, `end`: Timestamps
- `lb`: Result from `best_line_break()`
- `has_speaker`: Whether segment starts with speaker marker

**Cost components:**
- `lb["score"]` (line break cost)
- `cue_len_deviation × |char_count - target_cue_chars|`
- `cue_dur_below × max(0, min_cue_dur - duration)`
- `cue_dur_above × max(0, duration - max_cue_dur)`
- Boundary quality:
  - `boundary_punct_bonus` if ends with sentence punctuation
  - `boundary_punct_bonus × 0.3` if ends with comma
  - `boundary_weak_end` if ends with weak word (no punct)
  - `boundary_no_punct` if ends without any punctuation
- `speaker_change_bonus` if `has_speaker`

**Additional penalties in `segment_words()`:**
- +2.0 if duration < min_cue_dur and not final segment
- +1.5 if text < 35 chars and not final segment (straggler prevention)
- -2.0 bonus if next word is segment start (sentence boundary alignment)
- +1.0 if mid-sentence break without punctuation

#### `greedy_segment(words: List[Word]) -> List[Dict[str, Any]]`

**Purpose:** Fallback when DP fails to find valid path.

**Algorithm:** From each position, greedily extend segment as far as valid, then move to next position.

**When it triggers:** Extremely rare — only if constraints are so tight no valid segmentation exists.

---

### 4.5 SRT Generation

#### `seconds_to_srt_time(seconds: float) -> str`

Converts `123.456` → `"00:02:03,456"`

Format: `HH:MM:SS,mmm`

#### `generate_srt(segments: List[Dict[str, Any]]) -> str`

**Purpose:** Convert segments to SRT string.

**Timing adjustments:**
1. Enforce minimum display duration: `end = max(end, start + min_display_dur)`
2. Prevent overlap: `end = min(end, next_start - 0.05)`

**Output format:**
```
{index}
{start} --> {end}
{formatted_text}

```

---

## 5. Configuration Presets

### 5.1 PRESET_BROADCAST

```python
{
    "max_lines": 2,
    "max_line_chars": 42,
    "max_cue_chars": 84,
    "target_line_chars": 32,
    "prefer_split_over": 36,
    "min_line_chars": 12,
    "target_cps": 13.0,
    "max_cps": 17.3,
    "target_cue_chars": 50,
    "min_cue_dur": 1.5,
    "max_cue_dur": 7.0,
    "min_display_dur": 1.2,
    "max_lookback_words": 18,
    "weights": {
        "len_deviation": 0.20,
        "balance": 0.12,
        "orphan": 2.5,
        "weak_end": 8.0,
        "short_end": 1.5,
        "punct_bonus": -2.5,
        "comma_bonus": -1.2,
        "single_line_long": 1.2,
        "cps_above_target": 0.8,
        "cps_above_max": 3.0,
        "cue_len_deviation": 0.08,
        "cue_dur_below": 2.5,
        "cue_dur_above": 0.5,
        "boundary_weak_end": 4.0,
        "boundary_punct_bonus": -3.5,
        "boundary_no_punct": 2.0,
        "speaker_change_bonus": -5.0,
    }
}
```

### 5.2 PRESET_SOCIAL

```python
{
    "max_lines": 1,
    "max_line_chars": 25,
    "max_cue_chars": 25,
    "target_line_chars": 18,
    "prefer_split_over": 18,
    "min_line_chars": 6,
    "target_cps": 12.0,
    "max_cps": 15.0,
    "target_cue_chars": 16,
    "min_cue_dur": 0.8,
    "max_cue_dur": 3.5,
    "min_display_dur": 0.6,
    "max_lookback_words": 6,
    "weights": {
        "len_deviation": 0.15,
        "balance": 0.0,
        "orphan": 2.0,
        "weak_end": 5.0,
        "short_end": 0.8,
        "punct_bonus": -3.5,
        "comma_bonus": -2.0,
        "single_line_long": 3.0,
        "cps_above_target": 1.0,
        "cps_above_max": 4.0,
        "cue_len_deviation": 0.10,
        "cue_dur_below": 1.5,
        "cue_dur_above": 1.0,
        "boundary_weak_end": 4.0,
        "boundary_punct_bonus": -4.0,
        "boundary_no_punct": 1.5,
        "speaker_change_bonus": -4.0,
    }
}
```

---

## 6. Scoring Weight Reference

| Weight | Type | Effect | Typical Range |
|--------|------|--------|---------------|
| `len_deviation` | Per-char | Cost per char away from target | 0.1–0.4 |
| `balance` | Per-char | Cost per char difference between lines | 0.0–0.2 |
| `orphan` | Per-char | Cost per char below min_line_chars | 1.5–3.0 |
| `weak_end` | Fixed | Penalty for weak word at line 1 end | 5.0–10.0 |
| `short_end` | Fixed | Penalty for 1-2 char word at line 1 end | 0.5–2.0 |
| `punct_bonus` | Fixed | Bonus (negative) for sentence punct break | -1.0–-3.0 |
| `comma_bonus` | Fixed | Bonus (negative) for comma break | -0.5–-2.0 |
| `single_line_long` | Per-char | Cost per char over prefer_split_over | 1.0–3.0 |
| `cps_above_target` | Per-CPS | Cost per CPS above target | 0.5–1.5 |
| `cps_above_max` | Per-CPS | Additional cost per CPS above max | 2.0–5.0 |
| `cue_len_deviation` | Per-char | Cost per char away from target_cue_chars | 0.05–0.2 |
| `cue_dur_below` | Per-sec | Cost per second below min_cue_dur | 1.5–3.0 |
| `cue_dur_above` | Per-sec | Cost per second above max_cue_dur | 0.3–1.0 |
| `boundary_weak_end` | Fixed | Penalty for segment ending with weak word | 3.0–6.0 |
| `boundary_punct_bonus` | Fixed | Bonus for segment ending with punct | -2.0–-5.0 |
| `boundary_no_punct` | Fixed | Penalty for segment ending without punct | 1.0–3.0 |
| `speaker_change_bonus` | Fixed | Bonus for segment with speaker marker | -3.0–-6.0 |

**Tuning guidance:**
- Higher `weak_end` = more aggressive avoidance of weak line endings
- More negative `boundary_punct_bonus` = stronger preference for sentence-aligned segments
- Lower `max_lookback_words` = shorter segments (important for social format)
- Balance `cue_len_deviation` vs `boundary_punct_bonus` to control sentence-splitting behavior

---

## 7. Swedish Weak Words

```python
WEAK_END_WORDS = {
    # Conjunctions
    "och", "att", "som", "men", "eller", "utan", "eftersom", "medan",
    
    # Prepositions
    "i", "på", "av", "för", "med", "till", "om", "från", "kring", "mot", "via",
    "under", "över", "mellan", "innan", "efter", "trots",
    
    # Temporal/causal
    "när", "då", "så",
    
    # Pronouns/articles
    "det", "de", "den", "detta", "dessa", "man", "vi", "jag", "du",
    "han", "hon", "ni", "en", "ett", "där", "här", "ju",
    
    # Common verbs
    "är", "var", "blir", "ska", "kan", "har", "hade", "får", "vill", "kommer", "inte"
}
```

**Logic:** Ending a line with these words creates an incomplete feeling. The algorithm penalizes this to produce more natural line breaks.

---

## 8. Edge Cases & Gotchas

### 8.1 Long Compound Words

Swedish compounds like "Skellefteåföretaget" (19 chars) or "Northvolt-konkursen" (19 chars) significantly constrain line break options. The algorithm handles this but may produce suboptimal results.

**Symptom:** Unbalanced lines or forced weak-word endings.

**No workaround:** SDH standards prohibit hyphenation.

### 8.2 Speaker Markers in Word Stream

Em-dashes appear as separate "words" in the input:
```json
{"word": "–", "start": 0.5, "end": 0.55}
```

**Handling:**
- Detected by `is_speaker_marker` flag
- Creates forced break in DP
- NOT included in caption text
- Triggers "– " prefix in output

### 8.3 UTF-8 Character Counting

**Correct:** `len("stämningen") = 10`  
**Incorrect:** `len("stämningen".encode()) = 12`

All length checks use `visible_len()` which counts characters, not bytes.

### 8.4 Incomplete JSON

Input may have missing closing brackets:
```json
[{"words": [...]}
```

The `try_parse_json()` function handles this by trying bracket completions.

### 8.5 Timing Overlaps

If word timestamps are imprecise, adjacent captions might overlap. `generate_srt()` enforces `end = min(end, next_start - 0.05)` to prevent this.

### 8.6 Very Short Final Segments

The algorithm applies straggler penalties (+1.5 for short segments) but exempts the final segment. This prevents forcing short final words into oversized previous segments.

---

## 9. Common Modifications

### 9.1 Adding a New Format Preset

```python
PRESET_CUSTOM = {
    # Copy from closest existing preset
    **PRESET_BROADCAST,
    # Override specific values
    "max_line_chars": 38,
    "target_line_chars": 30,
}
PRESET_CUSTOM["weights"] = {
    **PRESET_BROADCAST["weights"],
    "weak_end": 10.0,  # Override specific weight
}

PRESETS["custom"] = PRESET_CUSTOM
```

### 9.2 Adding Weak Words

```python
WEAK_END_WORDS.add("någon")
WEAK_END_WORDS.add("något")
```

### 9.3 Changing Line Break Behavior

To prefer single lines more strongly:
```python
CONFIG["weights"]["single_line_long"] = 0.5  # Reduce penalty
CONFIG["prefer_split_over"] = 40  # Increase threshold
```

To prefer two lines more strongly:
```python
CONFIG["weights"]["single_line_long"] = 3.0  # Increase penalty
CONFIG["prefer_split_over"] = 30  # Lower threshold
```

### 9.4 Adjusting Segment Length

For shorter segments:
```python
CONFIG["max_lookback_words"] = 10
CONFIG["target_cue_chars"] = 35
CONFIG["max_cue_dur"] = 4.0
```

For longer segments:
```python
CONFIG["max_lookback_words"] = 25
CONFIG["target_cue_chars"] = 70
CONFIG["max_cue_dur"] = 8.0
```

### 9.5 Disabling Segment Boundary Preference

In `segment_words()`, remove or comment out:
```python
# Bonus if this segment ends at a preferred break point
if j < N and words[j].is_segment_start:
    cost -= 2.0  # Remove this line
```

### 9.6 Adding a New Input Format

In `parse_input()`, add detection logic:
```python
# Example: handle {"tokens": [...]} format
if "tokens" in item and isinstance(item["tokens"], list):
    for t in item["tokens"]:
        text = t.get("value", "")
        # ... etc
```

---

## 10. Testing Approaches

### 10.1 Unit Test Key Functions

```python
def test_visible_len():
    assert visible_len("hello") == 5
    assert visible_len("hallå") == 5  # Swedish å = 1 char
    assert visible_len("<b>hello</b>") == 5  # Tags stripped

def test_last_word_clean():
    assert last_word_clean("hello world.") == "world"
    assert last_word_clean("Skellefteå,") == "skellefteå"

def test_weak_word_detection():
    assert "och" in WEAK_END_WORDS
    assert "Northvolt" not in WEAK_END_WORDS
```

### 10.2 Integration Test Segmentation

```python
def test_broadcast_segmentation():
    CONFIG.update(PRESET_BROADCAST)
    CONFIG["weights"] = PRESET_BROADCAST["weights"].copy()
    
    words = [Word("Hello", 0, 0.5, False, True), ...]
    segments = segment_words(words)
    
    for seg in segments:
        for line in seg["lines"]:
            assert visible_len(line) <= 42

def test_social_single_line():
    CONFIG.update(PRESET_SOCIAL)
    CONFIG["weights"] = PRESET_SOCIAL["weights"].copy()
    
    words = [...]
    segments = segment_words(words)
    
    for seg in segments:
        assert len(seg["lines"]) == 1
        assert visible_len(seg["lines"][0]) <= 25
```

### 10.3 Regression Test with Golden Output

```python
def test_golden_output():
    with open("test_input.json") as f:
        data = json.load(f)
    
    words = parse_input(data)
    segments = segment_words(words)
    srt = generate_srt(segments)
    
    with open("expected_output.srt") as f:
        expected = f.read()
    
    assert srt == expected
```

---

## 11. Performance Characteristics

**Time complexity:** O(N × W²) where N = word count, W = max_lookback_words

**Space complexity:** O(N) for DP arrays

**Typical performance:**
- 100 words: <10ms
- 1000 words: ~100ms
- 10000 words: ~1-2s

**Bottleneck:** The nested loop in `segment_words()` dominates. Reducing `max_lookback_words` improves performance linearly.

---

## 12. CLI Interface

```
python3 format_captions.py [input] [output] [--format FORMAT]

Arguments:
  input           Input JSON file path, or "-" for stdin
  output          Output SRT file path, or omit for stdout
  --format FORMAT One of: broadcast (default), social, some

Examples:
  python3 format_captions.py input.json output.srt
  python3 format_captions.py input.json output.srt --format social
  python3 format_captions.py input.json --format=some
  cat input.json | python3 format_captions.py - output.srt
  python3 format_captions.py --help
```

**Exit codes:**
- 0: Success
- 1: Error (parse failure, no words, segmentation failed)

**Stderr:** Progress messages (e.g., "Wrote 43 captions (SoMe format) to output.srt")

**Stdout:** SRT content (if no output file specified)

---

## 13. Dependencies

**None.** Pure Python 3.9+ standard library only.

Used modules:
- `json` — JSON parsing
- `math` — `inf` for DP initialization
- `re` — Regex for text processing
- `sys` — CLI args and I/O
- `dataclasses` — Word dataclass
- `typing` — Type hints

---

## 14. File Statistics

- **Lines:** ~730
- **Functions:** 18
- **Classes:** 1 (Word dataclass)
- **Global constants:** 4 (PRESET_BROADCAST, PRESET_SOCIAL, PRESETS, WEAK_END_WORDS)
- **Global mutable:** 1 (CONFIG)

---

## 15. Modification Checklist

When modifying this script, verify:

- [ ] `CONFIG["max_lines"]` is respected in `best_line_break()`
- [ ] `CONFIG["max_line_chars"]` is checked in all candidates
- [ ] `CONFIG["max_cue_chars"]` is checked in `segment_words()`
- [ ] Speaker markers create forced breaks
- [ ] Segment boundaries get preference bonus
- [ ] All length checks use `visible_len()`, not `len()`
- [ ] No text content is modified, only structure
- [ ] Timing adjustments in `generate_srt()` prevent overlaps
- [ ] Greedy fallback handles edge cases
- [ ] UTF-8 encoding is preserved in output
