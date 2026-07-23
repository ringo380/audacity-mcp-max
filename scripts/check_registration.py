#!/usr/bin/env python3
"""Register every tool module against a fresh FastMCP and report the count.

This is the check that a module rename or a broken import in one tools module
would otherwise slip past: the server would start with a silently smaller tool
set, since register_all_tools walks the package and skips whatever it cannot
import.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

EXPECTED_TOOLS = 131

from mcp.server.fastmcp import FastMCP  # noqa: E402

from audacity_mcp.tool_registry import register_all_tools  # noqa: E402
from audacity_mcp.tools import __path__ as tools_path  # noqa: E402


def main() -> int:
    import importlib
    import pkgutil

    modules = [name for _, name, _ in pkgutil.iter_modules(tools_path)]
    mcp = FastMCP("VerifyAudacityMCPMax")
    register_all_tools(mcp)
    tools = mcp._tool_manager._tools

    print(f"tool modules: {len(modules)}")
    print(f"tools registered: {len(tools)} (expected at least {EXPECTED_TOOLS})")

    failed = False
    if len(tools) < EXPECTED_TOOLS:
        print(f"ERROR: only {len(tools)} tools registered", file=sys.stderr)
        failed = True

    # Register each module on its own, so a module that imports but contributes
    # nothing is visible rather than hidden in the total.
    for name in sorted(modules):
        per_module = FastMCP(f"Verify-{name}")
        module = importlib.import_module(f"audacity_mcp.tools.{name}")
        if not hasattr(module, "register"):
            print(f"ERROR: {name} has no register()", file=sys.stderr)
            failed = True
            continue
        module.register(per_module)
        count = len(per_module._tool_manager._tools)
        print(f"  {name}: {count}")
        if count == 0:
            print(f"ERROR: {name} contributed no tools", file=sys.stderr)
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
