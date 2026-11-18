#!/usr/bin/env python3
"""Unit and stdio MCP integration tests for BrainGraph."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from brain_mcp import BrainStore


class TestBrainGraph(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.store = BrainStore(self.data_dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_remember_and_recall_fts(self) -> None:
        res = self.store.remember(
            topic="Session auth",
            content="API gateway uses JWT tokens with rotating signing keys.",
            project="MyApp",
            tags="auth,security",
        )
        self.assertEqual(res["status"], "saved")
        hits = self.store.recall("JWT", project="MyApp")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["topic"], "Session auth")

    def test_graph_query_and_explain(self) -> None:
        graph = {
            "nodes": [
                {"id": "n1", "label": "AuthService", "source_file": "src/auth.py"},
                {"id": "n2", "label": "verifyToken", "source_file": "src/tokens.py"},
            ],
            "links": [
                {"source": "n1", "target": "n2", "relation": "calls"},
            ],
        }
        (self.data_dir / "graphs" / "MyApp.json").write_text(json.dumps(graph), encoding="utf-8")
        q = self.store.query_graph("MyApp", "AuthService")
        self.assertEqual(len(q["nodes"]), 1)
        self.assertEqual(len(q["edges"]), 1)

        exp = self.store.explain_node("MyApp", "AuthService")
        self.assertEqual(exp["node"]["id"], "n1")
        self.assertEqual(len(exp["outgoing"]), 1)

    def test_project_isolation_and_explicit_links(self) -> None:
        self.store.remember("Auth", "Uses JWT", project="ABC")
        self.store.remember("Billing", "Uses Stripe", project="DEF")
        self.assertEqual(len(self.store.recall("Uses", project="ABC")), 1)
        self.assertEqual(self.store.recall("Stripe", project="ABC"), [])

        self.store.link_projects("ABC", "DEF", "calls_api", "ABC calls DEF billing webhook")
        links = self.store.get_project_links("ABC")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["target_project"], "DEF")

    def test_stdio_mcp_roundtrip(self) -> None:
        server = Path(__file__).resolve().parent.parent / "brain_mcp.py"
        requests = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            json.dumps({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "remember", "arguments": {"topic": "Boot", "content": "Zero-cost local test", "project": "Demo"}},
            }),
            json.dumps({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "recall", "arguments": {"query": "Zero-cost", "project": "Demo"}},
            }),
        ]) + "\n"
        proc = subprocess.run(
            [sys.executable, str(server), "--data-dir", str(self.data_dir)],
            input=requests,
            text=True,
            capture_output=True,
            check=True,
        )
        lines = [json.loads(line) for line in proc.stdout.strip().splitlines()]
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0]["result"]["serverInfo"]["name"], "braingraph")
        self.assertEqual(len(lines[1]["result"]["tools"]), 7)
        recalled = json.loads(lines[3]["result"]["content"][0]["text"])
        self.assertEqual(recalled[0]["topic"], "Boot")


if __name__ == "__main__":
    unittest.main()
