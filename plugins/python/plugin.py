from __future__ import annotations

from typing import Any

from git_tracker import mutation_tracker
from graph import graph_builder
from graph.insights import compute_insights
from plugins.base import BaseLanguagePlugin


class PythonLanguagePlugin(BaseLanguagePlugin):
    language = "python"
    label = "Python"
    extensions = (".py",)
    supports_mutation_tracking = True
    ready = True

    def scan(self, root_path: str, **options: Any) -> dict[str, Any]:
        include_summaries = bool(options.get("include_summaries", True))
        include_mutation_tracking = bool(options.get("include_mutation_tracking", True))
        graph_data = graph_builder.build_graph(root_path, include_summaries=include_summaries)
        if include_mutation_tracking:
            graph_data["nodes"] = mutation_tracker.track_mutations(
                root_path, graph_data.get("nodes", [])
            )
        graph_data["meta"] = {
            "language": self.language,
            "plugin_label": self.label,
            "mode": "full" if include_summaries else "lightweight",
        }
        graph_data["insights"] = compute_insights(graph_data)
        return graph_data


if __name__ == "__main__":
    print(PythonLanguagePlugin().describe())
