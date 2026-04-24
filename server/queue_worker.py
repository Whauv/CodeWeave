from __future__ import annotations

import os
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class QueueWorker:
    def __init__(self) -> None:
        max_workers = int(os.getenv("CODEWEAVE_JOB_WORKERS", "2"))
        self._executor = ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="codeweave-job")

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any]:
        return self._executor.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)
