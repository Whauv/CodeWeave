from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class AppState:
    _request_cache: ContextVar[dict[str, Any] | None] = ContextVar("request_cache", default=None)

    def _ensure_cache(self) -> dict[str, Any]:
        cache = self._request_cache.get()
        if cache is None:
            cache = {}
            self._request_cache.set(cache)
        return cache

    @property
    def graph_cache(self) -> dict[str, Any] | None:
        return self._ensure_cache().get("graph_cache")

    @graph_cache.setter
    def graph_cache(self, value: dict[str, Any] | None) -> None:
        self._ensure_cache()["graph_cache"] = value

    @property
    def scan_context(self) -> dict[str, Any] | None:
        return self._ensure_cache().get("scan_context")

    @scan_context.setter
    def scan_context(self, value: dict[str, Any] | None) -> None:
        self._ensure_cache()["scan_context"] = value

    @property
    def history_graph_cache(self) -> dict[str, dict[str, Any]]:
        cache = self._ensure_cache()
        if "history_graph_cache" not in cache:
            cache["history_graph_cache"] = {}
        return cache["history_graph_cache"]

    def reset(self) -> None:
        self._request_cache.set({})


STATE = AppState()
