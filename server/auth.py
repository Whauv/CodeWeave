from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from functools import wraps
from typing import Any, Callable

from flask import Response, g, request

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


def _security_secret() -> str:
    secret = os.getenv("CODEWEAVE_SECURITY_SECRET", "").strip()
    if secret:
        return secret
    if _session_enabled() or _csrf_enabled():
        raise ApiError(
            "security_secret_required",
            "CODEWEAVE_SECURITY_SECRET must be set when session or CSRF protection is enabled.",
            500,
        )
    tokens = sorted(_allowed_tokens())
    if tokens:
        return hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()
    return "codeweave-dev-secret"


def _csrf_enabled() -> bool:
    return os.getenv("CODEWEAVE_CSRF_PROTECTION", "off").strip().lower() in {"1", "true", "yes", "on"}


def _session_enabled() -> bool:
    return os.getenv("CODEWEAVE_SESSION_PROTECTION", "off").strip().lower() in {"1", "true", "yes", "on"}


def _cookie_secure() -> bool:
    return os.getenv("CODEWEAVE_COOKIE_SECURE", "off").strip().lower() in {"1", "true", "yes", "on"}


def _build_signed_token(kind: str, identity: str, ttl_seconds: int) -> str:
    issued_at = int(time.time())
    nonce = secrets.token_hex(8)
    payload = f"{kind}:{identity}:{issued_at}:{ttl_seconds}:{nonce}"
    signature = hmac.new(_security_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{issued_at}.{ttl_seconds}.{nonce}.{signature}"


def _verify_signed_token(kind: str, identity: str, token: str) -> bool:
    parts = str(token or "").split(".")
    if len(parts) != 4:
        return False
    issued_at_raw, ttl_raw, nonce, signature = parts
    if not issued_at_raw.isdigit() or not ttl_raw.isdigit() or not nonce:
        return False
    issued_at = int(issued_at_raw)
    ttl_seconds = int(ttl_raw)
    if ttl_seconds <= 0:
        return False
    if int(time.time()) > issued_at + ttl_seconds:
        return False
    payload = f"{kind}:{identity}:{issued_at}:{ttl_seconds}:{nonce}"
    expected = hmac.new(_security_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _trust_client_identity_header() -> bool:
    return os.getenv("CODEWEAVE_TRUST_CLIENT_IDENTITY", "off").strip().lower() in {"1", "true", "yes", "on"}


def _identity_from_request() -> str:
    explicit = str(request.headers.get("X-Codeweave-User") or "").strip()
    if explicit and _trust_client_identity_header():
        return explicit[:200]
    ip = str(request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",")[0].strip()
    return f"ip:{ip or 'unknown'}"


def resolve_untrusted_identity() -> str:
    return _identity_from_request()


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
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            if _session_enabled():
                session_cookie = str(request.cookies.get("codeweave_session") or "")
                session_header = str(request.headers.get("X-Codeweave-Session") or "")
                if not session_cookie or not session_header or session_cookie != session_header:
                    raise ApiError(
                        "session_protection_failed",
                        "Missing or invalid session protection header.",
                        403,
                    )
                if not _verify_signed_token("session", identity, session_cookie):
                    raise ApiError("session_invalid", "Session token is invalid or expired.", 403)
            if _csrf_enabled():
                csrf_cookie = str(request.cookies.get("codeweave_csrf") or "")
                csrf_header = str(request.headers.get("X-Codeweave-CSRF") or "")
                if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
                    raise ApiError("csrf_failed", "Missing or invalid CSRF token.", 403)
                if not _verify_signed_token("csrf", identity, csrf_cookie):
                    raise ApiError("csrf_invalid", "CSRF token is invalid or expired.", 403)
        return func(*args, **kwargs)

    return wrapper


def issue_security_cookies(response: Response, identity: str) -> Response:
    if _session_enabled() and not request.cookies.get("codeweave_session"):
        session_token = _build_signed_token("session", identity, ttl_seconds=24 * 60 * 60)
        response.set_cookie(
            "codeweave_session",
            session_token,
            max_age=24 * 60 * 60,
            secure=_cookie_secure(),
            httponly=True,
            samesite="Strict",
            path="/",
        )
    if _csrf_enabled() and not request.cookies.get("codeweave_csrf"):
        csrf_token = _build_signed_token("csrf", identity, ttl_seconds=2 * 60 * 60)
        response.set_cookie(
            "codeweave_csrf",
            csrf_token,
            max_age=2 * 60 * 60,
            secure=_cookie_secure(),
            httponly=False,
            samesite="Strict",
            path="/",
        )
    return response


def build_security_tokens(identity: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    if _session_enabled():
        payload["session"] = _build_signed_token("session", identity, ttl_seconds=24 * 60 * 60)
    if _csrf_enabled():
        payload["csrf"] = _build_signed_token("csrf", identity, ttl_seconds=2 * 60 * 60)
    return payload
