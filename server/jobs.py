from __future__ import annotations

import traceback
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
import os
from threading import Lock
import time
from typing import Any, Callable

from server import db
from server.errors import ApiError
from server.metrics import METRICS
from server.queue_worker import QueueWorker
from server.sentry_config import capture_exception


JobHandler = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass
class _RuntimeJob:
    identity: str
    job_type: str
    payload: dict[str, Any]
    future: Future[Any]


class JobManager:
    def __init__(self) -> None:
        self._worker = QueueWorker()
        self._jobs: dict[str, _RuntimeJob] = {}
        self._lock = Lock()
        self._max_retries = max(0, int(os.getenv("CODEWEAVE_JOB_MAX_RETRIES", "2")))
        self._base_backoff_seconds = max(0.1, float(os.getenv("CODEWEAVE_JOB_BACKOFF_SECONDS", "0.4")))

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, ApiError):
            return int(exc.status_code) >= 500
        return True

    def submit(self, *, identity: str, job_type: str, payload: dict[str, Any], handler: JobHandler) -> str:
        job_id = str(uuid.uuid4())
        db.save_job(job_id=job_id, identity=identity, job_type=job_type, status="queued", payload=payload)

        def run() -> dict[str, Any]:
            db.save_job(job_id=job_id, identity=identity, job_type=job_type, status="running", payload=payload)
            attempts = 0
            while True:
                attempts += 1
                try:
                    result = handler(identity, payload)
                    db.save_job(
                        job_id=job_id,
                        identity=identity,
                        job_type=job_type,
                        status="succeeded",
                        payload=payload,
                        result=result,
                    )
                    METRICS.observe_job(job_type=job_type, status="succeeded")
                    return result
                except Exception as exc:
                    retryable = self._is_retryable(exc)
                    if attempts <= self._max_retries and retryable:
                        db.save_job(
                            job_id=job_id,
                            identity=identity,
                            job_type=job_type,
                            status="running",
                            payload=payload,
                            error_message=f"Retrying after attempt {attempts}: {exc}",
                        )
                        delay = min(self._base_backoff_seconds * (2 ** (attempts - 1)), 5.0)
                        time.sleep(delay)
                        continue
                    message = str(exc) or "Job failed"
                    db.save_job(
                        job_id=job_id,
                        identity=identity,
                        job_type=job_type,
                        status="failed",
                        payload=payload,
                        error_message=f"{message}\n{traceback.format_exc(limit=5)}",
                    )
                    METRICS.observe_job(job_type=job_type, status="failed")
                    capture_exception(exc, job_id=job_id, job_type=job_type, attempts=attempts)
                    raise

        future = self._worker.submit(run)
        with self._lock:
            self._jobs[job_id] = _RuntimeJob(identity=identity, job_type=job_type, payload=payload, future=future)
        return job_id

    def get_status(self, *, identity: str, job_id: str) -> dict[str, Any] | None:
        record = db.get_job(job_id, identity)
        if not record:
            return None
        return {
            "id": record["id"],
            "job_type": record["job_type"],
            "status": record["status"],
            "error": record["error_message"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }

    def get_result(self, *, identity: str, job_id: str) -> dict[str, Any] | None:
        record = db.get_job(job_id, identity)
        if not record:
            return None
        return record


JOBS = JobManager()
