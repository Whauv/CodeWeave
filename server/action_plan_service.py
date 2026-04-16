from __future__ import annotations

from collections import Counter
from typing import Any

from graph.blast_radius import compute_blast_radius


def _find_node(graph_data: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    for node in graph_data.get("nodes", []):
        if node.get("id") == node_id:
            return node
    return None


def _collect_affected_nodes(
    graph_data: dict[str, Any],
    affected_ids: list[str],
) -> list[dict[str, Any]]:
    node_by_id = {node.get("id"): node for node in graph_data.get("nodes", [])}
    return [node_by_id[node_id] for node_id in affected_ids if node_id in node_by_id]


def _normalize_file(value: str | None) -> str:
    return str(value or "").replace("\\", "/")


def _build_markdown(plan: dict[str, Any]) -> str:
    node_name = plan.get("node_name", "Unknown node")
    summary = plan.get("summary", "")
    impacted_files = plan.get("impacted_files", [])
    impacted_modules = plan.get("impacted_modules", [])
    risk_hotspots = plan.get("risk_hotspots", [])
    test_focus = plan.get("test_focus_areas", [])
    checklist = plan.get("staged_checklist", [])

    lines = [
        f"# Action Plan - {node_name}",
        "",
        summary,
        "",
        "## Impacted Files",
    ]
    if impacted_files:
        lines.extend([f"- `{file_path}`" for file_path in impacted_files])
    else:
        lines.append("- None identified")

    lines.extend(["", "## Impacted Modules"])
    if impacted_modules:
        lines.extend([f"- `{module}`" for module in impacted_modules])
    else:
        lines.append("- None identified")

    lines.extend(["", "## Risk Hotspots"])
    if risk_hotspots:
        for hotspot in risk_hotspots:
            lines.append(
                f"- {hotspot.get('name', 'unknown')} ({hotspot.get('status', 'stable')}, churn {hotspot.get('churn_count', 0)})"
            )
    else:
        lines.append("- No elevated hotspots detected")

    lines.extend(["", "## Test Focus Areas"])
    if test_focus:
        lines.extend([f"- {item}" for item in test_focus])
    else:
        lines.append("- Add focused tests around callers/callees of the changed node")

    lines.extend(["", "## Staged Rollout Checklist"])
    if checklist:
        lines.extend([f"- [ ] {item}" for item in checklist])
    else:
        lines.append("- [ ] Validate local build and smoke tests")

    return "\n".join(lines).strip() + "\n"


def build_action_plan(graph_data: dict[str, Any], node_id: str) -> dict[str, Any]:
    node = _find_node(graph_data, node_id)
    if not node:
        return {
            "node_id": node_id,
            "error": "Node not found",
            "summary": "Could not generate an action plan because the selected node was not found.",
        }

    blast_data = compute_blast_radius(graph_data, node_id)
    affected_ids = list(dict.fromkeys(blast_data.get("affected_nodes", [])))
    affected_nodes = _collect_affected_nodes(graph_data, affected_ids)

    impacted_files = sorted(
        {
            _normalize_file(node_item.get("file"))
            for node_item in affected_nodes
            if _normalize_file(node_item.get("file"))
        }
    )
    module_counter = Counter(
        file_path.rsplit("/", 1)[0] if "/" in file_path else file_path
        for file_path in impacted_files
        if file_path
    )
    impacted_modules = [module for module, _count in module_counter.most_common(10)]

    hotspots = [
        {
            "id": node_item.get("id"),
            "name": node_item.get("name", "unknown"),
            "file": _normalize_file(node_item.get("file")),
            "status": str(node_item.get("mutation_status") or "stable"),
            "churn_count": int(node_item.get("churn_count") or 0),
        }
        for node_item in affected_nodes
        if str(node_item.get("mutation_status") or "").lower() in {"hotspot", "modified", "new"}
        or int(node_item.get("churn_count") or 0) >= 3
    ]
    hotspots.sort(
        key=lambda item: (
            3 if item["status"].lower() == "hotspot" else 2 if item["status"].lower() == "modified" else 1,
            item["churn_count"],
        ),
        reverse=True,
    )
    risk_hotspots = hotspots[:8]

    candidate_test_files = sorted(
        file_path
        for file_path in impacted_files
        if "test" in file_path.lower() or file_path.lower().endswith("_spec.py")
    )[:8]
    if candidate_test_files:
        test_focus_areas = [f"Update regression coverage in `{file_path}`" for file_path in candidate_test_files]
    else:
        primary_impacted = impacted_files[:5]
        test_focus_areas = [
            f"Add/extend unit tests for `{file_path}` call paths"
            for file_path in primary_impacted
        ] or [f"Add unit tests around `{node.get('name', 'selected node')}` behavior changes"]

    insights = graph_data.get("insights", {}) if isinstance(graph_data, dict) else {}
    high_fan_in = [item.get("name") for item in insights.get("fan_in", [])[:3] if isinstance(item, dict)]
    fan_in_note = (
        f"Prioritize backward compatibility for high fan-in nodes: {', '.join(high_fan_in)}."
        if high_fan_in
        else "Validate callers before rollout to avoid regressions."
    )

    staged_checklist = [
        f"Confirm intended change scope for `{node.get('name', 'selected node')}` and affected call graph depth.",
        f"Patch implementation in `{_normalize_file(node.get('file')) or 'target module'}` with focused commits.",
        "Run impacted unit/integration tests first, then full suite if hotspots are involved.",
        fan_in_note,
        "Roll out behind a feature flag or staged environment if blast depth is high.",
        "Monitor runtime errors and rollback quickly if hotspot modules show regressions.",
    ]

    summary = (
        f"Changing {node.get('name', 'this node')} may impact {len(affected_nodes)} nodes "
        f"across {len(impacted_modules)} modules and {len(impacted_files)} files."
    )

    plan = {
        "node_id": node_id,
        "node_name": node.get("name", "Unknown node"),
        "summary": summary,
        "blast_summary": blast_data.get("summary", ""),
        "impacted_files": impacted_files,
        "impacted_modules": impacted_modules,
        "risk_hotspots": risk_hotspots,
        "test_focus_areas": test_focus_areas,
        "staged_checklist": staged_checklist,
    }
    plan["markdown"] = _build_markdown(plan)
    return plan
