# soniox-converter

## Goal

`soniox-converter` turns Soniox async transcription output into editor-facing
deliverables and exposes the same pipeline through three entry points:

- CLI for local operator workflows
- FastAPI for service-to-service use
- Slack Socket Mode bot for in-thread transcription delivery

The codebase is intentionally documented for human maintainers and LLM coding
agents. Prefer explicit defaults, traceable invariants, and runnable commands.

## What the system currently does

- Transcribes supported audio/video files with Soniox
- Produces multiple output formats from the same transcript IR
- Accepts optional context via script text, terms, and general key/value hints
- Exposes an HTTP API with OpenAPI docs at `/docs`
- Runs a Slack modal workflow: upload file -> click button -> configure in modal -> receive files in thread

## Interface defaults that matter

Most runtime defaults come from `soniox_converter/config.py` and can be
overridden via environment variables.

### CLI and HTTP API

- Primary language defaults to `sv` via `DEFAULT_PRIMARY_LANGUAGE`
- Diarization defaults to enabled via `DEFAULT_DIARIZATION=true`
- If `output_formats` is omitted, the app uses `DEFAULT_FORMATTERS` from
  `soniox_converter/formatters/__init__.py`
- Current `DEFAULT_FORMATTERS` value:
  - `premiere_pro`
  - `plain_text`
  - `kinetic_words`
  - `srt_broadcast`
  - `srt_social`
- Deprecated `srt_captions` is still accepted when explicitly requested, but it
  generates both split SRT outputs and is not part of the default set

### Slack bot

- Active UX is modal-first, not the older in-channel configuration form
- The bot posts a compact thread message with a `Transkribera` button
- Clicking the button opens the modal defined in `soniox_converter/slack/messages.py`
- Modal defaults:
  - primary language `sv` (currently hardcoded in `soniox_converter/slack/messages.py`)
  - secondary language `en` (currently hardcoded in `soniox_converter/slack/messages.py`)
  - diarization enabled (currently hardcoded in `soniox_converter/slack/messages.py`)
  - `premiere_pro`, `srt_broadcast`, and `srt_social` preselected
- Legacy Block Kit form handlers remain registered only for compatibility with
  older interactive payloads

### Context handling

- Context is assembled in `soniox_converter/core/context.py`
- Supported inputs:
  - script text from `.txt` companion file or API upload
  - terms list
  - general context key/value pairs
- Total context payload is limited to 10,000 characters

## Available formats

| Format | Purpose | Output |
| --- | --- | --- |
| `premiere_pro` | Premiere Pro transcript JSON | `-transcript.json` |
| `plain_text` | Plain text transcript | `-transcript.txt` |
| `kinetic_words` | Word-timed kinetic reveal SRTs | `-kinetic-row1.srt`, `-kinetic-row2.srt`, `-kinetic-row3.srt` |
| `srt_broadcast` | 16:9 / 2-line subtitle output | `-broadcast.srt` |
| `srt_social` | 9:16 / 1-line subtitle output | `-social.srt` |
| `srt_captions` | Deprecated compatibility key; generates both split SRT outputs | `-broadcast.srt` + `-social.srt` |

## Quick start

### Install

```bash
pip install -e .
```

### Required environment

```bash
export SONIOX_API_KEY=your_api_key_here
```

Add these when using Slack:

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
```

### Optional environment overrides

```bash
export SONIOX_BASE_URL=https://api.soniox.com/v1
export SONIOX_MODEL=stt-async-v4
export DEFAULT_PRIMARY_LANGUAGE=sv
export DEFAULT_SECONDARY_LANGUAGE=en
export DEFAULT_DIARIZATION=true
export CONVERTER_API_URL=http://localhost:8000
```

- `SONIOX_BASE_URL` and `SONIOX_MODEL` override the upstream Soniox API target.
- `DEFAULT_PRIMARY_LANGUAGE`, `DEFAULT_SECONDARY_LANGUAGE`, and
  `DEFAULT_DIARIZATION` control the CLI and HTTP API smart defaults sourced from
  `soniox_converter/config.py`; Slack modal defaults are currently hardcoded in
  `soniox_converter/slack/messages.py`.
- `CONVERTER_API_URL` controls how the Slack bot reaches the HTTP API when they
  do not share the same host/port.

### Run the CLI

```bash
mkdir -p ./output
python -m soniox_converter input.wav --output-dir ./output
python -m soniox_converter input.wav --output-dir ./output --formats srt_social,srt_broadcast
```

### Run the HTTP API

```bash
soniox-api
```

- Base URL: `http://localhost:8000`
- OpenAPI docs: `http://localhost:8000/docs`

Minimal submission example:

```bash
curl -X POST http://localhost:8000/transcriptions \
  -F "file=@input.wav" \
  -F "output_formats=premiere_pro,srt_broadcast"
```

### Run the Slack bot

```bash
soniox-slack
```

## Caption quality guidance

Do not treat historical percentages in old notes or review comments as product
guarantees. Use the current tests and tuning tools as the source of truth.

- Social-media regression suite (`tests/test_caption_tuning.py`) currently enforces:
  - hard case below 10% weak-word endings
  - overall corpus below 5%
  - hard max 30 characters per social caption block
- Broadcast quality is evaluated heuristically with
  `tests/tools/tune_broadcast_captions.py`
  - weak-word rate, line balance, single-line usage, and punctuation behavior
  - reported numbers are measurements of the current corpus, not API-level SLAs

## Development and verification

### Primary checks

```bash
python3 -m pytest tests/test_api.py -v --tb=short
python3 -m pytest tests/test_slack_bot.py -v --tb=short
python3 -m pytest tests/test_caption_tuning.py -v --tb=short
python3 -m pytest tests/ -v --tb=short
python3 -c "from soniox_converter.server.app import app; print(app.title)"
```

### Caption tuning tools

```bash
python3 tests/tools/tune_social_captions.py --all
python3 tests/tools/tune_social_captions.py --compare baseline final
python3 tests/tools/tune_broadcast_captions.py --all
```

See `tests/tools/README_BROADCAST_TUNING.md` for broadcast-tuning heuristics.

## Deployment model

The repository currently supports two runtime shapes:

1. Direct Python entry points (`soniox-api`, `soniox-slack`)
2. A single container that runs both processes under `supervisord`

What is in the repo today:

- `Dockerfile` builds and tests the project, then runs both services in one container
- `supervisord.conf` starts and auto-restarts `soniox-api` and `soniox-slack`
- `docker-compose.yml` is a local convenience wrapper that builds the image,
  publishes port `8000`, and loads `.env`

See [DEPLOYMENT.md](./DEPLOYMENT.md) for the operator runbook.

## LLM agent map

If you are changing behavior, start here:

- `soniox_converter/cli.py` — CLI entry point and default output behavior
- `soniox_converter/config.py` — env var defaults, language map, and supported file formats
- `soniox_converter/server/app.py` — HTTP API contract and multipart form parsing
- `soniox_converter/server/models.py` — request/response docs surfaced via OpenAPI
- `soniox_converter/slack/bot.py` — Slack event handling and API handoff
- `soniox_converter/slack/messages.py` — modal layout, labels, and Slack defaults
- `soniox_converter/core/context.py` — context assembly and size limits
- `soniox_converter/formatters/__init__.py` — formatter registry and `DEFAULT_FORMATTERS`
- `format_captions/presets.py` — caption heuristics and tuning comments

## High-level architecture

```text
Slack / CLI / HTTP client
        -> soniox_converter.server.app or soniox_converter.cli
        -> Soniox async transcription
        -> transcript assembly / context handling
        -> formatter registry
        -> editor-facing outputs
```

## Support

- Issues: https://github.com/floke75/soniox-converter/issues
- API health endpoint: `GET /health`
