from __future__ import annotations

from typing import Any

from git_tracker import mutation_tracker
from graph import graph_builder
from plugins.base import BaseLanguagePlugin


class PythonLanguagePlugin(BaseLanguagePlugin):
    language = "python"
    label = "Python"
    extensions = (".py",)
    supports_mutation_tracking = True
    ready = True

    def scan(self, root_path: str) -> dict[str, Any]:
        graph_data = graph_builder.build_graph(root_path)
        graph_data["nodes"] = mutation_tracker.track_mutations(
            root_path, graph_data.get("nodes", [])
        )
        graph_data["meta"] = {
            "language": self.language,
            "plugin_label": self.label,
            "mode": "full",
        }
        return graph_data


if __name__ == "__main__":
    print(PythonLanguagePlugin().describe())
