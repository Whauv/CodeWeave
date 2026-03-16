from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from groq import Groq


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parents[1] / "summaries_cache.json"
MODEL_NAME = "llama3-8b-8192"


def get_node_id(file_path: str, function_name: str) -> str:
    return hashlib.md5(f"{file_path}::{function_name}".encode("utf-8")).hexdigest()


def _load_cache() -> dict[str, str]:
    if not CACHE_PATH.exists():
        CACHE_PATH.write_text("{}", encoding="utf-8")
        return {}

    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning("Failed to load summary cache: %s", exc)
        return {}


def _save_cache(cache: dict[str, str]) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception as exc:
        LOGGER.warning("Failed to save summary cache: %s", exc)


def summarize_node(source_code: str, node_id: str) -> str:
    cache = _load_cache()
    if node_id in cache:
        return cache[node_id]

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "No summary available."

    prompt = (
        "Summarize this Python function in one concise sentence (max 20 words):\n"
        f"{source_code}"
    )

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60,
            temperature=0.3,
        )
        summary = response.choices[0].message.content.strip() if response.choices else ""
        if not summary:
            summary = "No summary available."
        cache[node_id] = summary
        _save_cache(cache)
        return summary
    except Exception as exc:
        LOGGER.error("Groq summarization failed for %s: %s", node_id, exc)
        return "No summary available."


if __name__ == "__main__":
    example_id = get_node_id("demo.py", "example")
    print(summarize_node("def example():\n    return 'hello'", example_id))
