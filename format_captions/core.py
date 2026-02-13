"""Core caption formatting logic: segmentation, line breaking, and SRT generation.

WHY: This module contains the entire caption formatting pipeline — from a flat
list of timestamped words to a finished SRT string. The algorithm uses dynamic
programming to find globally optimal caption boundaries for Swedish SDH
subtitles, with scoring heuristics tuned for readability.

HOW: The pipeline has three stages:
  1. segment_words() — DP-based segmentation that groups words into caption blocks,
     respecting speaker changes, sentence boundaries, and timing constraints.
  2. best_line_break() — For each caption block, finds the optimal 1- or 2-line
     layout by scoring all possible word-boundary splits.
  3. generate_srt() — Converts the segmented blocks into SRT format with proper
     timestamps and overlap prevention.

RULES:
- ALL functions accept an explicit `config` dict parameter — no global state.
  This makes concurrent calls with different presets safe.
- Text content is never modified — only structure (line breaks, caption boundaries).
- All length checks use visible_len() to ignore HTML/XML tags.
- Speaker markers (em-dashes) force caption boundaries and trigger "– " prefixes.
- WEAK_END_WORDS is passed explicitly to functions that need it.
"""

import json
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import Word
from .presets import WEAK_END_WORDS

# =============================================================================
# Text Utilities
# =============================================================================

TAG_RE = re.compile(r"<[^>]+>")
SENT_PUNCT_RE = re.compile(r"[.!?…]$")
COMMA_RE = re.compile(r"[,;:]$")


def strip_tags(s: str) -> str:
    """Remove HTML/XML tags from a string."""
    return TAG_RE.sub("", s)


def visible_len(s: str) -> int:
    """Return character count after stripping tags. Used for all length checks."""
    return len(strip_tags(s))


def strip_punct(w: str) -> str:
    """Remove leading/trailing punctuation from a word for clean comparison."""
    return re.sub(r'^[""\'(]+|[.,!?…:;)\]""\']+$', "", w)


def last_word_clean(line: str) -> str:
    """Return the last word of a line, lowercased, with punctuation stripped.

    Used for weak-word detection at line break points.
    """
    parts = strip_tags(line).strip().split()
    for w in reversed(parts):
        w = strip_punct(w).lower()
        if w:
            return w
    return ""


def ends_sentence(line: str) -> bool:
    """True if line ends with sentence punctuation (. ! ? …)."""
    return bool(SENT_PUNCT_RE.search(strip_tags(line).strip()))


def ends_comma(line: str) -> bool:
    """True if line ends with comma-class punctuation (, ; :)."""
    return bool(COMMA_RE.search(strip_tags(line).strip()))


# =============================================================================
# Input Parsing
# =============================================================================

def parse_input(data: Any) -> List[Word]:
    """Parse input JSON into a flat word list.

    WHY: Input JSON comes from various speech-to-text services with different
    schemas. This function normalizes them into a uniform Word list.

    HOW: Accepts two input shapes:
      1. List of segments with nested 'words' arrays
      2. Direct flat list of word objects
    Field names are flexible: word/text/t for text, start/s for start, end/e for end.

    RULES:
    - Speaker markers (–, -, —) get is_speaker_marker=True.
    - First non-marker word in each segment gets is_segment_start=True.
    - Handles incomplete JSON (missing closing brackets) via try_parse_json().

    Args:
        data: Parsed JSON data (list of segments or flat word list).

    Returns:
        Flat list of Word objects with timing and metadata.
    """
    words = []

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue

            # Check if this is a segment with nested words
            if "words" in item and isinstance(item["words"], list):
                is_first_in_segment = True
                for w in item["words"]:
                    if not isinstance(w, dict):
                        continue
                    text = w.get("word", w.get("text", w.get("t", "")))
                    if not text:
                        continue
                    start = float(w.get("start", w.get("s", 0)))
                    end = float(w.get("end", w.get("e", start)))
                    is_speaker = text.strip() in ("–", "-", "—")

                    word_obj = Word(
                        text=text.strip(),
                        start=start,
                        end=end,
                        is_speaker_marker=is_speaker,
                        is_segment_start=is_first_in_segment and not is_speaker
                    )
                    words.append(word_obj)

                    if not is_speaker:
                        is_first_in_segment = False

            # Or a direct word object
            elif any(k in item for k in ("word", "text", "t")):
                text = item.get("word", item.get("text", item.get("t", "")))
                if not text:
                    continue
                start = float(item.get("start", item.get("s", 0)))
                end = float(item.get("end", item.get("e", start)))
                is_speaker = text.strip() in ("–", "-", "—")
                words.append(Word(
                    text=text.strip(), start=start, end=end,
                    is_speaker_marker=is_speaker
                ))

    return words


def try_parse_json(raw: str) -> Any:
    """Try to parse JSON, attempting to fix incomplete input.

    WHY: Input files may be snippets from larger files with missing closing
    brackets. This function tries bracket completions to recover partial JSON.

    HOW:
      1. Normalize line endings, strip whitespace.
      2. Try direct json.loads().
      3. If that fails, strip trailing comma and try appending closing brackets.

    Args:
        raw: Raw JSON string, possibly incomplete.

    Returns:
        Parsed JSON data.

    Raises:
        ValueError: If JSON cannot be parsed even with attempted fixes.
    """
    # Normalize line endings and strip
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").strip()

    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Remove trailing comma and whitespace
    raw_clean = re.sub(r',\s*$', '', raw)

    # Try adding closing brackets with various combinations
    suffixes = [
        "",
        "]",
        "}]",
        "}]}",
        "]}",
        "]}}",
        "]}]",
    ]

    for suffix in suffixes:
        try:
            return json.loads(raw_clean + suffix)
        except json.JSONDecodeError:
            continue

    # Also try with the original (non-cleaned) raw
    for suffix in suffixes:
        try:
            return json.loads(raw + suffix)
        except json.JSONDecodeError:
            continue

    raise ValueError("Could not parse JSON input (even with attempted fixes)")


# =============================================================================
# Line Breaking
# =============================================================================

def best_line_break(text: str, start: float, end: float, config: Dict) -> Dict[str, Any]:
    """Find optimal line break for a caption block.

    WHY: Captions must fit within character limits per line, but naive breaking
    produces ugly, hard-to-read results. This function evaluates all possible
    break points and picks the one with the lowest visual/linguistic cost.

    HOW: Generates single-line and (if max_lines >= 2) two-line candidates for
    every word boundary. Each candidate is scored on length deviation, balance,
    weak-word endings, punctuation alignment, and reading speed (CPS).

    RULES:
    - Respects config["max_lines"] — social format never generates 2-line candidates.
    - All length checks use visible_len() to ignore tags.
    - Returns {"ok": False, ...} if no valid layout exists.

    Args:
        text: Caption text (will be whitespace-normalized).
        start: Start timestamp in seconds (for CPS calculation).
        end: End timestamp in seconds.
        config: Configuration dict with limits and weights.

    Returns:
        Dict with keys: ok, formatted, lines, score, break_at.
    """
    text = " ".join(text.split())  # Normalize whitespace
    words = text.split()

    if not words:
        return {"ok": False, "formatted": "", "lines": [], "score": math.inf}

    candidates = []

    # Single line candidate
    if visible_len(text) <= config["max_line_chars"]:
        score = _score_single_line(text, start, end, config)
        candidates.append({
            "lines": [text],
            "formatted": text,
            "score": score,
            "break_at": None
        })

    # Two-line candidates: only if max_lines >= 2
    if config["max_lines"] >= 2:
        for k in range(1, len(words)):
            line1 = " ".join(words[:k])
            line2 = " ".join(words[k:])

            len1, len2 = visible_len(line1), visible_len(line2)
            if len1 > config["max_line_chars"] or len2 > config["max_line_chars"]:
                continue

            score = _score_two_lines(line1, line2, text, start, end, config)
            candidates.append({
                "lines": [line1, line2],
                "formatted": "{}\n{}".format(line1, line2),
                "score": score,
                "break_at": k
            })

    if not candidates:
        return {"ok": False, "formatted": text, "lines": [text], "score": math.inf}

    best = min(candidates, key=lambda c: c["score"])
    return {"ok": True, **best}


def _score_single_line(text: str, start: float, end: float, config: Dict) -> float:
    """Score a single-line caption layout."""
    w = config["weights"]
    length = visible_len(text)
    score = 0.0

    # Length deviation from target
    score += w["len_deviation"] * abs(length - config["target_line_chars"])

    # Penalty for long single lines
    if length > config["prefer_split_over"]:
        score += w["single_line_long"] * (length - config["prefer_split_over"])

    # CPS penalty
    dur = max(0.001, end - start)
    cps = length / dur
    if cps > config["target_cps"]:
        score += w["cps_above_target"] * (cps - config["target_cps"])
    if cps > config["max_cps"]:
        score += w["cps_above_max"] * (cps - config["max_cps"])

    return score


def _score_two_lines(
    line1: str, line2: str, full_text: str,
    start: float, end: float, config: Dict
) -> float:
    """Score a two-line caption layout."""
    w = config["weights"]
    len1, len2 = visible_len(line1), visible_len(line2)
    score = 0.0

    # Length deviation
    score += w["len_deviation"] * (
        abs(len1 - config["target_line_chars"]) +
        abs(len2 - config["target_line_chars"])
    )

    # Balance
    score += w["balance"] * abs(len1 - len2)

    # Orphan penalty
    min_len = min(len1, len2)
    if min_len < config["min_line_chars"]:
        score += w["orphan"] * (config["min_line_chars"] - min_len)

    # Weak word at end of line 1
    end_word = last_word_clean(line1)
    if end_word in WEAK_END_WORDS:
        score += w["weak_end"]

    # Very short word at end
    if end_word and len(end_word) <= 2:
        score += w["short_end"]

    # Punctuation bonuses
    if ends_sentence(line1):
        score += w["punct_bonus"]
    elif ends_comma(line1):
        score += w["comma_bonus"]

    # CPS penalty
    dur = max(0.001, end - start)
    cps = len(full_text.replace("\n", "")) / dur
    if cps > config["target_cps"]:
        score += w["cps_above_target"] * (cps - config["target_cps"])
    if cps > config["max_cps"]:
        score += w["cps_above_max"] * (cps - config["max_cps"])

    return score


# =============================================================================
# Segmentation (Dynamic Programming)
# =============================================================================

def segment_words(words: List[Word], config: Dict) -> List[Dict[str, Any]]:
    """Segment words into caption blocks using dynamic programming.

    WHY: Greedy left-to-right segmentation produces locally acceptable but
    globally suboptimal caption boundaries. DP considers all valid segmentations
    and picks the one with the lowest total cost across all captions.

    HOW: Standard shortest-path DP over word positions. dp[j] = minimum cost
    to caption words[0:j]. For each position j, try all valid starting positions
    i and compute the cost of the segment words[i:j]. Speaker markers force
    boundaries; sentence boundaries get a bonus.

    RULES:
    - Speaker markers (is_speaker_marker) create forced break points.
    - Segment boundaries (is_segment_start) get a -2.0 cost bonus.
    - Falls back to greedy_segment() if no valid DP path exists.
    - config is passed through to best_line_break() and scoring functions.

    Args:
        words: Flat list of Word objects.
        config: Configuration dict with limits and weights.

    Returns:
        List of segment dicts with text, start, end, formatted, lines, has_speaker.
    """
    if not words:
        return []

    # Find forced break points (speaker changes)
    forced_breaks = set()
    for i, w in enumerate(words):
        if w.is_speaker_marker and i > 0:
            forced_breaks.add(i)

    # Find preferred break points (segment/sentence boundaries)
    preferred_breaks = set()
    for i, w in enumerate(words):
        if w.is_segment_start and i > 0:
            preferred_breaks.add(i)

    N = len(words)
    dp = [math.inf] * (N + 1)
    back = [-1] * (N + 1)
    info = [None] * (N + 1)  # type: List[Optional[Dict[str, Any]]]
    dp[0] = 0.0

    for j in range(1, N + 1):
        # Check if there's a forced break we must respect
        must_break_after = None  # type: Optional[int]
        for fb in forced_breaks:
            if fb < j and (must_break_after is None or fb > must_break_after):
                must_break_after = fb

        min_i = max(0, j - config["max_lookback_words"])
        if must_break_after is not None:
            min_i = max(min_i, must_break_after)

        for i in range(j - 1, min_i - 1, -1):
            # Can't cross a forced break
            crosses_break = any(fb > i and fb < j for fb in forced_breaks)
            if crosses_break:
                continue

            seg_words = words[i:j]

            # Build segment text, handling speaker markers
            text_parts = []
            has_speaker_marker = False
            for sw in seg_words:
                if sw.is_speaker_marker:
                    has_speaker_marker = True
                else:
                    text_parts.append(sw.text)

            if not text_parts:
                continue

            seg_text = " ".join(text_parts)
            if has_speaker_marker:
                seg_text = "– " + seg_text

            if len(seg_text) > config["max_cue_chars"] + 10:
                break
            if len(seg_text) > config["max_cue_chars"]:
                continue

            seg_start = seg_words[0].start
            seg_end = seg_words[-1].end

            lb = best_line_break(seg_text, seg_start, seg_end, config)
            if not lb["ok"]:
                continue

            cost = _compute_segment_cost(
                seg_text, seg_start, seg_end, lb, has_speaker_marker, config
            )

            # Bonus if this segment ends at a preferred break point
            if j < N and words[j].is_segment_start:
                cost -= 2.0

            # Penalty if we're breaking mid-sentence and not at punctuation
            if (j < N and not words[j].is_segment_start
                    and not ends_sentence(seg_text)
                    and not ends_comma(seg_text)):
                cost += 1.0

            # Nudge against tiny mid-stream cues
            if (seg_end - seg_start) < config["min_cue_dur"] and j != N:
                cost += 2.0

            # Additional penalty for very short text content (stragglers)
            if len(seg_text) < 35 and j != N:
                cost += 1.5

            total = dp[i] + cost
            if total < dp[j]:
                dp[j] = total
                back[j] = i
                info[j] = {
                    "text": seg_text,
                    "start": seg_start,
                    "end": seg_end,
                    "formatted": lb["formatted"],
                    "lines": lb["lines"],
                    "has_speaker": has_speaker_marker
                }

    # Backtrack
    if not math.isfinite(dp[N]):
        # Fallback: greedy segmentation
        return _greedy_segment(words, config)

    segments = []
    j = N
    while j > 0:
        i = back[j]
        if i < 0 or info[j] is None:
            break
        segments.append(info[j])
        j = i

    segments.reverse()
    return segments


def _compute_segment_cost(
    text: str, start: float, end: float,
    lb: Dict, has_speaker: bool, config: Dict
) -> float:
    """Compute the total cost of a caption segment for DP scoring."""
    w = config["weights"]
    cost = lb["score"]

    char_count = len(text.replace("\n", ""))
    dur = max(0.001, end - start)
    cps = char_count / dur

    # Cue length deviation
    cost += w["cue_len_deviation"] * abs(char_count - config["target_cue_chars"])

    # Duration penalties
    if dur < config["min_cue_dur"]:
        cost += w["cue_dur_below"] * (config["min_cue_dur"] - dur)
    if dur > config["max_cue_dur"]:
        cost += w["cue_dur_above"] * (dur - config["max_cue_dur"])

    # Boundary quality
    end_word = last_word_clean(text)
    if ends_sentence(text):
        cost += w["boundary_punct_bonus"]
    elif ends_comma(text):
        cost += w["boundary_punct_bonus"] * 0.3
    elif end_word in WEAK_END_WORDS:
        cost += w["boundary_weak_end"]
    else:
        cost += w.get("boundary_no_punct", 1.5)

    # Speaker change bonus
    if has_speaker:
        cost += w["speaker_change_bonus"]

    return cost


def _greedy_segment(words: List[Word], config: Dict) -> List[Dict[str, Any]]:
    """Fallback greedy segmentation when DP fails to find a valid path.

    This is extremely rare — only triggers if constraints are so tight that
    no valid segmentation exists via DP.
    """
    segments = []
    i = 0

    while i < len(words):
        best_j = i + 1
        best_info = None

        for j in range(i + 1, min(i + config["max_lookback_words"], len(words) + 1)):
            # Stop at speaker markers (except at start)
            if j < len(words) and words[j].is_speaker_marker and j > i + 1:
                break

            seg_words = words[i:j]
            text_parts = []
            has_speaker = False
            for sw in seg_words:
                if sw.is_speaker_marker:
                    has_speaker = True
                else:
                    text_parts.append(sw.text)

            if not text_parts:
                continue

            seg_text = " ".join(text_parts)
            if has_speaker:
                seg_text = "– " + seg_text

            if len(seg_text) > config["max_cue_chars"]:
                break

            lb = best_line_break(seg_text, seg_words[0].start, seg_words[-1].end, config)
            if lb["ok"]:
                best_j = j
                best_info = {
                    "text": seg_text,
                    "start": seg_words[0].start,
                    "end": seg_words[-1].end,
                    "formatted": lb["formatted"],
                    "lines": lb["lines"],
                    "has_speaker": has_speaker
                }

        if best_info:
            segments.append(best_info)
        i = best_j

    return segments


# =============================================================================
# SRT Output
# =============================================================================

def seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return "{:02d}:{:02d}:{:02d},{:03d}".format(hours, minutes, secs, millis)


def generate_srt(segments: List[Dict[str, Any]], config: Dict) -> str:
    """Generate SRT content from segments.

    WHY: SRT is the standard subtitle format. This function handles the
    non-trivial timing adjustments needed to prevent caption overlap and
    ensure minimum display duration.

    HOW: Iterates segments, enforces min_display_dur, prevents overlap with
    the next segment (gap of at least 0.05s), and formats each caption block
    with index, timestamp line, and formatted text.

    RULES:
    - Minimum display duration: end = max(end, start + min_display_dur).
    - Overlap prevention: end = min(end, next_start - 0.05).
    - SRT indices are 1-based.

    Args:
        segments: List of segment dicts from segment_words().
        config: Configuration dict with min_display_dur.

    Returns:
        Complete SRT file content as a string.
    """
    lines = []

    for i, seg in enumerate(segments, 1):
        start = seg["start"]
        end = seg["end"]

        # Ensure minimum display duration
        if end - start < config["min_display_dur"]:
            end = start + config["min_display_dur"]

        # Ensure end doesn't exceed next segment's start
        if i < len(segments):
            next_start = segments[i]["start"]
            if end > next_start - 0.05:
                end = next_start - 0.05

        lines.append(str(i))
        lines.append("{} --> {}".format(seconds_to_srt_time(start), seconds_to_srt_time(end)))
        lines.append(seg["formatted"])
        lines.append("")

    return "\n".join(lines)
