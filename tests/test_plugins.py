from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from plugins.registry import get_language_options, get_plugin


class PluginRegistryTests(unittest.TestCase):
    def test_registry_contains_expected_languages(self) -> None:
        languages = {item["language"] for item in get_language_options()}
        self.assertEqual(languages, {"python", "typescript", "go", "java"})

    def test_typescript_plugin_builds_nodes(self) -> None:
        temp_dir = Path.cwd() / "tests_runtime_plugin"
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir.mkdir(parents=True, exist_ok=True)
            source_path = temp_dir / "sample.ts"
            source_path.write_text(
                "class UserService { fetchUser(id: string) { return getUser(id); } }\n"
                "function getUser(id: string) { return id; }\n",
                encoding="utf-8",
            )
            graph = get_plugin("typescript").scan(str(temp_dir))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        node_names = {node["name"] for node in graph["nodes"]}
        self.assertIn("UserService", node_names)
        self.assertIn("UserService.fetchUser", node_names)
        self.assertIn("getUser", node_names)

    def test_typescript_plugin_builds_import_and_inheritance_edges(self) -> None:
        temp_dir = Path.cwd() / "tests_runtime_plugin"
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir.mkdir(parents=True, exist_ok=True)
            (temp_dir / "base.ts").write_text(
                "export class BaseService { run() { return 1; } }\n"
                "export interface Runnable { execute(): number; }\n",
                encoding="utf-8",
            )
            (temp_dir / "main.ts").write_text(
                "import { BaseService, Runnable } from './base';\n"
                "class FeatureService extends BaseService implements Runnable {\n"
                "  execute() { return this.run(); }\n"
                "}\n",
                encoding="utf-8",
            )
            graph = get_plugin("typescript").scan(str(temp_dir))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        edge_types = {edge["type"] for edge in graph["edges"]}
        self.assertIn("import", edge_types)
        self.assertIn("extends", edge_types)
        self.assertIn("implements", edge_types)
        self.assertIn("call", edge_types)
        self.assertIn("insights", graph)


if __name__ == "__main__":
    unittest.main()
