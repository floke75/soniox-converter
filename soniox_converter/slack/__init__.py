"""Slack bot integration for the Soniox transcript converter.

WHY: Users in Slack need to submit audio/video files for transcription
directly from their workspace. This package provides a Socket Mode bot
that watches a channel for file uploads, presents a Block Kit form for
configuration, and delivers transcription results back to the thread.

HOW: The bot runs as a separate process using slack-bolt's Socket Mode
adapter. It acts as an HTTP client to the local FastAPI transcription
API—uploading files, polling for status, and downloading results.

RULES:
- Bot is a separate process from the FastAPI server
- Communication with the API is via httpx HTTP calls
- Socket Mode requires SLACK_BOT_TOKEN and SLACK_APP_TOKEN
- All Slack actions must be ack()'d within 3 seconds
"""
