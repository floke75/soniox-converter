# Broadcast Caption Tuning Tool

## Overview

The `tune_broadcast_captions.py` tool evaluates broadcast (16:9, 2-line) caption formatting quality using Swedish test transcripts. It measures metrics specific to 2-line format: weak-word endings on both lines, line balance, single-line blocks, and unpunctuated boundaries.

## Usage

### Basic Commands

```bash
# Run comprehensive analysis with detailed metrics
python tests/tools/tune_broadcast_captions.py --all

# Generate outputs for a specific preset
python tests/tools/tune_broadcast_captions.py --preset baseline
python tests/tools/tune_broadcast_captions.py --preset broadcast

# Show help
python tests/tools/tune_broadcast_captions.py --help
```

## Metrics Explained

### 1. Weak-Word Straggler Rate (Weak%)

- **Definition**: Percentage of blocks where ANY line ends with a weak word
- **Weak words**: Swedish function words (conjunctions, prepositions, pronouns) like "och", "att", "för", "du", "detta"
- **Target**: < 15% acceptable, < 10% excellent, < 5% outstanding
- **Why it matters**: Weak-word endings create incomplete feeling and poor reading experience

### 2. Line Balance

- **Definition**: Average absolute difference between line 1 and line 2 character counts (for 2-line blocks)
- **Target**: < 10 chars good, < 5 chars excellent
- **Why it matters**: Balanced lines create better visual rhythm and easier reading

### 3. Single-Line Block Rate (Single%)

- **Definition**: Percentage of blocks using only 1 line instead of 2
- **Target**: < 10% good, < 5% excellent
- **Why it matters**: Broadcast format should utilize both lines for content efficiency and visual consistency
- **Note**: Some single-line blocks are justified (very short sentences, speaker changes)

### 4. Short-Word Endings (Short)

- **Definition**: Count of blocks where any line ends with a word ≤3 characters
- **Target**: Minimize but less critical than weak-words
- **Why it matters**: Very short words can create awkward breaks

### 5. Unpunctuated Boundaries (NoPunct)

- **Definition**: Count of blocks not ending with punctuation (. ! ? , ;)
- **Target**: Minimize
- **Why it matters**: Punctuation signals natural reading boundaries

## Output Format

### Summary Table

Historical sample output shape from the tool:

```
Test File                       Blocks   Weak%  Balance  Single%   Short  NoPunct
----------------------------------------------------------------------------------------
long_sentences                       7    0.0%      3.0     0.0%       2        5
medium_sentences                     4    0.0%      8.0     0.0%       0        0
mixed_complexity                     5   20.0%      6.2    20.0%       0        2
short_sentences                      4   25.0%      6.0    50.0%       3        1
weak_words_heavy                     4   25.0%      4.7    25.0%       1        1
----------------------------------------------------------------------------------------
TOTAL/AVG                           24   12.5%      5.6    16.7%       6        9
```

### Detailed Weak-Word Analysis

```
Test File                          Line 1 Weak     Line 2 Weak      Total Weak
--------------------------------------------------------------------------------
long_sentences                               0               0               0
medium_sentences                             0               0               0
mixed_complexity                             1               0               1
short_sentences                              0               1               1
weak_words_heavy                             0               1               1
--------------------------------------------------------------------------------
TOTAL                                        1               2               3
```

### Holistic Analysis (--all mode)

The `--all` flag provides:

- Overall statistics across all test files
- Quality assessment with recommendations
- Comparison against quality thresholds
- Specific tuning recommendations if needed

## Test Corpus

Located in `tests/fixtures/caption_tuning/real_transcripts/`:

- `long_sentences.json` - Long, complex sentences
- `medium_sentences.json` - Medium-length sentences
- `mixed_complexity.json` - Mix of sentence lengths
- `short_sentences.json` - Short, choppy sentences
- `weak_words_heavy.json` - Content with many weak words

## Output Files

Generated SRT files are saved to:

```
tests/fixtures/caption_tuning/output_broadcast/
├── baseline/
│   ├── long_sentences.srt
│   ├── medium_sentences.srt
│   ├── mixed_complexity.srt
│   ├── short_sentences.srt
│   └── weak_words_heavy.srt
└── broadcast/
    └── (same structure)
```

## Comparison with Social Media Tool

| Aspect | Broadcast Tool | Social Media Tool |
| --- | --- | --- |
| Format | 16:9, 2-line | 9:16, 1-line |
| Max lines | 2 | 1 |
| Key metric | Weak% on both lines | Weak% on final line |
| Line balance | Tracked | N/A (single line) |
| Single-line blocks | Tracked (should be low) | N/A |

## Interpreting Current Performance

Treat the numbers printed by the tool as measurements of the current test
corpus, not product guarantees. Re-run `python tests/tools/tune_broadcast_captions.py --all`
whenever you need the current baseline.

What to look for:

- Weak-word rate below 15% is acceptable for the current broadcast heuristic.
- Line balance below 10 chars is generally good.
- Single-line rate is diagnostic, not automatically a bug.
- Do not compare the broadcast percentage directly to the social-media suite as
  if they were equivalent KPIs; the tools score different layouts and use
  different thresholds.

See `tests/fixtures/caption_tuning/BROADCAST_EVALUATION_REPORT.md` for a
detailed historical analysis.

## Tuning Guidelines

### When to tune

- Weak-word rate > 15%: Consider increasing penalties
- Line balance > 10: Increase balance weight
- Single-line rate > 20%: Increase single_line_long penalty

### When NOT to tune

- Current performance is within the acceptable threshold you measure from the tool
- Short sentences naturally create stragglers
- Over-tuning can create worse artifacts

### Example tuning (if needed)

```python
from format_captions.presets import PRESET_BROADCAST

PRESET_BROADCAST["weights"].update({
    "weak_end": 10.0,              # was 8.0 - stronger penalty
    "boundary_weak_end": 5.0,      # was 4.0 - stronger at boundaries
    "short_end": 2.0,              # was 1.5 - avoid short endings
    "single_line_long": 2.0,       # was 1.2 - prefer 2-line blocks
})
```

## Architecture

The tool follows the same architecture as `tune_social_captions.py`:

1. Load JSON test transcripts
2. Convert to Transcript IR
3. Convert to caption words
4. Format with broadcast preset
5. Parse resulting SRT
6. Analyze metrics
7. Generate reports

## Dependencies

- `format_captions` - Caption formatting library
- `soniox_converter` - Transcript processing
- Standard library: `json`, `argparse`, `pathlib`

## Related Files

- `tests/tools/tune_social_captions.py` - Social media tuning tool
- `tests/fixtures/caption_tuning/presets.py` - Test preset configurations
- `format_captions/presets.py` - Production presets
- `tests/fixtures/caption_tuning/BROADCAST_EVALUATION_REPORT.md` - Evaluation report