from __future__ import annotations

import hashlib
import os
from functools import wraps
from typing import Any, Callable

from flask import g, request

from server.errors import ApiError
from server.logging_config import USER_IDENTITY


def _auth_mode() -> str:
    return os.getenv("CODEWEAVE_AUTH_MODE", "off").strip().lower()


def _allowed_tokens() -> set[str]:
    raw = os.getenv("CODEWEAVE_API_TOKENS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _extract_bearer_token() -> str | None:
    auth_header = str(request.headers.get("Authorization") or "").strip()
    if not auth_header:
        return None
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[7:].strip()
    return token or None


def _identity_from_request() -> str:
    explicit = str(request.headers.get("X-Codeweave-User") or "").strip()
    if explicit:
        return explicit[:200]
    ip = str(request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",")[0].strip()
    return f"ip:{ip or 'unknown'}"


def resolve_request_identity() -> str:
    mode = _auth_mode()
    if mode in {"off", "disabled", "none"}:
        return _identity_from_request()

    token = _extract_bearer_token()
    if not token:
        raise ApiError("auth_required", "Authorization bearer token is required.", 401)

    allowed = _allowed_tokens()
    if token not in allowed:
        raise ApiError("auth_invalid_token", "Invalid API token.", 401)

    token_fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    return f"token:{token_fingerprint}"


def set_request_identity(identity: str) -> None:
    g.codeweave_identity = identity
    USER_IDENTITY.set(identity)


def get_request_identity() -> str:
    return str(getattr(g, "codeweave_identity", "") or "anonymous")


def require_auth(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        identity = resolve_request_identity()
        set_request_identity(identity)
        return func(*args, **kwargs)

    return wrapper
