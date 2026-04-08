from __future__ import annotations

import unittest

from graph.blast_radius import compute_blast_radius


class BlastRadiusTests(unittest.TestCase):
    def test_returns_empty_result_for_missing_node(self) -> None:
        result = compute_blast_radius({"nodes": [], "edges": []}, "missing")
        self.assertEqual(result["summary"], "Node not found.")
        self.assertEqual(result["affected_nodes"], [])

    def test_computes_reverse_dependency_layers(self) -> None:
        graph_data = {
            "nodes": [
                {"id": "a", "name": "a", "file": "one.py"},
                {"id": "b", "name": "b", "file": "two.py"},
                {"id": "c", "name": "c", "file": "three.py"},
            ],
            "edges": [
                {"source": "b", "target": "a"},
                {"source": "c", "target": "b"},
            ],
        }
        result = compute_blast_radius(graph_data, "a")
        self.assertEqual(result["depth_map"]["b"], 1)
        self.assertEqual(result["depth_map"]["c"], 2)
        self.assertEqual(result["risk_colors"]["b"], "#ff2222")
        self.assertEqual(result["risk_colors"]["c"], "#ff6644")


if __name__ == "__main__":
    unittest.main()
