from __future__ import annotations

import ast
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import astunparse
import networkx as nx


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "htmlcov",
    "node_modules",
    "site-packages",
    "venv",
}


@dataclass
class NodeRecord:
    id: str
    name: str
    file: str
    line: int
    source_code: str
    type: str
    args: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)


class CallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: set[str] = set()

    def visit_Call(self, node: ast.Call) -> Any:
        callee_name = self._extract_name(node.func)
        if callee_name:
            self.calls.add(callee_name)
        self.generic_visit(node)

    def _extract_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None


def get_node_id(file_path: str, name: str) -> str:
    raw_value = f"{file_path}::{name}"
    return hashlib.md5(raw_value.encode("utf-8")).hexdigest()


def _iter_python_files(root_path: str) -> Iterable[Path]:
    root = Path(root_path)
    if not root.exists():
        LOGGER.warning("Root path does not exist: %s", root_path)
        return []
    return (
        file_path
        for file_path in root.rglob("*.py")
        if not any(part in SKIP_DIRECTORIES for part in file_path.parts)
    )


def _extract_args(function_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    arguments: list[str] = [arg.arg for arg in function_node.args.args]
    arguments.extend(arg.arg for arg in function_node.args.kwonlyargs)
    if function_node.args.vararg:
        arguments.append(function_node.args.vararg.arg)
    if function_node.args.kwarg:
        arguments.append(function_node.args.kwarg.arg)
    return arguments


def _safe_unparse(node: ast.AST) -> str:
    try:
        return astunparse.unparse(node).strip()
    except Exception as exc:
        LOGGER.warning("Failed to unparse node: %s", exc)
        return ""


def _build_function_record(
    file_path: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str | None = None,
) -> NodeRecord:
    function_name = name or node.name
    return NodeRecord(
        id=get_node_id(file_path, function_name),
        name=function_name,
        file=file_path,
        line=getattr(node, "lineno", 0),
        source_code=_safe_unparse(node),
        type="function",
        args=_extract_args(node),
    )


def _build_class_record(file_path: str, node: ast.ClassDef) -> NodeRecord:
    methods = [
        child.name
        for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return NodeRecord(
        id=get_node_id(file_path, node.name),
        name=node.name,
        file=file_path,
        line=getattr(node, "lineno", 0),
        source_code=_safe_unparse(node),
        type="class",
        args=[],
        methods=methods,
    )


def _parse_file(file_path: Path) -> ast.AST | None:
    encodings = ("utf-8", "utf-8-sig", "latin-1")
    try:
        source = ""
        for encoding in encodings:
            try:
                source = file_path.read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            source = file_path.read_text(encoding="utf-8", errors="ignore")
        return ast.parse(source, filename=str(file_path))
    except Exception as exc:
        LOGGER.warning("Skipping unparseable file %s: %s", file_path, exc)
        return None


def _collect_nodes(file_path: str, tree: ast.AST) -> tuple[list[NodeRecord], dict[str, set[str]]]:
    records: list[NodeRecord] = []
    file_calls: dict[str, set[str]] = {}

    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            record = _build_function_record(file_path, node)
            collector = CallCollector()
            collector.visit(node)
            records.append(record)
            file_calls[record.id] = collector.calls
        elif isinstance(node, ast.ClassDef):
            class_record = _build_class_record(file_path, node)
            records.append(class_record)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_name = f"{node.name}.{child.name}"
                    method_record = _build_function_record(file_path, child, name=method_name)
                    records.append(method_record)
                    collector = CallCollector()
                    collector.visit(child)
                    file_calls[method_record.id] = collector.calls

    return records, file_calls


def _collect_import_edges(
    tree: ast.AST,
    file_path: str,
    node_records: list[NodeRecord],
    name_to_ids: dict[str, list[str]],
) -> list[tuple[str, str]]:
    module_node_ids = [record.id for record in node_records if record.file == file_path]
    edges: set[tuple[str, str]] = set()

    for statement in getattr(tree, "body", []):
        imported_names: list[str] = []
        if isinstance(statement, ast.Import):
            imported_names.extend(alias.asname or alias.name.split(".")[-1] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            imported_names.extend(alias.asname or alias.name for alias in statement.names)

        if not imported_names:
            continue

        for imported_name in imported_names:
            for source_id in module_node_ids:
                for target_id in name_to_ids.get(imported_name, []):
                    if source_id != target_id:
                        edges.add((source_id, target_id))

    return list(edges)


def _record_to_node_dict(record: NodeRecord) -> dict[str, Any]:
    node_dict: dict[str, Any] = {
        "id": record.id,
        "name": record.name,
        "file": record.file,
        "line": record.line,
        "source_code": record.source_code,
        "type": record.type,
        "args": record.args,
    }
    if record.type == "class":
        node_dict["methods"] = record.methods
    return node_dict


def extract(root_path: str) -> dict[str, list[dict[str, Any]]]:
    graph = nx.DiGraph()
    all_records: list[NodeRecord] = []
    calls_by_node: dict[str, set[str]] = {}
    file_trees: dict[str, ast.AST] = {}

    for file_path in _iter_python_files(root_path):
        tree = _parse_file(file_path)
        if tree is None:
            continue

        file_path_str = str(file_path.resolve())
        file_trees[file_path_str] = tree
        records, file_calls = _collect_nodes(file_path_str, tree)
        all_records.extend(records)
        calls_by_node.update(file_calls)

    name_to_ids: dict[str, list[str]] = {}
    for record in all_records:
        graph.add_node(record.id, **_record_to_node_dict(record))
        name_to_ids.setdefault(record.name, []).append(record.id)
        if "." in record.name:
            name_to_ids.setdefault(record.name.split(".")[-1], []).append(record.id)

    for caller_id, called_names in calls_by_node.items():
        for called_name in called_names:
            for callee_id in name_to_ids.get(called_name, []):
                if caller_id != callee_id:
                    graph.add_edge(caller_id, callee_id)

    for file_path, tree in file_trees.items():
        import_edges = _collect_import_edges(tree, file_path, all_records, name_to_ids)
        for source_id, target_id in import_edges:
            graph.add_edge(source_id, target_id)

    return {
        "nodes": [dict(data) for _, data in graph.nodes(data=True)],
        "edges": [
            {"source": source, "target": target}
            for source, target in graph.edges()
        ],
    }


if __name__ == "__main__":
    current_directory = str(Path.cwd())
    extracted_graph = extract(current_directory)
    print(
        f"Scanned {current_directory} -> "
        f"{len(extracted_graph['nodes'])} nodes, {len(extracted_graph['edges'])} edges"
    )
