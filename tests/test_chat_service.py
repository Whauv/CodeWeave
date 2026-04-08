from __future__ import annotations

import unittest

from server.chat_service import build_chat_context, build_project_context


class ChatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph_data = {
            "nodes": [
                {"id": "a", "name": "load_data", "file": "ingestion.py", "line": 10, "type": "function", "summary": "Loads market data.", "mutation_status": "hotspot"},
                {"id": "b", "name": "clean_data", "file": "ingestion.py", "line": 22, "type": "function", "summary": "Cleans market data.", "mutation_status": "stable"},
                {"id": "c", "name": "train_model", "file": "model.py", "line": 40, "type": "function", "summary": "Trains the model.", "mutation_status": "stable"},
            ],
            "edges": [
                {"source": "b", "target": "a"},
                {"source": "c", "target": "b"},
            ],
        }

    def test_build_project_context_includes_coupling_and_hotspots(self) -> None:
        context = build_project_context(self.graph_data)
        self.assertIn("Project stats: 3 nodes, 2 edges.", context)
        self.assertIn("Hotspot nodes: load_data", context)
        self.assertIn("ingestion.py <-> model.py", context)

    def test_build_chat_context_for_selected_node_includes_neighbors(self) -> None:
        context = build_chat_context(self.graph_data, "b")
        self.assertIn("- name: clean_data", context)
        self.assertIn("- callers: train_model", context)
        self.assertIn("- callees: load_data", context)


if __name__ == "__main__":
    unittest.main()
