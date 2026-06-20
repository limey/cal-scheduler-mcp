#!/usr/bin/env python3
"""Verify the project version agrees across pyproject.toml, uv.lock, and server.json.

Exits 0 when every version string points to the same release, 1 otherwise.
Designed to run locally (pre-commit) and in CI (single step on Python 3.11+,
no third-party deps — stdlib tomllib/json only).
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT_NAME = "cal-scheduler-mcp"


def _read_pyproject() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def _read_uv_lock() -> str:
    data = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    matches = [p for p in data["package"] if p.get("name") == PROJECT_NAME]
    if len(matches) != 1:
        raise SystemExit(
            f"uv.lock: expected exactly one package entry for {PROJECT_NAME!r}, "
            f"found {len(matches)}"
        )
    return matches[0]["version"]


def _read_server_json() -> tuple[str, str]:
    data = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    top = data["version"]
    packages = data.get("packages", [])
    if len(packages) != 1:
        raise SystemExit(
            f"server.json: expected exactly one package entry, found {len(packages)}"
        )
    pkg = packages[0]["version"]
    return top, pkg


def main() -> int:
    pyproject = _read_pyproject()
    uv_lock = _read_uv_lock()
    server_top, server_pkg = _read_server_json()

    sources = {
        "pyproject.toml [project].version": pyproject,
        "uv.lock [[package]] cal-scheduler-mcp.version": uv_lock,
        "server.json .version": server_top,
        "server.json .packages[0].version": server_pkg,
    }

    unique = set(sources.values())
    if len(unique) == 1:
        version = next(iter(unique))
        print(f"OK: all version sources agree on {version}")
        for src, value in sources.items():
            print(f"  {src} = {value}")
        return 0

    print("FAIL: project version is not aligned across all sources:", file=sys.stderr)
    for src, value in sources.items():
        print(f"  {src} = {value}", file=sys.stderr)
    print(
        "\nFix: bump pyproject.toml [project].version and the two version "
        "fields in server.json to the same value, then refresh the lock with "
        "`uv lock` so uv.lock tracks the new release.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())