"""The plugin manifests, checked the way a broken one actually fails.

A typo in .mcp.json does not fail anything at build time. It fails at MCP client
session start, hours away from the edit, as a server that never connects.
"""
import json
import os
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_JSON = REPO / ".claude-plugin" / "plugin.json"
MCP_JSON = REPO / ".mcp.json"


def pyproject_version():
    text = (REPO / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject has no version"
    return match.group(1)


class TestPluginManifest:
    def test_it_parses_and_declares_what_the_catalog_needs(self):
        data = json.loads(PLUGIN_JSON.read_text())
        for key in ("name", "version", "description", "author", "license"):
            assert key in data, f"plugin.json is missing {key}"
        assert data["name"] == "audacity"
        # Apache-2.0, not MIT. The marketplace derives licence copy from this,
        # and a wrong value there misrepresents the licence publicly.
        assert data["license"] == "Apache-2.0"

    def test_version_matches_pyproject(self):
        data = json.loads(PLUGIN_JSON.read_text())
        assert data["version"] == pyproject_version()


class TestMcpManifest:
    def test_it_declares_one_stdio_server_pointing_at_the_launcher(self):
        data = json.loads(MCP_JSON.read_text())
        servers = data["mcpServers"]
        assert list(servers) == ["audacity"]
        entry = servers["audacity"]
        assert entry["type"] == "stdio"
        assert entry["command"] == "${CLAUDE_PLUGIN_ROOT}/scripts/launch-mcp.sh"

    def test_the_command_it_names_exists_and_is_executable(self):
        data = json.loads(MCP_JSON.read_text())
        command = data["mcpServers"]["audacity"]["command"]
        resolved = REPO / command.replace("${CLAUDE_PLUGIN_ROOT}/", "")
        assert resolved.is_file(), f"{resolved} does not exist"
        assert os.access(resolved, os.X_OK), f"{resolved} is not executable"
