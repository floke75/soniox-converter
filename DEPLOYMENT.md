# Deployment Guide

## Production Server

- **Host:** 165.227.150.233 (DigitalOcean)
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
ssh root@165.227.150.233 'cd /opt/soniox-converter && \
  git pull origin main && \
  pkill -f soniox-api && pkill -f soniox-slack && \
  sleep 1 && \
  nohup soniox-api > /var/log/soniox-api.log 2>&1 & \
  nohup soniox-slack > /var/log/soniox-slack.log 2>&1 & \
  sleep 2 && \
  ps aux | grep -E "(soniox-api|soniox-slack)" | grep -v grep'
```

### 2. Manual Deploy (Step-by-Step)

```bash
# Connect to server
ssh root@165.227.150.233

# Pull latest code
cd /opt/soniox-converter
git fetch origin
git checkout main
git pull origin main

# Verify commit
git log --oneline -3

# Get current PIDs
ps aux | grep -E "(soniox-api|soniox-slack)" | grep -v grep

# Stop old services
pkill -f soniox-api
pkill -f soniox-slack

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
curl http://165.227.150.233:8000/health

# Expected response:
# {"status":"ok","version":"0.1.0"}

# Check service PIDs changed
ps aux | grep -E "(soniox-api|soniox-slack)" | grep -v grep

# Monitor logs for errors
tail -50 /var/log/soniox-api.log
tail -50 /var/log/soniox-slack.log
```

## Rollback

If deployment breaks production:

```bash
ssh root@165.227.150.233
cd /opt/soniox-converter

# Find previous working commit
git log --oneline -10

# Rollback
git checkout <previous-commit-hash>

# Restart services
pkill -f soniox-api && pkill -f soniox-slack
sleep 2
nohup soniox-api > /var/log/soniox-api.log 2>&1 &
nohup soniox-slack > /var/log/soniox-slack.log 2>&1 &

# Verify
curl http://165.227.150.233:8000/health
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
pkill -9 -f soniox-api
pkill -9 -f soniox-slack
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
pkill -f soniox-api
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
2. Test with: `curl -X POST http://165.227.150.233:8000/transcriptions -F "file=@test.wav"`
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
