"""Tests for the FastAPI transcription API.

WHY: Validates that all 7 API endpoints behave correctly — happy paths,
error cases, and edge cases. Uses FastAPI TestClient for synchronous
in-process testing with mocked Soniox API calls.

HOW: Each test function exercises one endpoint behavior. The Soniox API
client is mocked to avoid external dependencies. Tests create jobs via
the API, manipulate job state directly when needed, and verify response
status codes, bodies, and headers.

RULES:
- All tests use the FastAPI TestClient (synchronous)
- Soniox API is never called (all external calls are mocked)
- Each test is independent — no shared state between tests
- Tests cover: happy paths, 404 not found, 409 conflict, 400 bad request
- The job store is reset before each test via a fresh app instance
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from soniox_converter.server.app import app, job_store
from soniox_converter.server.jobs import JobStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_job_store():
    """Clear all jobs before each test to ensure isolation."""
    job_store._jobs.clear()
    yield
    # Clean up any temp directories created during tests
    for job in list(job_store._jobs.values()):
        if job.output_dir.exists():
            import shutil
            shutil.rmtree(job.output_dir, ignore_errors=True)
    job_store._jobs.clear()


@pytest.fixture
def client():
    """Create a TestClient for the FastAPI app.

    The background task runner is patched to prevent the real Soniox
    pipeline from running during tests. Tests that need to verify
    completed/failed states set job status directly via the store.
    """
    with patch(
        "soniox_converter.server.app._run_transcription_sync",
        new=lambda job_id, store: None,
    ):
        yield TestClient(app)


def _make_audio_file(name: str = "test.mp3", content: bytes = b"fake audio data"):
    """Create a fake audio file for upload testing."""
    return ("file", (name, io.BytesIO(content), "audio/mpeg"))


# ---------------------------------------------------------------------------
# POST /transcriptions
# ---------------------------------------------------------------------------


class TestCreateTranscription:
    """Tests for POST /transcriptions endpoint."""

    def test_submit_job_returns_201(self, client):
        """Submitting a valid file returns 201 with job ID and pending status."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
            data={"primary_language": "sv", "diarization": "true"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["status"] == "pending"
        assert body["filename"] == "test.mp3"

    def test_submit_job_with_output_formats(self, client):
        """Submitting with specific output formats stores them in config."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
            data={"output_formats": "premiere_pro,srt_captions"},
        )
        assert resp.status_code == 201
        job_id = resp.json()["id"]
        job = job_store.get_job(job_id)
        assert job is not None
        assert job.config["output_formats"] == ["premiere_pro", "srt_captions"]

    def test_submit_job_saves_uploaded_file(self, client):
        """The uploaded file is saved to the job's output directory."""
        content = b"test audio content 12345"
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file(content=content)],
        )
        assert resp.status_code == 201
        job_id = resp.json()["id"]
        job = job_store.get_job(job_id)
        assert job is not None
        saved_file = job.output_dir / "test.mp3"
        assert saved_file.exists()
        assert saved_file.read_bytes() == content

    def test_reject_unsupported_file_type(self, client):
        """Uploading an unsupported file type returns 400."""
        resp = client.post(
            "/transcriptions",
            files=[("file", ("test.xyz", io.BytesIO(b"data"), "application/octet-stream"))],
        )
        assert resp.status_code == 400
        assert "Unsupported file type" in resp.json()["detail"]

    def test_reject_invalid_output_format(self, client):
        """Specifying an unknown output format returns 400."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
            data={"output_formats": "premiere_pro,nonexistent_format"},
        )
        assert resp.status_code == 400
        assert "Unknown output format" in resp.json()["detail"]

    def test_default_config_values(self, client):
        """Default config values are applied when not specified."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
        )
        assert resp.status_code == 201
        job_id = resp.json()["id"]
        job = job_store.get_job(job_id)
        assert job is not None
        assert job.config["primary_language"] == "sv"
        assert job.config["diarization"] is True
        assert job.config["output_formats"] is None


# ---------------------------------------------------------------------------
# GET /transcriptions/{id}
# ---------------------------------------------------------------------------


class TestGetTranscription:
    """Tests for GET /transcriptions/{id} endpoint."""

    def test_get_pending_job(self, client):
        """Getting a pending job returns its status and config."""
        # Create a job
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
            data={"primary_language": "en", "diarization": "false"},
        )
        job_id = resp.json()["id"]

        # Get job status
        resp = client.get("/transcriptions/{}".format(job_id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == job_id
        assert body["status"] == "pending"
        assert body["filename"] == "test.mp3"
        assert body["config"]["primary_language"] == "en"

    def test_get_nonexistent_job(self, client):
        """Getting a nonexistent job returns 404."""
        resp = client.get("/transcriptions/nonexistent-id-12345")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_get_completed_job_with_files(self, client):
        """A completed job includes output_files in the response."""
        # Create and manually complete a job
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
        )
        job_id = resp.json()["id"]
        job_store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            output_files=["test-transcript.json", "test-captions.srt"],
        )

        resp = client.get("/transcriptions/{}".format(job_id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["output_files"] == ["test-transcript.json", "test-captions.srt"]

    def test_get_failed_job_with_error(self, client):
        """A failed job includes the error message."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
        )
        job_id = resp.json()["id"]
        job_store.update_job(
            job_id,
            status=JobStatus.FAILED,
            error="Soniox API error 500: Internal server error",
        )

        resp = client.get("/transcriptions/{}".format(job_id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert "500" in body["error"]


# ---------------------------------------------------------------------------
# GET /transcriptions/{id}/files
# ---------------------------------------------------------------------------


class TestListTranscriptionFiles:
    """Tests for GET /transcriptions/{id}/files endpoint."""

    def test_list_files_for_completed_job(self, client):
        """Listing files for a completed job returns file metadata."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
        )
        job_id = resp.json()["id"]
        job = job_store.get_job(job_id)

        # Write a fake output file
        out_file = job.output_dir / "test-transcript.json"
        out_file.write_text('{"test": true}', encoding="utf-8")
        job_store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            output_files=["test-transcript.json"],
        )

        resp = client.get("/transcriptions/{}/files".format(job_id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == job_id
        assert len(body["files"]) == 1
        assert body["files"][0]["filename"] == "test-transcript.json"
        assert body["files"][0]["media_type"] == "application/json"
        assert body["files"][0]["size"] > 0

    def test_list_files_not_completed(self, client):
        """Listing files for a non-completed job returns 409."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
        )
        job_id = resp.json()["id"]

        resp = client.get("/transcriptions/{}/files".format(job_id))
        assert resp.status_code == 409
        assert "not completed" in resp.json()["detail"].lower()

    def test_list_files_nonexistent_job(self, client):
        """Listing files for a nonexistent job returns 404."""
        resp = client.get("/transcriptions/nonexistent-id/files")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /transcriptions/{id}/files/{filename}
# ---------------------------------------------------------------------------


class TestDownloadFile:
    """Tests for GET /transcriptions/{id}/files/{filename} endpoint."""

    def test_download_file_success(self, client):
        """Downloading an output file returns the file content."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
        )
        job_id = resp.json()["id"]
        job = job_store.get_job(job_id)

        # Write a fake output file
        file_content = '{"segments": []}'
        out_file = job.output_dir / "test-transcript.json"
        out_file.write_text(file_content, encoding="utf-8")
        job_store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            output_files=["test-transcript.json"],
        )

        resp = client.get("/transcriptions/{}/files/test-transcript.json".format(job_id))
        assert resp.status_code == 200
        assert resp.text == file_content
        assert resp.headers["content-type"] == "application/json"
        assert "attachment" in resp.headers.get("content-disposition", "")

    def test_download_srt_file(self, client):
        """Downloading an SRT file returns text/plain content type."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
        )
        job_id = resp.json()["id"]
        job = job_store.get_job(job_id)

        srt_content = "1\n00:00:00,000 --> 00:00:01,000\nHello\n"
        out_file = job.output_dir / "test-captions.srt"
        out_file.write_text(srt_content, encoding="utf-8")
        job_store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            output_files=["test-captions.srt"],
        )

        resp = client.get("/transcriptions/{}/files/test-captions.srt".format(job_id))
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]

    def test_download_file_not_in_job(self, client):
        """Downloading a file not listed in output_files returns 404."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
        )
        job_id = resp.json()["id"]
        job_store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            output_files=["test-transcript.json"],
        )

        resp = client.get("/transcriptions/{}/files/nonexistent.txt".format(job_id))
        assert resp.status_code == 404

    def test_download_file_job_not_completed(self, client):
        """Downloading a file from an incomplete job returns 409."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
        )
        job_id = resp.json()["id"]

        resp = client.get("/transcriptions/{}/files/any-file.json".format(job_id))
        assert resp.status_code == 409

    def test_download_file_nonexistent_job(self, client):
        """Downloading from a nonexistent job returns 404."""
        resp = client.get("/transcriptions/nonexistent-id/files/any.json")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /transcriptions/{id}
# ---------------------------------------------------------------------------


class TestDeleteTranscription:
    """Tests for DELETE /transcriptions/{id} endpoint."""

    def test_delete_job(self, client):
        """Deleting an existing job returns 204 and removes it."""
        resp = client.post(
            "/transcriptions",
            files=[_make_audio_file()],
        )
        job_id = resp.json()["id"]

        resp = client.delete("/transcriptions/{}".format(job_id))
        assert resp.status_code == 204

        # Verify job is gone
        resp = client.get("/transcriptions/{}".format(job_id))
        assert resp.status_code == 404

    def test_delete_nonexistent_job(self, client):
        """Deleting a nonexistent job returns 404."""
        resp = client.delete("/transcriptions/nonexistent-id-12345")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /formats
# ---------------------------------------------------------------------------


class TestListFormats:
    """Tests for GET /formats endpoint."""

    def test_list_formats_returns_all(self, client):
        """The formats endpoint returns all registered formatters."""
        resp = client.get("/formats")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 4  # premiere_pro, plain_text, kinetic_words, srt_captions

        keys = {f["key"] for f in body}
        assert "premiere_pro" in keys
        assert "plain_text" in keys
        assert "kinetic_words" in keys
        assert "srt_captions" in keys

    def test_format_info_structure(self, client):
        """Each format info has key, name, and suffix fields."""
        resp = client.get("/formats")
        body = resp.json()
        for fmt in body:
            assert "key" in fmt
            assert "name" in fmt
            assert "suffix" in fmt
            assert isinstance(fmt["key"], str)
            assert isinstance(fmt["name"], str)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests for GET /health endpoint."""

    def test_health_returns_ok(self, client):
        """Health check returns status ok and version."""
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["version"] == "0.1.0"


# ---------------------------------------------------------------------------
# OpenAPI schema validation
# ---------------------------------------------------------------------------


class TestOpenAPISchema:
    """Tests for OpenAPI schema generation."""

    def test_openapi_schema_generates(self, client):
        """The OpenAPI schema generates without errors."""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["info"]["title"] == "Soniox Transcript Converter API"
        assert schema["info"]["version"] == "0.1.0"

    def test_all_endpoints_in_schema(self, client):
        """All 7 endpoints appear in the OpenAPI schema."""
        resp = client.get("/openapi.json")
        schema = resp.json()
        paths = schema["paths"]

        assert "/transcriptions" in paths
        assert "post" in paths["/transcriptions"]

        assert "/transcriptions/{job_id}" in paths
        assert "get" in paths["/transcriptions/{job_id}"]
        assert "delete" in paths["/transcriptions/{job_id}"]

        assert "/transcriptions/{job_id}/files" in paths
        assert "/transcriptions/{job_id}/files/{filename}" in paths
        assert "/formats" in paths
        assert "/health" in paths

    def test_endpoints_have_descriptions(self, client):
        """Every endpoint has a summary and description."""
        resp = client.get("/openapi.json")
        schema = resp.json()
        paths = schema["paths"]

        for path, methods in paths.items():
            for method, spec in methods.items():
                if method in ("get", "post", "put", "delete", "patch"):
                    assert "summary" in spec, "Missing summary for {} {}".format(
                        method.upper(), path
                    )
                    assert "description" in spec, "Missing description for {} {}".format(
                        method.upper(), path
                    )

    def test_schema_has_tags(self, client):
        """Endpoints are grouped by tags."""
        resp = client.get("/openapi.json")
        schema = resp.json()
        paths = schema["paths"]

        for path, methods in paths.items():
            for method, spec in methods.items():
                if method in ("get", "post", "put", "delete", "patch"):
                    assert "tags" in spec, "Missing tags for {} {}".format(
                        method.upper(), path
                    )
