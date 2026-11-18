#!/usr/bin/env python3
"""Install BrainGraph skills, always-on rules, and MCP config into a target project or global agent dir."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BRAINGRAPH_DIR = Path(__file__).resolve().parent

SKILL_MD = """---
name: braingraph
description: Query BrainGraph persistent memory and AST code graphs before scanning raw files.
---

# BrainGraph

Use BrainGraph MCP tools to avoid burning tokens re-reading files across sessions:

1. **Recall prior context first:** Call `recall(query, project)` before exploring a subsystem.
2. **Query code structure:** Call `query_graph(project, query)` and `explain_node(project, node_id)` instead of repo-wide grep when `graph.json` is indexed.
3. **Persist new findings:** After fixing a non-obvious bug or making an architectural decision, call `remember(topic, content, project, tags)`.
"""

RULE_MD = """---
trigger: always_on
description: Consult BrainGraph memory and AST graph before repo-wide file reads.
---

## BrainGraph

- Before scanning raw files for architecture or past decisions, call `recall` and `query_graph` via the `braingraph` MCP server.
- After resolving non-trivial bugs or architectural changes, save a concise note via `remember`.
"""

AGENTS_SECTION = """
## BrainGraph (Persistent Memory & Code Map)

- Before running broad file searches, query the `braingraph` MCP server (`recall`, `query_graph`, `explain_node`).
- Save key decisions, invariants, and bug root-causes with `remember` so future sessions across Antigravity, Codex, and other agents share context.
"""


def install_to_project(target: Path) -> None:
    """Write Antigravity skill/rule and append AGENTS.md guidance in target project."""
    skill_path = target / ".agents" / "skills" / "braingraph" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(SKILL_MD, encoding="utf-8")

    rule_path = target / ".agents" / "rules" / "braingraph.md"
    rule_path.parent.mkdir(parents=True, exist_ok=True)
    rule_path.write_text(RULE_MD, encoding="utf-8")

    agents_md = target / "AGENTS.md"
    existing = agents_md.read_text(encoding="utf-8") if agents_md.exists() else ""
    if "## BrainGraph" not in existing:
        agents_md.write_text(existing.rstrip() + "\n" + AGENTS_SECTION.lstrip(), encoding="utf-8")

    print(f"Installed BrainGraph skill, rule, and AGENTS.md block into: {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install BrainGraph into a project or print MCP config")
    parser.add_argument("target", nargs="?", type=Path, help="Target repository directory")
    parser.add_argument("--print-mcp", action="store_true", help="Print MCP JSON config for Antigravity/Codex/Claude")
    args = parser.parse_args()

    if args.target:
        install_to_project(args.target.resolve())

    if args.print_mcp or not args.target:
        mcp_cfg = {
            "mcpServers": {
                "braingraph": {
                    "command": "python3",
                    "args": [str(BRAINGRAPH_DIR / "brain_mcp.py")],
                }
            }
        }
        print("\nAdd this to your Antigravity / Codex / Claude MCP config:")
        print(json.dumps(mcp_cfg, indent=2))


if __name__ == "__main__":
    main()
