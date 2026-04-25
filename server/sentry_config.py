from __future__ import annotations

import logging
import os
from typing import Any


LOGGER = logging.getLogger(__name__)


def init_sentry() -> None:
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except Exception as exc:
        LOGGER.warning("Sentry DSN provided but sentry-sdk is unavailable: %s", exc)
        return

    environment = os.getenv("SENTRY_ENVIRONMENT", os.getenv("CODEWEAVE_ENV", "development")).strip()
    release = os.getenv("SENTRY_RELEASE", os.getenv("CODEWEAVE_RELEASE", "codeweave-dev")).strip()
    traces_sample_rate_raw = os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0").strip()
    try:
        traces_sample_rate = max(0.0, min(1.0, float(traces_sample_rate_raw)))
    except ValueError:
        traces_sample_rate = 0.0

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=traces_sample_rate,
        environment=environment,
        release=release,
        send_default_pii=False,
    )
    LOGGER.info(
        "Sentry initialized (env=%s, release=%s, traces_sample_rate=%s)",
        environment,
        release,
        traces_sample_rate,
    )


def capture_exception(error: Exception, **context: Any) -> None:
    try:
        import sentry_sdk
    except Exception:
        return
    with sentry_sdk.push_scope() as scope:
        for key, value in context.items():
            scope.set_extra(str(key), value)
        sentry_sdk.capture_exception(error)

