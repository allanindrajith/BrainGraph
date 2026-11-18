#!/usr/bin/env python3
"""BrainGraph MCP Server: Persistent cross-agent memory (SQLite FTS5) + Graphify JSON query engine.

Supports:
  - stdio transport (default) for local Antigravity, Codex, Claude Code, Cursor
  - HTTP POST /mcp transport (--http) with optional bearer token for cloud/mobile access
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIR = Path(os.environ.get("BRAINGRAPH_DATA_DIR", Path(__file__).parent / "data"))
PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "braingraph", "version": "0.1.0"}


class BrainStore:
    """Manages persistent SQLite FTS5 memories and local Graphify graph.json lookups."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.graphs_dir = self.data_dir / "graphs"
        self.graphs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "brain.db"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    project, topic, content, tags,
                    content='memories',
                    content_rowid='id'
                );
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, project, topic, content, tags)
                    VALUES (new.id, new.project, new.topic, new.content, new.tags);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, project, topic, content, tags)
                    VALUES ('delete', old.id, old.project, old.topic, old.content, old.tags);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, project, topic, content, tags)
                    VALUES ('delete', old.id, old.project, old.topic, old.content, old.tags);
                    INSERT INTO memories_fts(rowid, project, topic, content, tags)
                    VALUES (new.id, new.project, new.topic, new.content, new.tags);
                END;
                CREATE TABLE IF NOT EXISTS projects (
                    name TEXT PRIMARY KEY,
                    repo_path TEXT NOT NULL,
                    graph_path TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS project_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_project TEXT NOT NULL,
                    target_project TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(source_project, target_project, relation)
                );
                """
            )

    def remember(self, topic: str, content: str, project: str = "", tags: str = "") -> dict[str, Any]:
        """Store a new memory entry and index it in FTS5."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO memories (project, topic, content, tags) VALUES (?, ?, ?, ?)",
                (project.strip(), topic.strip(), content.strip(), tags.strip()),
            )
            return {"id": cur.lastrowid, "project": project.strip(), "topic": topic.strip(), "status": "saved"}

    def recall(self, query: str, project: str = "", limit: int = 10) -> list[dict[str, Any]]:
        """Full-text search memories, optionally filtered by project."""
        limit = max(1, min(int(limit), 50))
        safe_q = " ".join(f'"{tok}"' for tok in query.replace('"', " ").split() if tok)
        if not safe_q:
            return []
        sql = """
            SELECT m.id, m.project, m.topic, m.content, m.tags, m.updated_at
            FROM memories_fts f
            JOIN memories m ON m.id = f.rowid
            WHERE memories_fts MATCH ?
        """
        params: list[Any] = [safe_q]
        if project.strip():
            sql += " AND m.project = ?"
            params.append(project.strip())
        sql += " ORDER BY bm25(memories_fts) LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def link_projects(self, source_project: str, target_project: str, relation: str, note: str = "") -> dict[str, Any]:
        """Record an explicit cross-project dependency or shared interface."""
        src, dst, rel = source_project.strip(), target_project.strip(), relation.strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO project_links (source_project, target_project, relation, note)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_project, target_project, relation) DO UPDATE SET note = excluded.note
                """,
                (src, dst, rel, note.strip()),
            )
        return {"source_project": src, "target_project": dst, "relation": rel, "status": "linked"}

    def get_project_links(self, project: str) -> list[dict[str, Any]]:
        """Return explicit cross-project links involving project."""
        p = project.strip()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source_project, target_project, relation, note FROM project_links WHERE source_project = ? OR target_project = ?",
                (p, p),
            ).fetchall()
            return [dict(r) for r in rows]

    def register_project(self, name: str, repo_path: str, graph_path: str) -> dict[str, Any]:
        """Register or update a project's repo and graph.json path."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (name, repo_path, graph_path, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(name) DO UPDATE SET
                    repo_path = excluded.repo_path,
                    graph_path = excluded.graph_path,
                    updated_at = datetime('now')
                """,
                (name, repo_path, graph_path),
            )
        return {"project": name, "repo_path": repo_path, "graph_path": graph_path}

    def list_projects(self) -> list[dict[str, Any]]:
        """List registered projects and any graph JSONs under data/graphs/."""
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY name").fetchall()]
        known = {r["name"] for r in rows}
        for p in sorted(self.graphs_dir.glob("*.json")):
            if p.stem not in known:
                rows.append({"name": p.stem, "repo_path": "", "graph_path": str(p), "updated_at": ""})
        return rows

    def _load_graph(self, project: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT graph_path, repo_path FROM projects WHERE name = ?", (project,)).fetchone()
        candidates: list[Path] = []
        if row:
            candidates.append(Path(row["graph_path"]))
            if row["repo_path"]:
                candidates.append(Path(row["repo_path"]) / "graphify-out" / "graph.json")
        candidates.append(self.graphs_dir / f"{project}.json")
        for path in candidates:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"No graph.json found for project '{project}'")

    def query_graph(self, project: str, query: str, limit: int = 15) -> dict[str, Any]:
        """Search nodes and connected edges in a project's graph.json."""
        data = self._load_graph(project)
        nodes = data.get("nodes", [])
        edges = data.get("links") or data.get("edges") or []
        terms = [t.lower() for t in query.split() if t.strip()]
        matched_nodes = []
        for n in nodes:
            hay = f"{n.get('id', '')} {n.get('label', '')} {n.get('source_file', '')}".lower()
            if all(t in hay for t in terms):
                matched_nodes.append(n)
                if len(matched_nodes) >= limit:
                    break
        matched_ids = {n.get("id") for n in matched_nodes}
        related_edges = [
            e for e in edges if e.get("source") in matched_ids or e.get("target") in matched_ids
        ][: limit * 2]
        return {"project": project, "nodes": matched_nodes, "edges": related_edges}

    def explain_node(self, project: str, node_id: str) -> dict[str, Any]:
        """Return a node and its 1-hop incoming/outgoing neighbors from graph.json."""
        data = self._load_graph(project)
        nodes_by_id = {n.get("id"): n for n in data.get("nodes", [])}
        edges = data.get("links") or data.get("edges") or []
        node = nodes_by_id.get(node_id)
        if not node:
            # Fallback to case-insensitive label/id match
            for nid, candidate in nodes_by_id.items():
                if str(nid).lower() == node_id.lower() or str(candidate.get("label", "")).lower() == node_id.lower():
                    node_id = nid
                    node = candidate
                    break
        if not node:
            return {"error": f"Node '{node_id}' not found in '{project}'"}
        incoming = [e for e in edges if e.get("target") == node_id]
        outgoing = [e for e in edges if e.get("source") == node_id]
        return {"node": node, "incoming": incoming[:30], "outgoing": outgoing[:30]}


TOOLS_SPEC = [
    {
        "name": "remember",
        "description": "Save a decision, architecture note, bug fix, or context to persistent cross-agent memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Short title or topic"},
                "content": {"type": "string", "description": "Detailed fact, decision, or note"},
                "project": {"type": "string", "description": "Optional project name (e.g. MyApp)"},
                "tags": {"type": "string", "description": "Comma-separated tags"},
            },
            "required": ["topic", "content"],
        },
    },
    {
        "name": "recall",
        "description": "Search persistent cross-agent memory using full-text search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
                "project": {"type": "string", "description": "Optional project filter"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_projects",
        "description": "List all projects indexed in BrainGraph.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "query_graph",
        "description": "Query a project's AST knowledge graph (graph.json) for symbols, files, and edges.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name"},
                "query": {"type": "string", "description": "Symbol, file, or keyword"},
                "limit": {"type": "integer", "description": "Max nodes (default 15)"},
            },
            "required": ["project", "query"],
        },
    },
    {
        "name": "explain_node",
        "description": "Get a node and its 1-hop callers/callees/imports from a project's knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name"},
                "node_id": {"type": "string", "description": "Node ID or exact label"},
            },
            "required": ["project", "node_id"],
        },
    },
    {
        "name": "link_projects",
        "description": "Create an explicit cross-project edge only when two separate projects genuinely connect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_project": {"type": "string", "description": "Source project name"},
                "target_project": {"type": "string", "description": "Target project name"},
                "relation": {"type": "string", "description": "Relationship (e.g. calls_api, shares_protocol)"},
                "note": {"type": "string", "description": "Details of the connection"},
            },
            "required": ["source_project", "target_project", "relation"],
        },
    },
    {
        "name": "get_project_links",
        "description": "List explicit cross-project connections for a project.",
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string", "description": "Project name"}},
            "required": ["project"],
        },
    },
]


def handle_rpc(store: BrainStore, msg: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch a single JSON-RPC 2.0 MCP request."""
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "notifications/initialized":
        return None

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS_SPEC}}

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            if name == "remember":
                out = store.remember(**args)
            elif name == "recall":
                out = store.recall(**args)
            elif name == "list_projects":
                out = store.list_projects()
            elif name == "query_graph":
                out = store.query_graph(**args)
            elif name == "explain_node":
                out = store.explain_node(**args)
            elif name == "link_projects":
                out = store.link_projects(**args)
            elif name == "get_project_links":
                out = store.get_project_links(**args)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {name}"},
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps({"error": str(exc)})}], "isError": True},
            }

    if req_id is None:
        return None
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def serve_stdio(store: BrainStore) -> None:
    """Run MCP server over newline-delimited JSON-RPC on stdin/stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}) + "\n")
            sys.stdout.flush()
            continue
        resp = handle_rpc(store, msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def serve_http(store: BrainStore, host: str, port: int, token: str) -> None:
    """Run MCP server over HTTP POST /mcp with optional Bearer token auth."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:
            if self.path != "/mcp":
                self.send_response(404)
                self.end_headers()
                return
            if token:
                auth = self.headers.get("Authorization", "")
                if auth != f"Bearer {token}":
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'{"error":"unauthorized"}')
                    return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                msg = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
                return
            resp = handle_rpc(store, msg)
            self.send_response(200 if resp is not None else 204)
            if resp is not None:
                body = json.dumps(resp).encode("utf-8")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.end_headers()

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write(f"[braingraph-http] {fmt % args}\n")

    server = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(f"BrainGraph HTTP MCP listening on http://{host}:{port}/mcp\n")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="BrainGraph MCP Server")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--http", action="store_true", help="Serve over HTTP instead of stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--token", default=os.environ.get("BRAINGRAPH_TOKEN", ""))
    args = parser.parse_args()

    store = BrainStore(args.data_dir)
    if args.http:
        serve_http(store, args.host, args.port, args.token)
    else:
        serve_stdio(store)


if __name__ == "__main__":
    main()
