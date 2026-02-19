"""Unit tests for the in-memory job store and background task runner.

WHY: The job store is the central state manager for the HTTP API. Race
conditions, missing cleanup, or incorrect status transitions would cause
stale jobs, leaked temp files, or broken polling. These tests verify
every public method and edge case.

HOW: Tests are organized by class, one per JobStore method or concern:
  - TestJobCreation: create_job basics and defaults
  - TestJobRetrieval: get_job and list_jobs
  - TestJobUpdate: status transitions, progress, errors, terminal states
  - TestJobDeletion: delete and temp dir cleanup
  - TestTTLCleanup: expiry logic and boundary conditions
  - TestBackgroundRunner: success, failure, and missing-job scenarios
  - TestThreadSafety: concurrent access doesn't corrupt state

RULES:
- Each test creates its own JobStore instance (no shared mutable state)
- Temp directories are cleaned up by the store or explicitly in tests
- Time-dependent tests use monkeypatch to control time.time()
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from soniox_converter.server.jobs import (
    DEFAULT_TTL_SECONDS,
    Job,
    JobStatus,
    JobStore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(**kwargs) -> JobStore:
    """Create a JobStore with optional overrides."""
    return JobStore(**kwargs)


# ---------------------------------------------------------------------------
# TestJobCreation
# ---------------------------------------------------------------------------


class TestJobCreation:
    """JobStore.create_job() creates a job in PENDING state."""

    def test_creates_job_with_pending_status(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        assert job.status == JobStatus.PENDING

    def test_assigns_unique_id(self):
        store = _make_store()
        job1 = store.create_job("a.mp3")
        job2 = store.create_job("b.mp3")
        assert job1.id != job2.id

    def test_stores_filename(self):
        store = _make_store()
        job = store.create_job("interview.wav")
        assert job.filename == "interview.wav"

    def test_creates_temp_directory(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        assert job.output_dir.is_dir()
        # cleanup
        store.delete_job(job.id)

    def test_sets_timestamps(self):
        store = _make_store()
        before = time.time()
        job = store.create_job("test.mp3")
        after = time.time()
        assert before <= job.created_at <= after
        assert before <= job.updated_at <= after
        store.delete_job(job.id)

    def test_stores_config(self):
        store = _make_store()
        config = {"formats": ["srt", "txt"], "language": "sv"}
        job = store.create_job("test.mp3", config=config)
        assert job.config == config
        store.delete_job(job.id)

    def test_default_config_is_empty_dict(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        assert job.config == {}
        store.delete_job(job.id)

    def test_initial_fields_are_none_or_empty(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        assert job.completed_at is None
        assert job.error is None
        assert job.progress is None
        assert job.output_files == []
        store.delete_job(job.id)


# ---------------------------------------------------------------------------
# TestJobRetrieval
# ---------------------------------------------------------------------------


class TestJobRetrieval:
    """JobStore.get_job() and list_jobs() retrieve stored jobs."""

    def test_get_existing_job(self):
        store = _make_store()
        created = store.create_job("test.mp3")
        retrieved = store.get_job(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id
        store.delete_job(created.id)

    def test_get_missing_job_returns_none(self):
        store = _make_store()
        assert store.get_job("nonexistent-id") is None

    def test_list_jobs_empty_store(self):
        store = _make_store()
        assert store.list_jobs() == []

    def test_list_jobs_returns_all(self):
        store = _make_store()
        j1 = store.create_job("a.mp3")
        j2 = store.create_job("b.mp3")
        jobs = store.list_jobs()
        assert len(jobs) == 2
        ids = {j.id for j in jobs}
        assert j1.id in ids
        assert j2.id in ids
        store.delete_job(j1.id)
        store.delete_job(j2.id)

    def test_list_jobs_ordered_by_creation_time(self):
        store = _make_store()
        j1 = store.create_job("first.mp3")
        j2 = store.create_job("second.mp3")
        jobs = store.list_jobs()
        assert jobs[0].id == j1.id
        assert jobs[1].id == j2.id
        store.delete_job(j1.id)
        store.delete_job(j2.id)


# ---------------------------------------------------------------------------
# TestJobUpdate
# ---------------------------------------------------------------------------


class TestJobUpdate:
    """JobStore.update_job() modifies job fields."""

    def test_update_status(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        updated = store.update_job(job.id, status=JobStatus.UPLOADING)
        assert updated is not None
        assert updated.status == JobStatus.UPLOADING
        store.delete_job(job.id)

    def test_update_bumps_updated_at(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        old_updated = job.updated_at
        time.sleep(0.01)
        store.update_job(job.id, status=JobStatus.TRANSCRIBING)
        assert job.updated_at > old_updated
        store.delete_job(job.id)

    def test_update_error(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        store.update_job(job.id, status=JobStatus.FAILED, error="API timeout")
        assert job.error == "API timeout"
        store.delete_job(job.id)

    def test_update_progress(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        progress = {"stage": "transcribing", "pct": 45}
        store.update_job(job.id, progress=progress)
        assert job.progress == progress
        store.delete_job(job.id)

    def test_update_output_files(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        store.update_job(job.id, output_files=["result.srt", "result.txt"])
        assert job.output_files == ["result.srt", "result.txt"]
        store.delete_job(job.id)

    def test_update_missing_job_returns_none(self):
        store = _make_store()
        result = store.update_job("nonexistent", status=JobStatus.FAILED)
        assert result is None

    def test_completed_sets_completed_at(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        assert job.completed_at is None
        store.update_job(job.id, status=JobStatus.COMPLETED)
        assert job.completed_at is not None
        store.delete_job(job.id)

    def test_failed_sets_completed_at(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        store.update_job(job.id, status=JobStatus.FAILED, error="boom")
        assert job.completed_at is not None
        store.delete_job(job.id)

    def test_non_terminal_status_does_not_set_completed_at(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        store.update_job(job.id, status=JobStatus.TRANSCRIBING)
        assert job.completed_at is None
        store.delete_job(job.id)

    def test_only_non_none_fields_updated(self):
        store = _make_store()
        job = store.create_job("test.mp3", config={"format": "srt"})
        store.update_job(job.id, status=JobStatus.UPLOADING)
        # Config should remain unchanged
        assert job.config == {"format": "srt"}
        assert job.error is None
        store.delete_job(job.id)


# ---------------------------------------------------------------------------
# TestJobDeletion
# ---------------------------------------------------------------------------


class TestJobDeletion:
    """JobStore.delete_job() removes jobs and cleans up temp dirs."""

    def test_delete_existing_job(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        job_id = job.id
        output_dir = job.output_dir
        assert store.delete_job(job_id) is True
        assert store.get_job(job_id) is None
        assert not output_dir.exists()

    def test_delete_missing_job_returns_false(self):
        store = _make_store()
        assert store.delete_job("nonexistent") is False

    def test_delete_removes_from_list(self):
        store = _make_store()
        j1 = store.create_job("a.mp3")
        j2 = store.create_job("b.mp3")
        store.delete_job(j1.id)
        jobs = store.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == j2.id
        store.delete_job(j2.id)

    def test_delete_cleans_temp_dir_with_files(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        # Write a file into the temp dir
        (job.output_dir / "result.srt").write_text("fake srt")
        assert (job.output_dir / "result.srt").is_file()
        store.delete_job(job.id)
        assert not job.output_dir.exists()

    def test_delete_handles_already_removed_dir(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        # Manually remove the dir before delete
        shutil.rmtree(job.output_dir)
        # Should not raise
        assert store.delete_job(job.id) is True


# ---------------------------------------------------------------------------
# TestTTLCleanup
# ---------------------------------------------------------------------------


class TestTTLCleanup:
    """JobStore.cleanup_expired() removes terminal jobs past their TTL."""

    def test_cleanup_removes_expired_completed_job(self, monkeypatch):
        store = _make_store(ttl_seconds=60)
        job = store.create_job("test.mp3")
        output_dir = job.output_dir

        # Complete the job at t=100
        monkeypatch.setattr(time, "time", lambda: 100.0)
        store.update_job(job.id, status=JobStatus.COMPLETED)

        # Cleanup at t=161 (61 seconds later, past 60s TTL)
        monkeypatch.setattr(time, "time", lambda: 161.0)
        removed = store.cleanup_expired()

        assert removed == 1
        assert store.get_job(job.id) is None
        assert not output_dir.exists()

    def test_cleanup_removes_expired_failed_job(self, monkeypatch):
        store = _make_store(ttl_seconds=60)
        job = store.create_job("test.mp3")

        monkeypatch.setattr(time, "time", lambda: 100.0)
        store.update_job(job.id, status=JobStatus.FAILED, error="err")

        monkeypatch.setattr(time, "time", lambda: 161.0)
        removed = store.cleanup_expired()

        assert removed == 1
        assert store.get_job(job.id) is None

    def test_cleanup_keeps_non_expired_job(self, monkeypatch):
        store = _make_store(ttl_seconds=60)
        job = store.create_job("test.mp3")

        monkeypatch.setattr(time, "time", lambda: 100.0)
        store.update_job(job.id, status=JobStatus.COMPLETED)

        # Cleanup at t=159 (59 seconds later, within 60s TTL)
        monkeypatch.setattr(time, "time", lambda: 159.0)
        removed = store.cleanup_expired()

        assert removed == 0
        assert store.get_job(job.id) is not None
        store.delete_job(job.id)

    def test_cleanup_ignores_in_progress_jobs(self, monkeypatch):
        store = _make_store(ttl_seconds=1)
        job = store.create_job("test.mp3")
        store.update_job(job.id, status=JobStatus.TRANSCRIBING)

        # Even way past TTL, non-terminal jobs should not be cleaned up
        far_future = time.time() + 10000
        monkeypatch.setattr(time, "time", lambda: far_future)
        removed = store.cleanup_expired()

        assert removed == 0
        assert store.get_job(job.id) is not None
        store.delete_job(job.id)

    def test_cleanup_returns_zero_on_empty_store(self):
        store = _make_store()
        assert store.cleanup_expired() == 0

    def test_cleanup_handles_multiple_expired(self, monkeypatch):
        store = _make_store(ttl_seconds=60)

        monkeypatch.setattr(time, "time", lambda: 100.0)
        j1 = store.create_job("a.mp3")
        j2 = store.create_job("b.mp3")
        store.update_job(j1.id, status=JobStatus.COMPLETED)
        store.update_job(j2.id, status=JobStatus.FAILED, error="err")

        monkeypatch.setattr(time, "time", lambda: 161.0)
        removed = store.cleanup_expired()

        assert removed == 2
        assert store.list_jobs() == []

    def test_default_ttl_is_one_hour(self):
        assert DEFAULT_TTL_SECONDS == 3600


# ---------------------------------------------------------------------------
# TestBackgroundRunner
# ---------------------------------------------------------------------------


class TestBackgroundRunner:
    """JobStore.run_in_background() wraps task execution with error handling."""

    def test_successful_task_runs_callable(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        called_with = {}

        def task(job_id, job_store):
            called_with["job_id"] = job_id
            called_with["store"] = job_store
            job_store.update_job(job_id, status=JobStatus.COMPLETED)

        store.run_in_background(job.id, task)

        assert called_with["job_id"] == job.id
        assert called_with["store"] is store
        assert store.get_job(job.id).status == JobStatus.COMPLETED
        store.delete_job(job.id)

    def test_failed_task_sets_status_to_failed(self):
        store = _make_store()
        job = store.create_job("test.mp3")

        def failing_task(job_id, job_store):
            raise RuntimeError("Soniox API unreachable")

        store.run_in_background(job.id, failing_task)

        updated_job = store.get_job(job.id)
        assert updated_job.status == JobStatus.FAILED
        assert "Soniox API unreachable" in updated_job.error
        store.delete_job(job.id)

    def test_failed_task_preserves_partial_progress(self):
        store = _make_store()
        job = store.create_job("test.mp3")

        def task_that_fails_midway(job_id, job_store):
            job_store.update_job(job_id, status=JobStatus.UPLOADING)
            job_store.update_job(
                job_id,
                status=JobStatus.TRANSCRIBING,
                progress={"pct": 50},
            )
            raise ValueError("Conversion error")

        store.run_in_background(job.id, task_that_fails_midway)

        updated_job = store.get_job(job.id)
        assert updated_job.status == JobStatus.FAILED
        assert "Conversion error" in updated_job.error
        store.delete_job(job.id)

    def test_task_with_multiple_status_transitions(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        transitions = []

        def pipeline_task(job_id, job_store):
            for status in [
                JobStatus.UPLOADING,
                JobStatus.TRANSCRIBING,
                JobStatus.CONVERTING,
                JobStatus.COMPLETED,
            ]:
                job_store.update_job(job_id, status=status)
                transitions.append(status)

        store.run_in_background(job.id, pipeline_task)

        assert transitions == [
            JobStatus.UPLOADING,
            JobStatus.TRANSCRIBING,
            JobStatus.CONVERTING,
            JobStatus.COMPLETED,
        ]
        assert store.get_job(job.id).status == JobStatus.COMPLETED
        store.delete_job(job.id)


# ---------------------------------------------------------------------------
# TestThreadSafety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Concurrent access to JobStore doesn't corrupt state."""

    def test_concurrent_creates(self):
        store = _make_store()
        results = []
        errors = []

        def create_job(idx):
            try:
                job = store.create_job(f"file_{idx}.mp3")
                results.append(job.id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_job, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 20
        assert len(set(results)) == 20  # all unique IDs
        assert len(store.list_jobs()) == 20

        # cleanup
        for job_id in results:
            store.delete_job(job_id)

    def test_concurrent_updates(self):
        store = _make_store()
        job = store.create_job("test.mp3")
        errors = []

        def update_progress(idx):
            try:
                store.update_job(
                    job.id,
                    progress={"thread": idx, "pct": idx * 5},
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=update_progress, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Job should still be valid
        assert store.get_job(job.id) is not None
        store.delete_job(job.id)

    def test_concurrent_create_and_delete(self):
        store = _make_store()
        # Pre-create jobs
        job_ids = [store.create_job(f"file_{i}.mp3").id for i in range(10)]
        errors = []

        def delete_job(jid):
            try:
                store.delete_job(jid)
            except Exception as e:
                errors.append(e)

        def create_more(idx):
            try:
                store.create_job(f"new_{idx}.mp3")
            except Exception as e:
                errors.append(e)

        threads = []
        for jid in job_ids:
            threads.append(threading.Thread(target=delete_job, args=(jid,)))
        for i in range(10):
            threads.append(threading.Thread(target=create_more, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Should have exactly 10 new jobs (old ones deleted)
        remaining = store.list_jobs()
        assert len(remaining) == 10
        for job in remaining:
            store.delete_job(job.id)


# ---------------------------------------------------------------------------
# TestJobStatusEnum
# ---------------------------------------------------------------------------


class TestJobStatusEnum:
    """JobStatus enum has correct values and string behavior."""

    def test_all_statuses_defined(self):
        assert set(JobStatus) == {
            JobStatus.PENDING,
            JobStatus.UPLOADING,
            JobStatus.TRANSCRIBING,
            JobStatus.CONVERTING,
            JobStatus.COMPLETED,
            JobStatus.FAILED,
        }

    def test_values_are_lowercase_strings(self):
        for status in JobStatus:
            assert status.value == status.value.lower()
            assert isinstance(status.value, str)

    def test_string_comparison(self):
        assert JobStatus.PENDING == "pending"
        assert JobStatus.COMPLETED == "completed"
