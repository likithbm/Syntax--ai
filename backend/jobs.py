"""In-memory job store so the UI can poll real progress while the (slow) vision request runs."""

from __future__ import annotations

import logging
import threading
import time
import uuid

from .errors import SyntaxAIError

log = logging.getLogger("syntaxai")


class JobStore:
    def __init__(self, ttl_seconds: float = 3600):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def create(self) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._prune_locked()
            self._jobs[job_id] = {"id": job_id, "status": "running", "stage": "queued",
                                  "message": "Queued…", "started": time.monotonic(), "finished": None,
                                  "result": None, "error": None}
        return job_id

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            end = job["finished"] or time.monotonic()
            return {**job, "elapsed": round(end - job["started"], 1)}

    def _prune_locked(self) -> None:
        now = time.monotonic()
        stale = [k for k, j in self._jobs.items() if j["finished"] and now - j["finished"] > self._ttl]
        for k in stale:
            del self._jobs[k]


def run_job(store: JobStore, job_id: str, func) -> None:
    """Run func(progress) and record the outcome. Never raises; never stores stack traces."""

    def progress(stage: str, message: str) -> None:
        store.update(job_id, stage=stage, message=message)

    try:
        result = func(progress)
        store.update(job_id, status="done", stage="done", message="Done", result=result,
                     finished=time.monotonic())
    except SyntaxAIError as exc:
        store.update(job_id, status="error", error={"code": exc.code, "message": exc.message},
                     finished=time.monotonic())
    except Exception:
        log.exception("job %s failed unexpectedly", job_id)
        store.update(job_id, status="error", finished=time.monotonic(),
                     error={"code": "internal_error",
                            "message": "Something went wrong while processing the diagram. Please try again."})
