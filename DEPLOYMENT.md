# Deployment Guide

## Intent

This guide documents the deployment shapes that are actually supported by the
repository today.

Code-backed runtime options in-tree:

1. Direct Python entry points: `soniox-api` and `soniox-slack`
2. Single container runtime via `Dockerfile` + `supervisord.conf`

What is not defined in the repo:

- systemd units
- Kubernetes manifests
- reverse proxy / TLS setup
- persistent job storage beyond the app's current filesystem + in-memory model

## Runtime artifacts in the repo

- `pyproject.toml` exposes:
  - `soniox-api = soniox_converter.server.app:run_api`
  - `soniox-slack = soniox_converter.slack.bot:main`
- `Dockerfile` builds the project, runs tests during the builder stage, and
  starts both services in the final image
- `supervisord.conf` runs both services and restarts them automatically inside
  the container
- `docker-compose.yml` is a local convenience file that builds the image,
  exposes port `8000`, and loads `.env`

## Required environment

Minimum:

```bash
SONIOX_API_KEY=...
```

Add these for Slack deployments:

```bash
SLACK_BOT_TOKEN=...
SLACK_APP_TOKEN=...
SLACK_CHANNEL_ID=...   # optional filter
```

Optional:

```bash
CONVERTER_API_URL=http://localhost:8000
```

## Mode A: Host-managed Python processes

Use this when the server runs the repo directly from a checkout.

### Update code and install

```bash
cd /opt/soniox-converter
git fetch origin
git checkout main
git pull origin main
pip3 install -e .
```

### Start services

The repo provides the entry points, but not a mandated host process manager.
You can run them under `nohup`, `tmux`, `systemd`, or another supervisor.

Example with `nohup`:

```bash
cd /opt/soniox-converter
nohup soniox-api > /var/log/soniox-api.log 2>&1 &
nohup soniox-slack > /var/log/soniox-slack.log 2>&1 &
```

Restart example:

```bash
pkill -f soniox-api || true
pkill -f soniox-slack || true
sleep 2
cd /opt/soniox-converter
nohup soniox-api > /var/log/soniox-api.log 2>&1 &
nohup soniox-slack > /var/log/soniox-slack.log 2>&1 &
```

## Mode B: Container runtime

Use this when you want the repo-defined container behavior.

### Build and run with Docker

```bash
docker build -t soniox-converter .
docker run --rm -p 8000:8000 --env-file .env soniox-converter
```

Notes:

- The final container starts `supervisord`
- `supervisord` runs both `soniox-api` and `soniox-slack`
- Container logs are written to stdout/stderr via supervisor config

### Local compose convenience

```bash
docker compose up --build
```

Current `docker-compose.yml` scope is intentionally small:

- one service
- port `8000:8000`
- `.env` loading

It is useful for local validation, but it is not a full production stack.

## Verification after deploy

### API checks

```bash
curl http://$SONIOX_SERVER:8000/health
curl http://$SONIOX_SERVER:8000/formats
python3 -c "from soniox_converter.server.app import app; print(app.title)"
```

Expected outcomes:

- `/health` returns HTTP 200
- `/formats` lists registered formatters, including deprecated `srt_captions`
- import smoke succeeds without traceback

### Process checks

Host-managed:

```bash
ps aux | grep -E "(soniox-api|soniox-slack)" | grep -v grep
tail -50 /var/log/soniox-api.log
tail -50 /var/log/soniox-slack.log
```

Container-managed:

```bash
docker ps
docker logs <container_id> --tail 100
```

### Slack smoke test

- Upload a supported audio/video file in the target channel
- Confirm the bot posts the compact `Transkribera` button message
- Click the button and confirm the modal opens
- Submit a small file and verify outputs are posted back into the thread

## Rollback

Rollback depends on the deployment mode, but the principle is the same:
return to the last known-good revision or image, then restart the services.

### Host-managed rollback

```bash
cd /opt/soniox-converter
git log --oneline -10
git checkout <known-good-commit>
pip3 install -e .
pkill -f soniox-api || true
pkill -f soniox-slack || true
sleep 2
nohup soniox-api > /var/log/soniox-api.log 2>&1 &
nohup soniox-slack > /var/log/soniox-slack.log 2>&1 &
```

### Container rollback

```bash
docker images | grep soniox-converter
docker stop <container_id>
docker run --rm -p 8000:8000 --env-file .env <known-good-image>
```

## Troubleshooting

### API will not start

```bash
lsof -i :8000
python3 -c "from soniox_converter.server.app import app; print('OK')"
```

### Slack bot will not start

Check that `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` are present in the runtime
environment. The bot entry point raises immediately if either is missing.

### Old code appears to still be running

```bash
cd /opt/soniox-converter
git log --oneline -1
```

If you are using host-managed processes, confirm they were restarted from the
same checkout you just updated.

## Operator reminders

- `nohup` is an example host strategy, not a requirement imposed by the code
- Container mode has automatic child-process restart through supervisor
- Host-managed mode only has auto-restart if you provide your own process manager
- The API listens on port `8000`
