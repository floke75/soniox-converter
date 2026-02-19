"""In-memory job store with background task execution and TTL cleanup.

WHY: The HTTP API needs to track transcription jobs through their lifecycle
(pending → uploading → transcribing → converting → completed | failed).
Jobs are long-running (30s–20min), so the API returns a job ID immediately
and processes work in the background. An in-memory store is sufficient for
a single-team tool with no persistence requirements.

HOW: Three components work together:
  JobStatus  — enum of valid job states
  Job        — dataclass holding job metadata, status, and temp directory
  JobStore   — thread-safe dict-based store with create/update/get/list/delete,
               background task execution via BackgroundTasks, and TTL cleanup

RULES:
- All store mutations are protected by threading.Lock for thread safety
- Each job gets a dedicated temp directory for output files
- TTL-based expiry removes stale jobs and cleans up their temp directories
- Background runner updates job status to 'failed' on unhandled exceptions
- Job IDs are UUID4 strings generated at creation time
- Default TTL is 1 hour (3600 seconds)
"""

from __future__ import annotations

import enum
import logging
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default time-to-live for completed/failed jobs (seconds)
DEFAULT_TTL_SECONDS = 3600


class JobStatus(str, enum.Enum):
    """Valid states for a transcription job.

    WHY: Jobs progress through a linear pipeline with two terminal states.
    Using an enum prevents invalid status strings and makes transitions
    explicit.

    HOW: Inherits from str so values serialize cleanly to JSON.

    RULES:
    - pending: job created, not yet started
    - uploading: file being uploaded to Soniox
    - transcribing: Soniox processing the audio
    - converting: transcript assembled, formatters running
    - completed: all output files ready for download
    - failed: unrecoverable error at any stage
    """

    PENDING = "pending"
    UPLOADING = "uploading"
    TRANSCRIBING = "transcribing"
    CONVERTING = "converting"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    """Metadata and state for a single transcription job.

    WHY: The API needs to track each job's status, configuration, output
    files, timing, and errors. A dataclass provides typed fields with
    sensible defaults.

    HOW: Created by JobStore.create_job() with a UUID and temp directory.
    Updated by the background runner as the job progresses through states.

    RULES:
    - id: UUID4 string, unique and immutable after creation
    - status: current JobStatus (starts as PENDING)
    - filename: original uploaded filename (for display/download)
    - output_dir: Path to temp directory for output files
    - created_at: epoch timestamp when job was created
    - updated_at: epoch timestamp of last status change
    - completed_at: epoch timestamp when job reached terminal state, or None
    - error: error message string if status is FAILED, else None
    - progress: optional progress info dict (e.g. {"stage": "transcribing", "pct": 45})
    - config: job configuration dict (formats, languages, etc.)
    - output_files: list of output filenames available for download
    """

    id: str
    status: JobStatus
    filename: str
    output_dir: Path
    created_at: float
    updated_at: float
    completed_at: Optional[float] = None
    error: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None
    config: Dict[str, Any] = field(default_factory=dict)
    output_files: List[str] = field(default_factory=list)


class JobStore:
    """Thread-safe in-memory store for transcription jobs.

    WHY: Multiple concurrent API requests and background tasks access job
    state simultaneously. A centralized store with locking prevents race
    conditions and provides a clean interface for CRUD operations.

    HOW: Jobs are stored in a plain dict keyed by job ID. All mutations
    acquire a threading.Lock. Background tasks run via callables that
    receive the job ID and update the store as they progress. TTL cleanup
    iterates all jobs and removes expired ones.

    RULES:
    - All public methods that mutate state acquire self._lock
    - create_job() generates a UUID, creates a temp dir, and stores the job
    - get_job() returns None for missing job IDs (no exceptions)
    - update_job() sets status, error, progress, and/or output_files
    - delete_job() removes the job and cleans up its temp directory
    - cleanup_expired() removes jobs past their TTL and their temp dirs
    """

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_jobs: int = 100,
    ) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._ttl_seconds = ttl_seconds
        self.max_jobs = max_jobs

    def create_job(
        self,
        filename: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> Job:
        """Create a new job in PENDING state with a dedicated temp directory.

        WHY: Every transcription request needs a tracked job with a unique ID
        and a place to store output files.

        HOW: Generates a UUID4, creates a temp directory, builds a Job
        dataclass, and stores it under the lock.

        RULES:
        - Returns the newly created Job
        - The temp directory is created immediately and persists until
          the job is deleted or expires
        """
        with self._lock:
            if len(self._jobs) >= self.max_jobs:
                raise ValueError(
                    "Maximum number of concurrent jobs ({}) reached".format(
                        self.max_jobs
                    )
                )

            job_id = uuid.uuid4().hex
            now = time.time()
            output_dir = Path(tempfile.mkdtemp(prefix="soniox_job_"))

            job = Job(
                id=job_id,
                status=JobStatus.PENDING,
                filename=filename,
                output_dir=output_dir,
                created_at=now,
                updated_at=now,
                config=config or {},
            )

            self._jobs[job_id] = job

        logger.info("Created job %s for file %s", job_id, filename)
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        """Retrieve a job by ID, or None if not found.

        WHY: The polling endpoint and background runner both need to look up
        jobs by ID. Returning None (instead of raising) lets callers decide
        how to handle missing jobs.

        HOW: Direct dict lookup under the lock.

        RULES:
        - Returns None for unknown job IDs
        - The returned Job object is the live instance (not a copy)
        """
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> List[Job]:
        """Return all jobs as a list, ordered by creation time (oldest first).

        WHY: The API may expose a job listing endpoint for monitoring.

        HOW: Snapshot of all jobs sorted by created_at under the lock.

        RULES:
        - Returns a new list (not a reference to internal state)
        - Jobs are sorted oldest-first by created_at
        """
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at)

    def update_job(
        self,
        job_id: str,
        status: Optional[JobStatus] = None,
        error: Optional[str] = None,
        progress: Optional[Dict[str, Any]] = None,
        output_files: Optional[List[str]] = None,
    ) -> Optional[Job]:
        """Update a job's mutable fields.

        WHY: Background tasks need to update status, progress, errors, and
        output file lists as the job progresses.

        HOW: Acquires the lock, applies non-None updates, bumps updated_at.
        Sets completed_at when the job reaches a terminal state.

        RULES:
        - Returns the updated Job, or None if job_id not found
        - Only non-None arguments are applied
        - updated_at is always bumped on any change
        - completed_at is set when status becomes COMPLETED or FAILED
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None

            now = time.time()

            if status is not None:
                job.status = status
            if error is not None:
                job.error = error
            if progress is not None:
                job.progress = progress
            if output_files is not None:
                job.output_files = output_files

            job.updated_at = now

            if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                job.completed_at = now

            return job

    def delete_job(self, job_id: str) -> bool:
        """Delete a job and clean up its temp directory.

        WHY: Clients may cancel jobs or clean up after downloading results.
        Temp directories must be removed to avoid filling disk.

        HOW: Removes the job from the dict under the lock, then cleans up
        the temp directory outside the lock (I/O should not hold the lock).

        RULES:
        - Returns True if the job was found and deleted, False otherwise
        - Temp directory removal is best-effort (logged but not raised)
        """
        with self._lock:
            job = self._jobs.pop(job_id, None)

        if job is None:
            return False

        self._cleanup_output_dir(job.output_dir)
        logger.info("Deleted job %s", job_id)
        return True

    def cleanup_expired(self) -> int:
        """Remove all jobs that have exceeded their TTL.

        WHY: Completed and failed jobs accumulate temp files on disk.
        Periodic cleanup prevents disk exhaustion.

        HOW: Iterates all jobs under the lock. Jobs in a terminal state
        (COMPLETED or FAILED) whose completed_at is older than TTL are
        removed. Their temp directories are cleaned up outside the lock.

        RULES:
        - Only terminal-state jobs (COMPLETED, FAILED) are candidates
        - TTL is measured from completed_at, not created_at
        - Returns the count of removed jobs
        - Temp directory cleanup is best-effort
        """
        now = time.time()
        expired_jobs: List[Job] = []

        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
                    continue
                if job.completed_at is None:
                    continue
                if now - job.completed_at > self._ttl_seconds:
                    expired_jobs.append(self._jobs.pop(job_id))

        for job in expired_jobs:
            self._cleanup_output_dir(job.output_dir)
            logger.info("Expired job %s (completed %.0fs ago)", job.id, now - job.completed_at)

        return len(expired_jobs)

    @staticmethod
    def _cleanup_output_dir(output_dir: Path) -> None:
        """Remove a job's temp directory tree.

        WHY: Each job creates a temp directory for output files. When the
        job is deleted or expires, the directory must be cleaned up.

        HOW: shutil.rmtree with ignore_errors=True for best-effort cleanup.

        RULES:
        - Never raises — logs warnings on failure
        - Skips if directory doesn't exist
        """
        if output_dir.exists():
            try:
                shutil.rmtree(output_dir)
            except OSError:
                logger.warning("Failed to clean up temp dir: %s", output_dir)
