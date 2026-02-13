#!/usr/bin/env python3
"""
format_captions.py
Swedish SDH caption formatter for EFN video content.

Accepts word-level timestamped JSON (segments with nested words) and outputs SRT.

Usage:
    python3 format_captions.py input.json output.srt
    python3 format_captions.py input.json output.srt --format social
    python3 format_captions.py input.json  # outputs to stdout
    cat input.json | python3 format_captions.py - output.srt

Format presets:
    --format broadcast  (default) 16:9 TV, 2 lines, max 42 chars/line
    --format social     9:16 vertical/SoMe, 1 line, max 25 chars/line
    --format some       Alias for social
"""

import json
import math
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# =============================================================================
# Configuration Presets
# =============================================================================

# Broadcast format: 16:9, traditional TV subtitles
PRESET_BROADCAST = {
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

# Social media format (SoMe): 9:16 vertical video, single line captions
PRESET_SOCIAL = {
    "max_lines": 1,
    "max_line_chars": 25,
    "max_cue_chars": 25,
    "target_line_chars": 18,
    "prefer_split_over": 18,  # Always prefer short lines
    "min_line_chars": 6,
    "target_cps": 12.0,  # Slightly slower for mobile reading
    "max_cps": 15.0,
    "target_cue_chars": 16,
    "min_cue_dur": 0.8,  # Faster pacing for social
    "max_cue_dur": 3.5,  # Shorter max duration
    "min_display_dur": 0.6,
    "max_lookback_words": 6,  # Fewer words per caption
    "weights": {
        "len_deviation": 0.15,
        "balance": 0.0,  # No balance needed for single line
        "orphan": 2.0,
        "weak_end": 5.0,
        "short_end": 0.8,
        "punct_bonus": -3.5,  # Strong preference for punctuation breaks
        "comma_bonus": -2.0,
        "single_line_long": 3.0,  # Strong penalty for approaching max
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

PRESETS = {
    "broadcast": PRESET_BROADCAST,
    "social": PRESET_SOCIAL,
    "some": PRESET_SOCIAL,  # Alias
}

# Active config (set by main)
CONFIG = PRESET_BROADCAST.copy()

# Swedish weak words - avoid ending lines with these
WEAK_END_WORDS = {
    "och", "att", "som", "i", "på", "av", "för", "med", "till", "om",
    "när", "då", "så", "men", "eller", "utan", "under", "över", "mellan",
    "innan", "efter", "trots", "eftersom", "medan", "från", "kring", "mot", "via",
    "det", "de", "den", "detta", "dessa", "man", "vi", "jag", "du", "han",
    "hon", "ni", "en", "ett", "där", "här", "ju",
    "är", "var", "blir", "ska", "kan", "har", "hade", "får", "vill", "kommer", "inte"
}

# =============================================================================
# Text Utilities
# =============================================================================

TAG_RE = re.compile(r"<[^>]+>")
SENT_PUNCT_RE = re.compile(r"[.!?…]$")
COMMA_RE = re.compile(r"[,;:]$")


def strip_tags(s: str) -> str:
    return TAG_RE.sub("", s)


def visible_len(s: str) -> int:
    return len(strip_tags(s))


def strip_punct(w: str) -> str:
    return re.sub(r'^[""\'(]+|[.,!?…:;)\]""\']+$', "", w)


def last_word_clean(line: str) -> str:
    parts = strip_tags(line).strip().split()
    for w in reversed(parts):
        w = strip_punct(w).lower()
        if w:
            return w
    return ""


def ends_sentence(line: str) -> bool:
    return bool(SENT_PUNCT_RE.search(strip_tags(line).strip()))


def ends_comma(line: str) -> bool:
    return bool(COMMA_RE.search(strip_tags(line).strip()))


# =============================================================================
# Input Parsing
# =============================================================================

@dataclass
class Word:
    text: str
    start: float
    end: float
    is_speaker_marker: bool = False
    is_segment_start: bool = False  # True if this word starts a new segment (sentence)


def parse_input(data: Any) -> List[Word]:
    """
    Parse input JSON into flat word list.
    
    Accepts:
    - List of segments with nested 'words' arrays
    - Direct list of word objects
    
    Handles incomplete JSON (missing closing brackets) gracefully.
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
                words.append(Word(text=text.strip(), start=start, end=end, is_speaker_marker=is_speaker))
    
    return words


def try_parse_json(raw: str) -> Any:
    """Try to parse JSON, attempting to fix incomplete input."""
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

def best_line_break(text: str, start: float, end: float) -> Dict[str, Any]:
    """
    Find optimal line break for a caption block.
    Returns best 1 or 2 line layout (respects CONFIG["max_lines"]).
    """
    text = " ".join(text.split())  # Normalize whitespace
    words = text.split()
    
    if not words:
        return {"ok": False, "formatted": "", "lines": [], "score": math.inf}
    
    candidates = []
    
    # Single line candidate
    if visible_len(text) <= CONFIG["max_line_chars"]:
        score = score_single_line(text, start, end)
        candidates.append({
            "lines": [text],
            "formatted": text,
            "score": score,
            "break_at": None
        })
    
    # Two-line candidates: only if max_lines >= 2
    if CONFIG["max_lines"] >= 2:
        for k in range(1, len(words)):
            line1 = " ".join(words[:k])
            line2 = " ".join(words[k:])
            
            len1, len2 = visible_len(line1), visible_len(line2)
            if len1 > CONFIG["max_line_chars"] or len2 > CONFIG["max_line_chars"]:
                continue
            
            score = score_two_lines(line1, line2, text, start, end)
            candidates.append({
                "lines": [line1, line2],
                "formatted": f"{line1}\n{line2}",
                "score": score,
                "break_at": k
            })
    
    if not candidates:
        return {"ok": False, "formatted": text, "lines": [text], "score": math.inf}
    
    best = min(candidates, key=lambda c: c["score"])
    return {"ok": True, **best}


def score_single_line(text: str, start: float, end: float) -> float:
    w = CONFIG["weights"]
    length = visible_len(text)
    score = 0.0
    
    # Length deviation from target
    score += w["len_deviation"] * abs(length - CONFIG["target_line_chars"])
    
    # Penalty for long single lines
    if length > CONFIG["prefer_split_over"]:
        score += w["single_line_long"] * (length - CONFIG["prefer_split_over"])
    
    # CPS penalty
    dur = max(0.001, end - start)
    cps = length / dur
    if cps > CONFIG["target_cps"]:
        score += w["cps_above_target"] * (cps - CONFIG["target_cps"])
    if cps > CONFIG["max_cps"]:
        score += w["cps_above_max"] * (cps - CONFIG["max_cps"])
    
    return score


def score_two_lines(line1: str, line2: str, full_text: str, start: float, end: float) -> float:
    w = CONFIG["weights"]
    len1, len2 = visible_len(line1), visible_len(line2)
    score = 0.0
    
    # Length deviation
    score += w["len_deviation"] * (abs(len1 - CONFIG["target_line_chars"]) + abs(len2 - CONFIG["target_line_chars"]))
    
    # Balance
    score += w["balance"] * abs(len1 - len2)
    
    # Orphan penalty
    min_len = min(len1, len2)
    if min_len < CONFIG["min_line_chars"]:
        score += w["orphan"] * (CONFIG["min_line_chars"] - min_len)
    
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
    if cps > CONFIG["target_cps"]:
        score += w["cps_above_target"] * (cps - CONFIG["target_cps"])
    if cps > CONFIG["max_cps"]:
        score += w["cps_above_max"] * (cps - CONFIG["max_cps"])
    
    return score


# =============================================================================
# Segmentation (Dynamic Programming)
# =============================================================================

def segment_words(words: List[Word]) -> List[Dict[str, Any]]:
    """
    Segment words into caption blocks using dynamic programming.
    Speaker markers (–) force block boundaries.
    Segment boundaries (sentence starts) are preferred but not forced.
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
    info = [None] * (N + 1)
    dp[0] = 0.0
    
    for j in range(1, N + 1):
        # Check if there's a forced break we must respect
        must_break_after = None
        for fb in forced_breaks:
            if fb < j and (must_break_after is None or fb > must_break_after):
                must_break_after = fb
        
        min_i = max(0, j - CONFIG["max_lookback_words"])
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
            
            if len(seg_text) > CONFIG["max_cue_chars"] + 10:
                break
            if len(seg_text) > CONFIG["max_cue_chars"]:
                continue
            
            seg_start = seg_words[0].start
            seg_end = seg_words[-1].end
            
            lb = best_line_break(seg_text, seg_start, seg_end)
            if not lb["ok"]:
                continue
            
            cost = compute_segment_cost(seg_text, seg_start, seg_end, lb, has_speaker_marker)
            
            # Bonus if this segment ends at a preferred break point (next word is segment start)
            if j < N and words[j].is_segment_start:
                cost -= 2.0  # Bonus for aligning with sentence boundary
            
            # Penalty if we're breaking mid-sentence and not at punctuation
            if j < N and not words[j].is_segment_start and not ends_sentence(seg_text) and not ends_comma(seg_text):
                cost += 1.0  # Additional penalty for mid-sentence break
            
            # Nudge against tiny mid-stream cues
            if (seg_end - seg_start) < CONFIG["min_cue_dur"] and j != N:
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
        return greedy_segment(words)
    
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


def compute_segment_cost(text: str, start: float, end: float, lb: Dict, has_speaker: bool) -> float:
    w = CONFIG["weights"]
    cost = lb["score"]
    
    char_count = len(text.replace("\n", ""))
    dur = max(0.001, end - start)
    cps = char_count / dur
    
    # Cue length deviation
    cost += w["cue_len_deviation"] * abs(char_count - CONFIG["target_cue_chars"])
    
    # Duration penalties
    if dur < CONFIG["min_cue_dur"]:
        cost += w["cue_dur_below"] * (CONFIG["min_cue_dur"] - dur)
    if dur > CONFIG["max_cue_dur"]:
        cost += w["cue_dur_above"] * (dur - CONFIG["max_cue_dur"])
    
    # Boundary quality - strongly prefer ending at sentence punctuation
    end_word = last_word_clean(text)
    if ends_sentence(text):
        cost += w["boundary_punct_bonus"]
    elif ends_comma(text):
        cost += w["boundary_punct_bonus"] * 0.3  # Smaller bonus for comma
    elif end_word in WEAK_END_WORDS:
        cost += w["boundary_weak_end"]
    else:
        # Penalty for ending without any punctuation
        cost += w.get("boundary_no_punct", 1.5)
    
    # Speaker change bonus
    if has_speaker:
        cost += w["speaker_change_bonus"]
    
    return cost


def greedy_segment(words: List[Word]) -> List[Dict[str, Any]]:
    """Fallback greedy segmentation when DP fails."""
    segments = []
    i = 0
    
    while i < len(words):
        # Find extent of this segment
        best_j = i + 1
        best_score = math.inf
        best_info = None
        
        for j in range(i + 1, min(i + CONFIG["max_lookback_words"], len(words) + 1)):
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
            
            if len(seg_text) > CONFIG["max_cue_chars"]:
                break
            
            lb = best_line_break(seg_text, seg_words[0].start, seg_words[-1].end)
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
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(segments: List[Dict[str, Any]]) -> str:
    """Generate SRT content from segments."""
    lines = []
    
    for i, seg in enumerate(segments, 1):
        start = seg["start"]
        end = seg["end"]
        
        # Ensure minimum display duration
        if end - start < CONFIG["min_display_dur"]:
            end = start + CONFIG["min_display_dur"]
        
        # Ensure end doesn't exceed next segment's start
        if i < len(segments):
            next_start = segments[i]["start"]
            if end > next_start - 0.05:
                end = next_start - 0.05
        
        lines.append(str(i))
        lines.append(f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}")
        lines.append(seg["formatted"])
        lines.append("")
    
    return "\n".join(lines)


# =============================================================================
# Main
# =============================================================================

def main():
    global CONFIG
    
    # Parse arguments
    args = sys.argv[1:]
    
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print("\nAvailable format presets:")
        print("  broadcast  16:9 TV subtitles (2 lines, 42 chars max)")
        print("  social     9:16 vertical video (1 line, 25 chars max)")
        print("  some       Alias for social")
        sys.exit(0)
    
    # Extract --format argument
    format_name = "broadcast"
    filtered_args = []
    i = 0
    while i < len(args):
        if args[i] == "--format" and i + 1 < len(args):
            format_name = args[i + 1].lower()
            i += 2
        elif args[i].startswith("--format="):
            format_name = args[i].split("=", 1)[1].lower()
            i += 1
        else:
            filtered_args.append(args[i])
            i += 1
    
    # Set config based on format
    if format_name not in PRESETS:
        print(f"Error: Unknown format '{format_name}'. Available: {', '.join(PRESETS.keys())}", file=sys.stderr)
        sys.exit(1)
    
    CONFIG = PRESETS[format_name].copy()
    CONFIG["weights"] = PRESETS[format_name]["weights"].copy()
    
    # Get input/output paths
    input_path = filtered_args[0] if filtered_args else "-"
    output_path = filtered_args[1] if len(filtered_args) > 1 else None
    
    # Read input
    if input_path == "-":
        raw = sys.stdin.read()
    else:
        with open(input_path, "r", encoding="utf-8") as f:
            raw = f.read()
    
    # Parse JSON
    try:
        data = try_parse_json(raw)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Parse words
    words = parse_input(data)
    if not words:
        print("Error: No words found in input", file=sys.stderr)
        sys.exit(1)
    
    # Segment
    segments = segment_words(words)
    if not segments:
        print("Error: Segmentation produced no output", file=sys.stderr)
        sys.exit(1)
    
    # Generate SRT
    srt = generate_srt(segments)
    
    # Output
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(srt)
        format_label = "SoMe" if format_name in ("social", "some") else "broadcast"
        print(f"Wrote {len(segments)} captions ({format_label} format) to {output_path}", file=sys.stderr)
    else:
        print(srt)


if __name__ == "__main__":
    main()
