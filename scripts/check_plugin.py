#!/usr/bin/env python3
"""Assert the plugin manifests agree with the Python project.

Two files carry the version and one names an executable. Nothing at build time
notices when they drift - the failure lands at MCP client session start, as a
server that silently never connects.
"""
import json
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def pyproject_version(text):
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def main() -> int:
    failed = False

    try:
        plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        mcp = json.loads((REPO / ".mcp.json").read_text())
        # pyproject belongs in here too: read outside, an unreadable one aborts
        # with a raw traceback instead of the readable line this script promises
        # everywhere else.
        py_version = pyproject_version((REPO / "pyproject.toml").read_text())
    except (OSError, ValueError) as e:
        print(f"ERROR: could not read the plugin manifests: {e}", file=sys.stderr)
        return 1

    print(f"plugin: {plugin.get('name')} {plugin.get('version')}")
    print(f"pyproject version: {py_version}")
    if plugin.get("version") != py_version:
        print(
            f"ERROR: version drift - plugin.json {plugin.get('version')} "
            f"vs pyproject {py_version}",
            file=sys.stderr,
        )
        failed = True

    servers = mcp.get("mcpServers", {})
    print(f"mcp servers: {', '.join(servers) or 'none'}")
    for name, entry in servers.items():
        command = entry.get("command", "")
        resolved = REPO / command.replace("${CLAUDE_PLUGIN_ROOT}/", "")
        if not resolved.is_file():
            print(f"ERROR: {name} command does not exist: {resolved}", file=sys.stderr)
            failed = True
        elif not os.access(resolved, os.X_OK):
            print(f"ERROR: {name} command is not executable: {resolved}", file=sys.stderr)
            failed = True
        else:
            print(f"  {name}: {command} ok")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
