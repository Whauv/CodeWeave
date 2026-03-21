from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Iterable

from groq import Groq


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parents[1] / "summaries_cache.json"
MODEL_NAME = "llama-3.1-8b-instant"
MAX_BATCH_SIZE = 4
MAX_SOURCE_CHARS = 1600
MAX_BATCH_TOKENS = 220


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


def _get_client() -> Groq | None:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return Groq(api_key=api_key)


def _trim_source(source_code: str) -> str:
    normalized = source_code.strip()
    if len(normalized) <= MAX_SOURCE_CHARS:
        return normalized
    return f"{normalized[:MAX_SOURCE_CHARS]}\n# ...truncated..."


def _chunk_nodes(nodes: list[dict[str, str]], chunk_size: int) -> Iterable[list[dict[str, str]]]:
    for index in range(0, len(nodes), chunk_size):
        yield nodes[index:index + chunk_size]


def _is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "429" in message or "rate limit" in message or "too many requests" in message


def _is_json_generation_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "json_validate_failed" in message or "failed to generate json" in message or "max completion tokens reached" in message


def _store_no_summary(cache: dict[str, str], nodes: list[dict[str, str]]) -> None:
    for node in nodes:
        cache.setdefault(node["id"], "No summary available.")


def _summarize_batch(client: Groq, cache: dict[str, str], batch: list[dict[str, str]]) -> bool:
    prompt_lines = [
        "Summarize each Python function below in one concise sentence of at most 20 words.",
        "Return valid JSON only as an object mapping each id to its summary.",
    ]
    for node in batch:
        prompt_lines.append(f"ID: {node['id']}")
        prompt_lines.append(_trim_source(node.get("source_code", "")))
        prompt_lines.append("")
    prompt = "\n".join(prompt_lines)

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=MAX_BATCH_TOKENS,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content.strip() if response.choices else "{}"
        parsed = json.loads(content or "{}")
        for node in batch:
            node_id = node["id"]
            summary = parsed.get(node_id) or "No summary available."
            cache[node_id] = str(summary).strip() or "No summary available."
        return True
    except Exception as exc:
        failed_ids = ", ".join(node["id"] for node in batch)
        LOGGER.error("Groq batch summarization failed for [%s]: %s", failed_ids, exc)

        if _is_json_generation_error(exc) and len(batch) > 1:
            midpoint = max(1, len(batch) // 2)
            left = batch[:midpoint]
            right = batch[midpoint:]
            left_ok = _summarize_batch(client, cache, left)
            right_ok = _summarize_batch(client, cache, right)
            return left_ok and right_ok

        _store_no_summary(cache, batch)
        if _is_rate_limit_error(exc):
            LOGGER.warning("Groq rate limit hit; disabling remote summaries for the rest of this scan.")
            return False
        if _is_json_generation_error(exc):
            LOGGER.warning("Groq JSON generation failed for a batch; falling back to cached/default summaries.")
        return True


def summarize_nodes(nodes: list[dict[str, str]]) -> dict[str, str]:
    cache = _load_cache()
    pending_nodes = [node for node in nodes if node.get("id") and node["id"] not in cache]
    if not pending_nodes:
        return {node["id"]: cache.get(node["id"], "No summary available.") for node in nodes if node.get("id")}

    client = _get_client()
    if client is None:
        return {node["id"]: cache.get(node["id"], "No summary available.") for node in nodes if node.get("id")}

    disable_remote_summaries = False
    for batch in _chunk_nodes(pending_nodes, MAX_BATCH_SIZE):
        if disable_remote_summaries:
            _store_no_summary(cache, batch)
            continue
        should_continue = _summarize_batch(client, cache, batch)
        if not should_continue:
            disable_remote_summaries = True

    _save_cache(cache)
    return {node["id"]: cache.get(node["id"], "No summary available.") for node in nodes if node.get("id")}


def summarize_node(source_code: str, node_id: str) -> str:
    cache = _load_cache()
    if node_id in cache:
        return cache[node_id]

    client = _get_client()
    if client is None:
        return "No summary available."

    prompt = (
        "Summarize this Python function in one concise sentence (max 20 words):\n"
        f"{source_code}"
    )

    try:
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
