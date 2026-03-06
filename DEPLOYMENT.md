# Deployment Guide

## Production Server

- **Host:** $SONIOX_SERVER (DigitalOcean - set environment variable to your production server IP)
- **User:** root
- **Location:** `/opt/soniox-converter`
- **Services:** soniox-api (port 8000), soniox-slack

## Prerequisites

1. PR merged to `main` branch
2. SSH access to production server
3. All tests passing locally

## Deployment Steps

### 1. One-Line Deploy (Recommended)

```bash
# Note: Set SONIOX_SERVER environment variable to your production server IP
ssh root@$SONIOX_SERVER 'cd /opt/soniox-converter && \
  git pull origin main && \
  pip3 install -e . && \
  (pkill -f soniox-api || true) && (pkill -f soniox-slack || true) && \
  sleep 1 && \
  nohup soniox-api > /var/log/soniox-api.log 2>&1 & \
  nohup soniox-slack > /var/log/soniox-slack.log 2>&1 & \
  sleep 2 && \
  ps aux | grep -E "(soniox-api|soniox-slack)" | grep -v grep'
```

### 2. Manual Deploy (Step-by-Step)

```bash
# Connect to server (set SONIOX_SERVER environment variable to your production server IP)
ssh root@$SONIOX_SERVER

# Pull latest code
cd /opt/soniox-converter
git fetch origin
git checkout main
git pull origin main

# Install/update dependencies
pip3 install -e .

# Verify commit
git log --oneline -3

# Get current PIDs
ps aux | grep -E "(soniox-api|soniox-slack)" | grep -v grep

# Stop old services
pkill -f soniox-api || true
pkill -f soniox-slack || true

# Wait for clean shutdown
sleep 2

# Start new services
cd /opt/soniox-converter
nohup soniox-api > /var/log/soniox-api.log 2>&1 &
nohup soniox-slack > /var/log/soniox-slack.log 2>&1 &

# Verify new services started
sleep 2
ps aux | grep -E "(soniox-api|soniox-slack)" | grep -v grep
```

### 3. Verify Deployment

```bash
# Health check (from local machine or server)
curl http://$SONIOX_SERVER:8000/health

# Expected response:
# {"status":"ok","version":"0.1.0"}

# Check service PIDs changed
ps aux | grep -E "(soniox-api|soniox-slack)" | grep -v grep

# Monitor logs for errors
tail -50 /var/log/soniox-api.log
tail -50 /var/log/soniox-slack.log
```

## Rollback

**Prerequisite:** Production server must have git push access to GitHub (SSH key or credentials configured).

If deployment breaks production:

```bash
# Option A: Revert on server (requires git push credentials)
ssh root@$SONIOX_SERVER
cd /opt/soniox-converter
git log --oneline -10
git revert <bad-commit-hash>
git push origin main  # Requires write access to repo

# Restart services to apply rollback
(pkill -f soniox-api || true) && (pkill -f soniox-slack || true)
sleep 2
nohup soniox-api > /var/log/soniox-api.log 2>&1 &
nohup soniox-slack > /var/log/soniox-slack.log 2>&1 &

# Verify health
curl http://$SONIOX_SERVER:8000/health
```

```bash
# Option B: Revert on server, push from dev machine
# On server:
ssh root@$SONIOX_SERVER
cd /opt/soniox-converter
git revert <bad-commit-hash>  # Creates revert commit locally
exit

# On local machine:
git pull origin main
git push origin main  # Uses your local credentials
```

**Alternative:** If you need to hard-reset (use with caution):
```bash
git reset --hard <previous-commit-hash>
git push --force-with-lease origin main  # REQUIRED: Updates remote
```

## Troubleshooting

### Services Won't Start

**Check port conflicts:**
```bash
lsof -i :8000
```

**Check for zombie processes:**
```bash
ps aux | grep defunct
pkill -9 -f soniox-api
```

**View error logs:**
```bash
tail -100 /var/log/soniox-api.log
tail -100 /var/log/soniox-slack.log
```

### Old Code Still Running

**Verify deployment:**
```bash
cd /opt/soniox-converter
git log --oneline -1
```

**Check process working directory:**
```bash
lsof -p <PID> | grep soniox-converter
```

**Force restart:**
```bash
cd /opt/soniox-converter
pkill -9 -f soniox-api || true
pkill -9 -f soniox-slack || true
sleep 1
nohup soniox-api > /var/log/soniox-api.log 2>&1 &
nohup soniox-slack > /var/log/soniox-slack.log 2>&1 &
```

### API Returns 500 Errors

**Check imports:**
```bash
cd /opt/soniox-converter
python3 -c "from soniox_converter.server.app import app; print('OK')"
```

**Check dependencies:**
```bash
pip3 list | grep -E "(fastapi|uvicorn|soniox)"
```

**Run with verbose logging:**
```bash
pkill -f soniox-api || true
cd /opt/soniox-converter
python3 -m uvicorn soniox_converter.server.app:app --host 0.0.0.0 --port 8000 --log-level debug
```

## Post-Deployment Checklist

- [ ] Health endpoint returns 200 OK
- [ ] Both services show new PIDs (not old ones)
- [ ] Git log shows correct commit deployed
- [ ] Test API with sample transcription
- [ ] Monitor logs for errors (first 5 minutes)
- [ ] Verify Slack bot responds in test channel
- [ ] Check caption quality with test file

## Common Deployment Scenarios

### Caption Quality Improvements
After tuning caption algorithms:
1. Deploy normally
2. Run quality check: `python3 tests/tools/tune_social_captions.py --preset baseline`
3. Verify weak-word rate within threshold (< 5%)

### New Format Added
When adding new output formatters:
1. Deploy normally
2. Test with: `curl -X POST http://$SONIOX_SERVER:8000/transcriptions -F "file=@test.wav"`
3. Verify new format in output_files list

### Breaking API Changes
When modifying API contracts:
1. Coordinate with API consumers first
2. Deploy during low-traffic window
3. Monitor error rates closely
4. Have rollback ready

## Notes

- Services run with `nohup` (not systemd)
- Log rotation not configured (monitor log sizes)
- No automatic restart on crash (manual intervention required)
- Must restart from `/opt/soniox-converter` to pick up new code
- API serves on 0.0.0.0:8000 (publicly accessible)
