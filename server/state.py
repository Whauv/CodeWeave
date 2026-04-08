from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppState:
    graph_cache: dict[str, Any] | None = None
    scan_context: dict[str, Any] | None = None
    history_graph_cache: dict[str, dict[str, Any]] = field(default_factory=dict)


STATE = AppState()
