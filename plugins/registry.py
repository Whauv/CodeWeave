from __future__ import annotations

from typing import Any

from plugins.base import BaseLanguagePlugin
from plugins.go import GoLanguagePlugin
from plugins.java import JavaLanguagePlugin
from plugins.python import PythonLanguagePlugin
from plugins.typescript import TypeScriptLanguagePlugin


def _build_registry() -> dict[str, BaseLanguagePlugin]:
    plugins: list[BaseLanguagePlugin] = [
        PythonLanguagePlugin(),
        TypeScriptLanguagePlugin(),
        GoLanguagePlugin(),
        JavaLanguagePlugin(),
    ]
    return {plugin.language: plugin for plugin in plugins}


REGISTRY = _build_registry()


def get_plugin(language: str) -> BaseLanguagePlugin:
    normalized = str(language or "").strip().lower() or "python"
    if normalized not in REGISTRY:
        raise ValueError(f"Unsupported language: {normalized}")
    return REGISTRY[normalized]


def get_language_options() -> list[dict[str, Any]]:
    return [
        {
            "language": plugin.describe().language,
            "label": plugin.describe().label,
            "extensions": list(plugin.describe().extensions),
            "supports_mutation_tracking": plugin.describe().supports_mutation_tracking,
            "ready": plugin.describe().ready,
        }
        for plugin in REGISTRY.values()
    ]


if __name__ == "__main__":
    print(get_language_options())
