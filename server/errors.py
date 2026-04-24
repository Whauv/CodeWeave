from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(slots=True)
class ApiError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] | None = None


def error_response(
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> tuple[Any, int] | tuple[Any, int, dict[str, str]]:
    payload: dict[str, Any] = {
        "error": message,
        "error_code": code,
    }
    if details:
        payload["details"] = details
    if status_code == 429:
        retry_after = str(int(details.get("retry_after", 1))) if isinstance(details, dict) else "1"
        return jsonify(payload), status_code, {"Retry-After": retry_after}
    return jsonify(payload), status_code


def from_api_error(exc: ApiError) -> tuple[Any, int]:
    return error_response(exc.code, exc.message, exc.status_code, details=exc.details)
