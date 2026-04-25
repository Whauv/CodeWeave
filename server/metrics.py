from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Any


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._request_total: dict[tuple[str, str, str], int] = defaultdict(int)
        self._request_duration_seconds_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._request_duration_seconds_count: dict[tuple[str, str], int] = defaultdict(int)
        self._job_total: dict[tuple[str, str], int] = defaultdict(int)

    def observe_request(self, *, method: str, route: str, status_code: int, duration_seconds: float) -> None:
        status_class = f"{int(status_code) // 100}xx"
        with self._lock:
            self._request_total[(method.upper(), route, status_class)] += 1
            self._request_duration_seconds_sum[(method.upper(), route)] += max(0.0, float(duration_seconds))
            self._request_duration_seconds_count[(method.upper(), route)] += 1

    def observe_job(self, *, job_type: str, status: str) -> None:
        with self._lock:
            self._job_total[(str(job_type), str(status))] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "request_total": dict(self._request_total),
                "request_duration_seconds_sum": dict(self._request_duration_seconds_sum),
                "request_duration_seconds_count": dict(self._request_duration_seconds_count),
                "job_total": dict(self._job_total),
            }

    def to_prometheus_text(self) -> str:
        data = self.snapshot()
        lines: list[str] = []
        lines.append("# HELP codeweave_http_requests_total Total HTTP requests by method, route, and status class.")
        lines.append("# TYPE codeweave_http_requests_total counter")
        for (method, route, status), value in sorted(data["request_total"].items()):
            lines.append(
                f'codeweave_http_requests_total{{method="{method}",route="{route}",status="{status}"}} {value}'
            )

        lines.append("# HELP codeweave_http_request_duration_seconds_sum Total HTTP request duration in seconds.")
        lines.append("# TYPE codeweave_http_request_duration_seconds_sum counter")
        for (method, route), value in sorted(data["request_duration_seconds_sum"].items()):
            lines.append(
                f'codeweave_http_request_duration_seconds_sum{{method="{method}",route="{route}"}} {value:.6f}'
            )

        lines.append("# HELP codeweave_http_request_duration_seconds_count HTTP request duration sample count.")
        lines.append("# TYPE codeweave_http_request_duration_seconds_count counter")
        for (method, route), value in sorted(data["request_duration_seconds_count"].items()):
            lines.append(
                f'codeweave_http_request_duration_seconds_count{{method="{method}",route="{route}"}} {value}'
            )

        lines.append("# HELP codeweave_jobs_total Total async jobs by type and final status.")
        lines.append("# TYPE codeweave_jobs_total counter")
        for (job_type, status), value in sorted(data["job_total"].items()):
            lines.append(f'codeweave_jobs_total{{job_type="{job_type}",status="{status}"}} {value}')

        return "\n".join(lines) + "\n"


METRICS = MetricsRegistry()

