#!/usr/bin/env python3
"""Index local repositories using Graphify's zero-cost AST parser and register them in BrainGraph."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from brain_mcp import BrainStore, DEFAULT_DATA_DIR


def index_repo(repo_dir: Path, store: BrainStore, graphify_bin: str) -> bool:
    """Run Graphify AST-only update/build on repo_dir and register graph.json in BrainStore."""
    repo_dir = repo_dir.resolve()
    if not repo_dir.is_dir():
        print(f"[skip] Not a directory: {repo_dir}", file=sys.stderr)
        return False

    graph_file = repo_dir / "graphify-out" / "graph.json"
    cmd = [graphify_bin, "update" if graph_file.exists() else "build", "--code-only", str(repo_dir)]
    # Fallback for graphify versions where `update .` takes cwd
    if graph_file.exists():
        cmd = [graphify_bin, "update", "."]
    else:
        cmd = [graphify_bin, "extract", ".", "--code-only"]

    print(f"[index] {repo_dir.name}: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[warn] {repo_dir.name} returned {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}", file=sys.stderr)

    if graph_file.is_file():
        dst = store.graphs_dir / f"{repo_dir.name}.json"
        shutil.copy2(graph_file, dst)
        store.register_project(repo_dir.name, str(repo_dir), str(graph_file))
        print(f"[ok] {repo_dir.name} -> {dst}")
        return True

    print(f"[miss] No graphify-out/graph.json produced for {repo_dir.name}", file=sys.stderr)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Index local Git repositories into BrainGraph")
    parser.add_argument("targets", nargs="*", type=Path, help="Repo directories to index")
    parser.add_argument("--all-in", type=Path, help="Index all git repos inside this parent directory")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()

    graphify_bin = shutil.which("graphify") or str(Path.home() / ".local" / "bin" / "graphify")
    if not Path(graphify_bin).exists():
        print("Error: graphify CLI not found. Install with: uv tool install graphifyy", file=sys.stderr)
        sys.exit(1)

    repos: list[Path] = list(args.targets)
    if args.all_in:
        for child in sorted(args.all_in.iterdir()):
            if child.is_dir() and (child / ".git").exists():
                repos.append(child)

    if not repos:
        parser.print_help()
        sys.exit(1)

    store = BrainStore(args.data_dir)
    ok = sum(1 for r in repos if index_repo(r, store, graphify_bin))
    print(f"Indexed {ok}/{len(repos)} repositories into {args.data_dir}")


if __name__ == "__main__":
    main()
