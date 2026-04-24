from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from functools import wraps
from typing import Any, Callable

from flask import request

from server.auth import get_request_identity
from server.errors import ApiError


_WINDOWS: dict[str, deque[float]] = defaultdict(deque)
_LOCK = threading.Lock()


def rate_limit(limit: int, window_seconds: int) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            identity = get_request_identity()
            bucket_key = f"{request.path}:{identity}"
            now = time.time()
            window_start = now - window_seconds

            with _LOCK:
                hits = _WINDOWS[bucket_key]
                while hits and hits[0] < window_start:
                    hits.popleft()
                if len(hits) >= limit:
                    retry_after = max(1, int(hits[0] + window_seconds - now))
                    raise ApiError(
                        code="rate_limited",
                        message="Too many requests. Please retry later.",
                        status_code=429,
                        details={"retry_after": retry_after, "limit": limit, "window_seconds": window_seconds},
                    )
                hits.append(now)

            return func(*args, **kwargs)

        return wrapper

    return decorator
