"""Configuration presets and linguistic constants for caption formatting.

WHY: Different output targets (broadcast TV, social media) need different
caption constraints — line counts, character limits, timing, and scoring
weights. Centralizing these as importable constants lets callers select a
preset by name without knowing the details, and supports concurrent
formatting with different presets (no global state).

HOW: Each preset is a plain dict with hard limits (max_lines, max_line_chars),
soft targets (target_cps, target_cue_chars), and a nested 'weights' dict
that controls the DP scoring heuristics. The PRESETS dict maps preset names
to their config dicts. WEAK_END_WORDS is the set of Swedish words that
should not end a caption line.

RULES:
- Presets are frozen constants — never mutate them at runtime.
- Callers must copy a preset before modifying it (the library does this
  internally via format_srt).
- Only Swedish weak words are implemented; English support is a future task.
- The "some" key is an alias for "social".
"""

from typing import Dict, Set

# Broadcast format: 16:9, traditional TV subtitles
PRESET_BROADCAST: Dict = {
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
PRESET_SOCIAL: Dict = {
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

# Preset lookup by name
PRESETS: Dict[str, Dict] = {
    "broadcast": PRESET_BROADCAST,
    "social": PRESET_SOCIAL,
    "some": PRESET_SOCIAL,  # Alias
}

# Swedish weak words — avoid ending caption lines with these.
# These are function words (conjunctions, prepositions, pronouns, auxiliaries)
# that create an incomplete feeling when placed at a line break.
WEAK_END_WORDS: Set[str] = {
    "och", "att", "som", "i", "på", "av", "för", "med", "till", "om",
    "när", "då", "så", "men", "eller", "utan", "under", "över", "mellan",
    "innan", "efter", "trots", "eftersom", "medan", "från", "kring", "mot", "via",
    "det", "de", "den", "detta", "dessa", "man", "vi", "jag", "du", "han",
    "hon", "ni", "en", "ett", "där", "här", "ju",
    "är", "var", "blir", "ska", "kan", "har", "hade", "får", "vill", "kommer", "inte"
}
