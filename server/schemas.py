from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from server.errors import ApiError


COMMIT_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")


@dataclass(slots=True)
class ScanRequest:
    path: str
    language: str


@dataclass(slots=True)
class ChatRequest:
    message: str
    node_id: str | None
    provider: str
    history: list[Any]


def validate_commit_hash(value: str, field_name: str = "commit_hash") -> str:
    normalized = str(value or "").strip()
    if not COMMIT_HASH_PATTERN.fullmatch(normalized):
        raise ApiError(
            code="invalid_commit_hash",
            message=f"Invalid {field_name}. Use a 7-64 character hexadecimal commit hash.",
            status_code=400,
        )
    return normalized


def parse_scan_request(payload: Any) -> ScanRequest:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ApiError(
            code="invalid_request_body",
            message="Request body must be a JSON object.",
            status_code=400,
        )

    project_input = str(payload.get("path") or "").strip()
    language = str(payload.get("language") or "python").strip().lower()

    if not project_input:
        raise ApiError(
            code="invalid_scan_path",
            message="Invalid project path",
            status_code=400,
        )

    if not language:
        raise ApiError(
            code="invalid_language",
            message="Language is required.",
            status_code=400,
        )

    return ScanRequest(path=project_input, language=language)


def parse_chat_request(payload: Any, default_provider: str) -> ChatRequest:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ApiError(
            code="invalid_request_body",
            message="Request body must be a JSON object.",
            status_code=400,
        )

    message = str(payload.get("message") or "").strip()
    if not message:
        raise ApiError(
            code="invalid_chat_message",
            message="Message is required",
            status_code=400,
        )

    node_id = str(payload.get("node_id") or "").strip() or None
    provider = str(payload.get("provider") or default_provider).strip().lower()
    history_value = payload.get("history")
    history = history_value if isinstance(history_value, list) else []

    return ChatRequest(
        message=message,
        node_id=node_id,
        provider=provider,
        history=history,
    )
