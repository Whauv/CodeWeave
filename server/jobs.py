from __future__ import annotations

import traceback
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from server import db
from server.queue_worker import QueueWorker


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

    def submit(self, *, identity: str, job_type: str, payload: dict[str, Any], handler: JobHandler) -> str:
        job_id = str(uuid.uuid4())
        db.save_job(job_id=job_id, identity=identity, job_type=job_type, status="queued", payload=payload)

        def run() -> dict[str, Any]:
            db.save_job(job_id=job_id, identity=identity, job_type=job_type, status="running", payload=payload)
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
                return result
            except Exception as exc:
                message = str(exc) or "Job failed"
                db.save_job(
                    job_id=job_id,
                    identity=identity,
                    job_type=job_type,
                    status="failed",
                    payload=payload,
                    error_message=f"{message}\n{traceback.format_exc(limit=5)}",
                )
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
