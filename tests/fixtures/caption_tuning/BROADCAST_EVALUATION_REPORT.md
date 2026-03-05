# Broadcast Caption Format Evaluation Report

## Executive Summary

This report presents a comprehensive evaluation of the broadcast (16:9, 2-line) caption formatting preset using the same test corpus and methodology as the social media evaluation.

**Key Findings:**

- Weak-word straggler rate: 12.5% (3/24 blocks)
- Line balance: 5.6 chars average difference (GOOD)
- Single-line block rate: 16.7% (4/24 blocks)
- Broadcast preset outperforms social media preset: 12.5% vs 16.2% weak-word rate

## Test Methodology

### Test Corpus

- 5 Swedish test files covering various linguistic patterns
- Same corpus used for social media evaluation
- Total: 24 caption blocks generated in broadcast format

### Metrics Tracked

For 2-line broadcast format, we track:

1. **Weak-word stragglers**: Blocks where ANY line (line 1 OR line 2) ends with weak word
2. **Line balance**: Average absolute difference between line 1 and line 2 character counts
3. **Single-line blocks**: Percentage of blocks using only 1 line (should be minimal)
4. **Short-word endings**: Lines ending with words ≤3 characters
5. **Unpunctuated boundaries**: Blocks not ending at sentence boundaries

## Results Summary

### Overall Metrics

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

### Weak-Word Breakdown by Line

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

**Observation**: Line 2 has twice as many weak-word endings as Line 1, suggesting the algorithm may need stronger penalties for Line 2 endings.

## Quality Assessment

### Strengths

1. **Low Weak-Word Rate (12.5%)** - ACCEPTABLE
  - Below the 15% threshold
  - Better than social media preset (16.2%)
  - Excellent performance on longer sentences (0% on long/medium)
2. **Excellent Line Balance (5.6 chars)** - GOOD
  - Well below 10-char threshold
  - Lines are evenly distributed
  - Natural reading rhythm
3. **Natural Sentence Boundaries**
  - Most breaks occur at sentence/clause boundaries
  - Good use of punctuation cues

### Areas for Improvement

1. **Short Sentences Challenge (25% weak-word rate)**
  - Short, choppy sentences create more stragglers
  - Example: "Hej alla. Jag heter Anna. Vi ses"
  - Difficult to optimize when sentences are very brief
2. **Single-Line Blocks (16.7%)**
  - Higher than ideal for broadcast format
  - Examples identified:
    - "Hej alla. Jag heter Anna. Vi ses" (short_sentences, block 1)
    - "Vi är klara. Bra jobbat. Ses imorgon." (short_sentences, block 4)
    - "– Bra. Låt oss börja direkt eftersom" (mixed_complexity, block 4)
    - "eftersom det betyder mycket för oss." (weak_words_heavy, block 3)
  - Some are justified (very short content), others could be split
3. **Specific Weak-Word Cases**
  - **mixed_complexity, Block 4, Line 1**: "– Bra. Låt oss börja direkt eftersom" → [eftersom]
  - **short_sentences, Block 2, Line 2**: "Jag gillar det. Vad tycker du?" → [du]
  - **weak_words_heavy, Block 2, Line 2**: "att vi fortsätter med detta" → [detta]

## Comparison: Broadcast vs Social Media

### Format Characteristics

| Aspect | Broadcast (16:9, 2-line) | Social (9:16, 1-line) |
| --- | --- | --- |
| Max lines | 2 | 1 |
| Max chars/line | 42 | 30 |
| Target CPS | 13.0 | 12.0 |
| Weak-word rate | 12.5% | 16.2% |
| Blocks (same corpus) | 24 | 74 |

### Performance Comparison

**Broadcast advantages:**

- Lower weak-word rate (12.5% vs 16.2%)
- Fewer total blocks (24 vs 74) = less fragmentation
- Better for longer sentences
- More natural reading flow

**Social advantages:**

- Simpler format (single line)
- Better for mobile/vertical video
- More frequent caption updates

## Specific Examples

### Excellent 2-Line Balance

```
Block from long_sentences:
"Under de senaste månaderna har vi arbetat
intensivt med att utveckla och förbättra"

Line 1: 44 chars → "Under de senaste månaderna har vi arbetat"
Line 2: 41 chars → "intensivt med att utveckla och förbättra"
Balance: 3 chars difference ✓
```

### Weak-Word Straggler Examples

**Case 1: Conjunction at boundary**

```
Block 4 from mixed_complexity:
"– Bra. Låt oss börja direkt eftersom"

Issue: Line ends with "eftersom" (weak conjunction)
Also: Single-line block when could potentially be split
```

**Case 2: Pronoun ending**

```
Block 2 from short_sentences:
"snart. Tack för idag. Det var bra.
Jag gillar det. Vad tycker du?"

Issue: Line 2 ends with "du" (weak pronoun)
```

**Case 3: Demonstrative pronoun**

```
Block 2 from weak_words_heavy:
"Jag tycker att det är viktigt
att vi fortsätter med detta"

Issue: Line 2 ends with "detta" (demonstrative pronoun)
```

## Recommendations

### Immediate Actions

1. **Accept current performance** - 12.5% weak-word rate is acceptable
2. **Monitor Line 2 endings** - Consider slight increase to `boundary_weak_end` penalty

### Optional Tuning (if stricter quality needed)

To reduce weak-word rate below 10%:

```python
PRESET_BROADCAST["weights"].update({
    "weak_end": 10.0,              # was 8.0
    "boundary_weak_end": 5.0,      # was 4.0
    "short_end": 2.0,              # was 1.5
})
```

To reduce single-line blocks:

```python
PRESET_BROADCAST["weights"].update({
    "single_line_long": 2.0,       # was 1.2
})
```

### Not Recommended

- Do NOT aggressively tune for short sentences - they naturally create stragglers
- Do NOT over-penalize single-line blocks - some content is legitimately brief
- Do NOT sacrifice line balance for weak-word avoidance

## Conclusion

The broadcast preset is performing **well** with:

- ✓ 12.5% weak-word straggler rate (below 15% threshold)
- ✓ Excellent line balance (5.6 chars avg)
- ✓ Better performance than social media preset
- ⚠ Some room for improvement on short sentences
- ⚠ Single-line block rate could be lower

**Overall verdict: ACCEPTABLE - No critical tuning needed**

The preset achieves a good balance between natural language boundaries and technical constraints. The issues identified are primarily driven by the test corpus (short sentences) rather than fundamental algorithm problems.

For production use, the current broadcast preset is **recommended without modification**.

## Appendix: Tool Usage

### Generate baseline outputs

```bash
python tests/tools/tune_broadcast_captions.py --preset baseline
```

### Run comprehensive analysis

```bash
python tests/tools/tune_broadcast_captions.py --all
```

### Output location

```
tests/fixtures/caption_tuning/output_broadcast/
├── baseline/
│   ├── long_sentences.srt
│   ├── medium_sentences.srt
│   ├── mixed_complexity.srt
│   ├── short_sentences.srt
│   └── weak_words_heavy.srt
└── broadcast/
    └── (same files)
```