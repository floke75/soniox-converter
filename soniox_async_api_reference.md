# Soniox Async API → Premiere Pro Transcript Converter: Ground-Truth Reference

> **Purpose**: Authoritative schema reference for an LLM agentic coder implementing a converter from the Soniox Non-Realtime (Async) Speech-to-Text API output to Adobe Premiere Pro's Audio Transcript JSON format.
>
> **Verified against**: Official Soniox documentation at `soniox.com/docs/llms-full.txt` and all individual API reference pages, fetched 2026-02-13. Every claim below is confirmed against the official docs. Where the async API differs from the realtime WebSocket API, differences are explicitly called out.

---

## 1. Async Transcript Response Schema

**Endpoint**: `GET /v1/transcriptions/{transcription_id}/transcript`

Returns a JSON object with exactly three top-level fields:

```json
{
  "id": "string (UUID)",
  "text": "string",
  "tokens": [ { ... }, { ... }, ... ]
}
```

| Field    | Type           | Description                                          |
|----------|----------------|------------------------------------------------------|
| `id`     | string (UUID)  | Transcription identifier                             |
| `text`   | string         | Complete pre-assembled plaintext of entire transcript |
| `tokens` | array\<object> | Flat list of every sub-word token with metadata       |

**Critical facts**:
- There are **NO** utterance, sentence, paragraph, or segment groupings — just one flat array.
- The `text` field is a convenience concatenation of all `token.text` values. The realtime WebSocket API does NOT provide this field.
- The converter must build ALL hierarchical structure (speaker segments, word grouping, sentence boundaries) from the flat token array.

### 1.1 Token Object Fields

Each token object contains the following fields. Some fields are **conditional** — they only appear when the corresponding feature is enabled in the create-transcription request.

| Field                | Type   | Present when                                 | Description                                                     |
|----------------------|--------|----------------------------------------------|-----------------------------------------------------------------|
| `text`               | string | **Always**                                   | Sub-word fragment, whole word, or punctuation mark               |
| `start_ms`           | number | **Always**\*                                 | Start timestamp in integer **milliseconds** from audio start     |
| `end_ms`             | number | **Always**\*                                 | End timestamp in integer **milliseconds** from audio start       |
| `confidence`         | number | **Always**                                   | Recognition confidence, float `0.0`–`1.0`                       |
| `speaker`            | string | Only if `enable_speaker_diarization: true`   | Speaker label: `"1"`, `"2"`, … up to `"15"`                     |
| `language`           | string | Only if `enable_language_identification: true`| ISO 639-1 code: `"en"`, `"sv"`, `"de"`, etc.                    |
| `translation_status` | string | Only if `translation` is configured          | `"original"`, `"translation"`, or `"none"`                       |
| `source_language`    | string | Only on translation tokens                   | Source language code for translated tokens                        |

\* `start_ms` and `end_ms` are absent on tokens where `translation_status` is `"translation"`, since generated translations have no audio alignment.

### 1.2 Critical Async-vs-Realtime Differences

| Aspect               | Async API (`/v1/transcriptions/.../transcript`)          | Realtime WebSocket API                              |
|----------------------|----------------------------------------------------------|-----------------------------------------------------|
| `is_final` field     | **DOES NOT EXIST** — all tokens are inherently final      | Present on every token (`true`/`false`)              |
| `text` top-level     | Present — pre-assembled full transcript string            | Not present                                          |
| Response wrapping    | Single response with all tokens                           | Streaming responses with `final_audio_proc_ms`, etc. |
| `<end>` / `<fin>`   | **NEVER appear** — these are realtime endpoint markers    | Returned for endpoint detection / manual finalization |
| `finished` field     | Not present                                               | Signals end of WebSocket stream                      |

**For the converter: do NOT look for `is_final`, `<end>`, `<fin>`, `finished`, `final_audio_proc_ms`, or `total_audio_proc_ms` in async output. These are all realtime-only constructs.**

---

## 2. Sub-Word Token Assembly Rules

Soniox uses BPE-style (byte-pair encoding) tokenization. Words are frequently split into multiple sub-word tokens. The **leading space character** in a token's `text` field is the **sole word boundary signal**.

### 2.1 The Four Rules

| Rule | Signal | Meaning | Example |
|------|--------|---------|---------|
| **1. Leading space = new word** | `text` starts with `" "` | This token begins a new word | `" are"`, `" fan"`, `" Morgen"` |
| **2. No leading space = continuation** | `text` does NOT start with `" "` AND is not the first token | Append to current word | `"tastic"` after `" fan"` → "fantastic" |
| **3. Punctuation = standalone** | `text` is only punctuation chars (`"?"`, `"."`, `"!"`, `","`) | Separate token, not part of a word | `"?"` after `" you"` |
| **4. First token has no space** | Very first token in array, or first after speaker change | Starts a new word despite no leading space | `"How"`, `"I"` |

### 2.2 Assembly Algorithm (Pseudocode)

```
words = []
current_word = null

for each token in tokens:
    if token is punctuation (text matches /^[.,!?;:…—–-]+$/):
        if current_word is not null:
            words.append(current_word)
            current_word = null
        words.append(make_punctuation(token))

    else if token.text starts with " " OR current_word is null:
        if current_word is not null:
            words.append(current_word)
        current_word = new Word(
            text = token.text.lstrip(" "),
            start_ms = token.start_ms,
            end_ms = token.end_ms,
            confidences = [token.confidence],
            speaker = token.speaker,
            language = token.language
        )

    else:  // continuation sub-word
        current_word.text += token.text
        current_word.end_ms = token.end_ms
        current_word.confidences.append(token.confidence)

if current_word is not null:
    words.append(current_word)
```

### 2.3 Verified Examples from Official Docs

**"Beautiful"** → 3 tokens (from Timestamps docs):
```json
{"text": "Beau", "start_ms": 300, "end_ms": 420}
{"text": "ti",   "start_ms": 420, "end_ms": 540}
{"text": "ful",  "start_ms": 540, "end_ms": 780}
```
Assembled: `text="Beautiful"`, `start_ms=300`, `end_ms=780`

**"fantastic"** → 2 tokens (from Speaker Diarization docs):
```json
{"text": " fan",    "start_ms": 960,  "end_ms": 1100}
{"text": "tastic",  "start_ms": 1100, "end_ms": 1350}
```
Assembled: `text="fantastic"`, `start_ms=960`, `end_ms=1350`

**"Hello"** → 2 tokens (from API Reference example response):
```json
{"text": "Hel", "start_ms": 10,  "end_ms": 90,  "confidence": 0.95}
{"text": "lo",  "start_ms": 110, "end_ms": 160, "confidence": 0.98}
```
Assembled: `text="Hello"`, `start_ms=10`, `end_ms=160`, `confidence=min(0.95, 0.98)=0.95`

---

## 3. Field-by-Field Mapping: Soniox → Premiere Pro

### 3.1 Timestamps

| Soniox field | Unit         | Premiere Pro field | Unit    | Conversion                           |
|--------------|--------------|--------------------|---------|--------------------------------------|
| `start_ms`   | milliseconds | `start`            | seconds | `start = start_ms / 1000.0`         |
| `end_ms`     | milliseconds | `duration`         | seconds | `duration = (end_ms - start_ms) / 1000.0` |

For assembled multi-token words: use first sub-word's `start_ms` and last sub-word's `end_ms`.

Sub-word timestamps are typically **contiguous** within a word (e.g., `300→420`, `420→540`, `540→780`).

### 3.2 Confidence

Soniox provides per-sub-word-token confidence (float `0.0`–`1.0`, always present, no config needed).

Premiere Pro expects a single per-word confidence. Aggregation strategies:
- **Minimum** (conservative): `word_confidence = min(sub_word_confidences)`
- **Mean** (balanced): `word_confidence = mean(sub_word_confidences)`

Low values typically indicate background noise, heavy accents, or uncommon vocabulary.

### 3.3 Punctuation Classification

Premiere Pro requires `type: "word"` or `type: "punctuation"` on each word object.

Soniox punctuation tokens are identifiable by:
- Token `text` consists entirely of punctuation characters: `"."`, `","`, `"?"`, `"!"`, `";"`, `":"`, `"…"`, `"—"`
- No leading space
- Has own `start_ms`/`end_ms`/`confidence`/`speaker`

Converter: set `type: "punctuation"` for these tokens, `type: "word"` for everything else.

### 3.4 End-of-Sentence (`eos`) Inference

Soniox provides **NO** explicit sentence boundary marker. The converter must infer `eos` from punctuation:

1. Identify sentence-ending punctuation tokens: `"."`, `"?"`, `"!"`
2. Set `eos: true` on the **word immediately preceding** that punctuation token
3. All other words get `eos: false`

Note: commas (`","`) and colons (`":"`) are NOT sentence-ending.

### 3.5 Speaker Segmentation

Soniox returns a flat per-token `speaker` field (string `"1"`, `"2"`, … `"15"`). Tokens are NOT pre-grouped.

Premiere Pro requires hierarchical segments grouped by speaker, where each speaker has a UUID v4 and display name.

Converter must:
1. Iterate tokens, detect `speaker` value changes
2. Group contiguous same-speaker tokens into segments
3. Maintain a map: `{"1" → {uuid: "uuid-v4-here", name: "Speaker 1"}, "2" → ...}`
4. Create Premiere Pro segment objects with the UUID-based speaker references

Up to **15 speakers** supported. Async diarization is significantly more accurate than realtime (full audio context).

### 3.6 Language Code Mapping

| Soniox format | Example | Premiere Pro format | Example  |
|---------------|---------|---------------------|----------|
| ISO 639-1     | `"en"`  | BCP-47 locale       | `"en-us"`|

Soniox does not provide region information. The converter needs a configurable mapping with sensible defaults:

```json
{
  "en": "en-us",
  "sv": "sv-se",
  "es": "es-es",
  "de": "de-de",
  "fr": "fr-fr",
  "da": "da-dk",
  "no": "nb-no",
  "fi": "fi-fi",
  "nl": "nl-nl",
  "it": "it-it",
  "pt": "pt-br",
  "ja": "ja-jp",
  "ko": "ko-kr",
  "zh": "zh-cn",
  "ar": "ar-sa",
  "ru": "ru-ru",
  "pl": "pl-pl",
  "tr": "tr-tr",
  "hi": "hi-in"
}
```

Language tagging has **sentence-level coherence** — an embedded foreign word like "amigo" in English context stays tagged `"en"`.

### 3.7 Tags Array

Premiere Pro word objects require a `tags` array. Soniox has no equivalent. Always set `tags: []`.

---

## 4. API Configuration for the Converter

### 4.1 Authentication & Base URL

```
Base URL: https://api.soniox.com/v1
Auth header: Authorization: Bearer <SONIOX_API_KEY>
```

Regional endpoints also available: `api.eu.soniox.com` (EU), `api.jp.soniox.com` (Japan).

### 4.2 Async Workflow (4 Steps)

```
Step 1 (optional): POST /v1/files          → {"id": "<file_id>"}
Step 2:            POST /v1/transcriptions  → {"id": "...", "status": "queued"}
Step 3:            GET  /v1/transcriptions/{id}  → poll until status == "completed"
Step 4:            GET  /v1/transcriptions/{id}/transcript → {id, text, tokens}
```

Alternative to polling: set `webhook_url` in Step 2 to receive a POST when done.

### 4.3 Recommended Create-Transcription Request

For the Premiere Pro converter, all optional features should be enabled:

```json
POST /v1/transcriptions
Content-Type: application/json
Authorization: Bearer <API_KEY>

{
  "model": "stt-async-v4",
  "audio_url": "https://example.com/audio.mp3",
  "language_hints": ["en"],
  "enable_speaker_diarization": true,
  "enable_language_identification": true,
  "client_reference_id": "optional-tracking-id"
}
```

Or with a previously uploaded file:
```json
{
  "model": "stt-async-v4",
  "file_id": "<uuid-from-upload>",
  "language_hints": ["sv", "en"],
  "enable_speaker_diarization": true,
  "enable_language_identification": true
}
```

### 4.4 Models

| Model              | Status                                       | Recommendation       |
|--------------------|----------------------------------------------|----------------------|
| `stt-async-v4`     | **Active** — latest (released Jan 29, 2026)  | **Use this**          |
| `stt-async-v3`     | Active until Feb 28, 2026; auto-routes to v4 after | Legacy — still works |
| `stt-async-preview-v1` | Alias → `stt-async-v3`                  | Don't use             |

Note: Official Soniox example code (Python/Node.js) still references `stt-async-v3`. Both v3 and v4 have identical API contracts. Use `stt-async-v4` for best quality (improved diarization, normalization, noise handling, multilingual accuracy).

### 4.5 Audio Limits & Formats

- **Maximum duration**: 300 minutes (5 hours) per file — cannot be increased
- **Supported formats** (auto-detected, no config needed): `aac, aiff, amr, asf, flac, mp3, ogg, wav, webm, m4a, mp4`
- **Max pending transcriptions**: 100
- **Max total transcriptions**: 2,000 (pending + completed + failed)
- **Max file storage**: 10 GB across all uploaded files
- **Max uploaded files**: 1,000

Files are NOT auto-deleted — the converter should clean up via `DELETE /v1/transcriptions/{id}` and `DELETE /v1/files/{id}` after processing.

### 4.6 Transcription Status Values

When polling `GET /v1/transcriptions/{id}`:

| Status       | Meaning                                        | Action                |
|--------------|------------------------------------------------|-----------------------|
| `"queued"`   | Waiting to be processed                        | Continue polling      |
| `"processing"` | Currently being transcribed                  | Continue polling      |
| `"completed"` | Ready — fetch transcript                      | Call `/transcript`    |
| `"error"`    | Failed — check `error_message` field           | Handle error          |

---

## 5. Complete Verified Sample Response

Based on all documented fields and confirmed examples. This shows a multi-speaker transcript with diarization and language identification enabled:

```json
{
  "id": "73d4357d-cad2-4338-a60d-ec6f2044f721",
  "text": "How are you doing today? I am fantastic, thank you.",
  "tokens": [
    {"text": "How",      "start_ms": 120,  "end_ms": 250,  "confidence": 0.97, "speaker": "1", "language": "en"},
    {"text": " are",     "start_ms": 260,  "end_ms": 380,  "confidence": 0.95, "speaker": "1", "language": "en"},
    {"text": " you",     "start_ms": 390,  "end_ms": 510,  "confidence": 0.96, "speaker": "1", "language": "en"},
    {"text": " do",      "start_ms": 520,  "end_ms": 600,  "confidence": 0.93, "speaker": "1", "language": "en"},
    {"text": "ing",      "start_ms": 600,  "end_ms": 720,  "confidence": 0.94, "speaker": "1", "language": "en"},
    {"text": " to",      "start_ms": 730,  "end_ms": 790,  "confidence": 0.91, "speaker": "1", "language": "en"},
    {"text": "day",      "start_ms": 790,  "end_ms": 920,  "confidence": 0.96, "speaker": "1", "language": "en"},
    {"text": "?",        "start_ms": 920,  "end_ms": 940,  "confidence": 0.99, "speaker": "1", "language": "en"},
    {"text": "I",        "start_ms": 1200, "end_ms": 1260, "confidence": 0.98, "speaker": "2", "language": "en"},
    {"text": " am",      "start_ms": 1270, "end_ms": 1380, "confidence": 0.97, "speaker": "2", "language": "en"},
    {"text": " fan",     "start_ms": 1390, "end_ms": 1520, "confidence": 0.90, "speaker": "2", "language": "en"},
    {"text": "tastic",   "start_ms": 1520, "end_ms": 1780, "confidence": 0.93, "speaker": "2", "language": "en"},
    {"text": ",",        "start_ms": 1780, "end_ms": 1800, "confidence": 0.98, "speaker": "2", "language": "en"},
    {"text": " thank",   "start_ms": 1810, "end_ms": 1950, "confidence": 0.96, "speaker": "2", "language": "en"},
    {"text": " you",     "start_ms": 1960, "end_ms": 2100, "confidence": 0.97, "speaker": "2", "language": "en"},
    {"text": ".",        "start_ms": 2100, "end_ms": 2120, "confidence": 0.99, "speaker": "2", "language": "en"}
  ]
}
```

### 5.1 Expected Converter Output for Above Input

After assembly, the word-level objects (before Premiere Pro segment grouping) should be:

| text        | start    | duration | confidence | type        | eos   | speaker |
|-------------|----------|----------|------------|-------------|-------|---------|
| How         | 0.120    | 0.130    | 0.97       | word        | false | 1       |
| are         | 0.260    | 0.120    | 0.95       | word        | false | 1       |
| you         | 0.390    | 0.120    | 0.96       | word        | false | 1       |
| doing       | 0.520    | 0.200    | 0.93       | word        | false | 1       |
| today       | 0.730    | 0.190    | 0.91       | word        | true  | 1       |
| ?           | 0.920    | 0.020    | 0.99       | punctuation | false | 1       |
| I           | 1.200    | 0.060    | 0.98       | word        | false | 2       |
| am          | 1.270    | 0.110    | 0.97       | word        | false | 2       |
| fantastic   | 1.390    | 0.390    | 0.90       | word        | false | 2       |
| ,           | 1.780    | 0.020    | 0.98       | punctuation | false | 2       |
| thank       | 1.810    | 0.140    | 0.96       | word        | false | 2       |
| you         | 1.960    | 0.140    | 0.97       | word        | true  | 2       |
| .           | 2.100    | 0.020    | 0.99       | punctuation | false | 2       |

Notes on the assembly:
- "doing" = `" do"` + `"ing"` → `start_ms=520`, `end_ms=720`, confidence=min(0.93, 0.94)=0.93
- "today" = `" to"` + `"day"` → `start_ms=730`, `end_ms=920`, confidence=min(0.91, 0.96)=0.91
- "fantastic" = `" fan"` + `"tastic"` → `start_ms=1390`, `end_ms=1780`, confidence=min(0.90, 0.93)=0.90
- "today" gets `eos: true` because the next token is `"?"`
- "you" (second occurrence) gets `eos: true` because the next token is `"."`

---

## 6. Translation Tokens (When Translation Is Enabled)

If the create-transcription request includes a `translation` config, the token array will contain **additional translation tokens** interspersed with transcription tokens.

Translation tokens are identified by `"translation_status": "translation"` and:
- Have `language` set to the target language
- Have `source_language` set to the original spoken language
- Do **NOT** have `start_ms` / `end_ms` (no audio alignment)

Original spoken tokens get `"translation_status": "original"`. Tokens in a language not configured for translation get `"translation_status": "none"`.

**For the Premiere Pro converter**: Filter OUT all tokens where `translation_status === "translation"`. Only process tokens where `translation_status` is `"original"`, `"none"`, or the field is absent (when no translation is configured).

---

## 7. Implementation Checklist

For the agentic coder building the converter:

- [ ] Parse JSON response, extract `tokens` array
- [ ] **Filter out translation tokens** if `translation_status === "translation"`
- [ ] **Assemble sub-words into words** using leading-space rule (Section 2)
- [ ] **Classify tokens** as `type: "word"` or `type: "punctuation"`
- [ ] **Convert timestamps**: `start = start_ms / 1000.0`, `duration = (end_ms - start_ms) / 1000.0`
- [ ] **Aggregate confidence** across sub-word tokens (min or mean)
- [ ] **Infer `eos`** from sentence-ending punctuation (`.`, `?`, `!`)
- [ ] **Group by speaker** — detect `speaker` field changes, create segments
- [ ] **Generate speaker UUIDs** — map `"1"` → UUID v4 + display name
- [ ] **Map language codes** — ISO 639-1 → BCP-47 locale codes
- [ ] **Set `tags: []`** on all word objects
- [ ] **Clean up** — DELETE transcription and file after processing

---

## 8. Verification Sources

All claims in this document were verified against these official Soniox documentation pages on 2026-02-13:

| Page | URL |
|------|-----|
| Get Transcription Transcript (API Reference) | `soniox.com/docs/stt/api-reference/transcriptions/get_transcription_transcript` |
| Create Transcription (API Reference) | `soniox.com/docs/stt/api-reference/transcriptions/create_transcription` |
| Timestamps (Shared Concepts) | `soniox.com/docs/stt/concepts/timestamps` |
| Confidence Scores (Shared Concepts) | `soniox.com/docs/stt/concepts/confidence-scores` |
| Speaker Diarization (Shared Concepts) | `soniox.com/docs/stt/concepts/speaker-diarization` |
| Language Identification (Shared Concepts) | `soniox.com/docs/stt/concepts/language-identification` |
| Async Transcription (Guide) | `soniox.com/docs/stt/async/async-transcription` |
| Models & Changelog | `soniox.com/docs/stt/models` |
| Limits & Quotas (Async) | `soniox.com/docs/stt/async/limits-and-quotas` |
| WebSocket API (for async-vs-RT comparison) | `soniox.com/docs/stt/api-reference/websocket-api` |
| Full LLM Context File | `soniox.com/docs/llms-full.txt` |
| Official Python Example Code | `github.com/soniox/soniox_examples/.../soniox_async.py` |
| Official Node.js Example Code | `github.com/soniox/soniox_examples/.../soniox_async.js` |
