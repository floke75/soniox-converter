"""Test preset configurations for social media caption tuning.

This module defines baseline and tuned variants of the social media preset
for systematic testing and comparison.
"""

from copy import deepcopy
from format_captions.presets import PRESET_SOCIAL

# Baseline: Current production social preset
PRESET_BASELINE = deepcopy(PRESET_SOCIAL)

# Tuning iteration 1: Match broadcast penalties for weak words and short endings
# Goal: Reduce weak-word stragglers by applying stronger penalties
PRESET_TUNED_V1 = deepcopy(PRESET_SOCIAL)
PRESET_TUNED_V1["weights"].update({
    "weak_end": 8.0,            # was 5.0 (match broadcast)
    "short_end": 1.5,           # was 0.8 (match broadcast)
    "orphan": 2.5,              # was 2.0 (match broadcast)
    "boundary_no_punct": 2.0,   # was 1.5 (match broadcast)
})

# Tuning iteration 2: Increase lookback window
# Goal: Give DP more context to find better break points
PRESET_TUNED_V2 = deepcopy(PRESET_TUNED_V1)
PRESET_TUNED_V2["max_lookback_words"] = 10  # was 6

# Tuning iteration 3: Further increase weak-end penalty
# Goal: Even stronger avoidance of weak-word endings
PRESET_TUNED_V3 = deepcopy(PRESET_TUNED_V2)
PRESET_TUNED_V3["weights"]["weak_end"] = 10.0  # was 8.0

# Tuning iteration 4: Balance with punctuation bonuses
# Goal: Encourage breaks at natural punctuation while avoiding weak words
PRESET_TUNED_V4 = deepcopy(PRESET_TUNED_V3)
PRESET_TUNED_V4["weights"].update({
    "punct_bonus": -4.0,              # was -3.5 (stronger pull toward punctuation)
    "boundary_punct_bonus": -5.0,     # was -4.0 (even stronger at boundaries)
})

# Tuning iteration 5: Extremely aggressive weak-word avoidance
# Goal: Test if very high penalties can eliminate stragglers
PRESET_TUNED_V5 = deepcopy(PRESET_SOCIAL)
PRESET_TUNED_V5["weights"].update({
    "weak_end": 20.0,              # extremely high
    "boundary_weak_end": 15.0,     # was 4.0
    "short_end": 3.0,              # was 0.8
    "orphan": 3.0,                 # was 2.0
    "boundary_no_punct": 3.0,      # was 1.5
})
PRESET_TUNED_V5["max_lookback_words"] = 12

# Tuning iteration 6: Balanced aggressive tuning (FINAL)
# Goal: Reduce stragglers while maintaining reasonable caption lengths
PRESET_TUNED_FINAL = deepcopy(PRESET_SOCIAL)
PRESET_TUNED_FINAL["weights"].update({
    "weak_end": 15.0,              # lighter than production (35.0) — intermediate tuning iteration
    "boundary_weak_end": 12.0,     # lighter than production (20.0) — intermediate tuning iteration
    "short_end": 2.5,              # lighter than production (4.0) — intermediate tuning iteration
    "orphan": 3.0,                 # lighter than production (5.0) — intermediate tuning iteration
    "boundary_no_punct": 2.5,      # lighter than production (3.5) — intermediate tuning iteration
    "punct_bonus": -4.5,           # lighter than production (-8.0) — intermediate tuning iteration
    "boundary_punct_bonus": -6.0,  # lighter than production (-10.0) — intermediate tuning iteration
})
PRESET_TUNED_FINAL["max_lookback_words"] = 10  # was 6

# All presets available for testing
TEST_PRESETS = {
    "baseline": PRESET_BASELINE,
    "tuned-v1": PRESET_TUNED_V1,
    "tuned-v2": PRESET_TUNED_V2,
    "tuned-v3": PRESET_TUNED_V3,
    "tuned-v4": PRESET_TUNED_V4,
    "tuned-v5": PRESET_TUNED_V5,
    "final": PRESET_TUNED_FINAL,
}
