import pytest
from unittest.mock import AsyncMock, MagicMock

from audacity_mcp_shared.constants import PipePaths


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.execute = AsyncMock(return_value={"success": True, "raw": "", "message": "", "data": {}})
    client.execute_long = AsyncMock(return_value={"success": True, "raw": "", "message": "", "data": {}})
    return client


@pytest.fixture(autouse=True)
def isolated_pipe_paths(tmp_path, monkeypatch):
    """Point the pipe paths at a per-test temp dir, for every test.

    Without this the client reaches the real FIFOs at /tmp/audacity_script_pipe.*,
    so the suite's outcome depends on whether those exist — a developer with
    Audacity open and one with it closed get different results from the same tree.
    The paths do not exist unless a test creates them, so the default is a clean
    "Audacity is not running" environment.
    """
    monkeypatch.setattr(PipePaths, "TO_SRV", str(tmp_path / "to_srv"))
    monkeypatch.setattr(PipePaths, "FROM_SRV", str(tmp_path / "from_srv"))
    return tmp_path


def register_tools(module_name: str, client) -> dict:
    """Register one tools module against a throwaway FastMCP, bound to `client`.

    The tool modules do `from audacity_mcp.main import client` inside register(),
    so the patch has to be in place while register() runs — after that the client
    is captured in each tool's closure.
    """
    import importlib
    from unittest.mock import patch
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(f"Test-{module_name}")
    with patch("audacity_mcp.main.client", client):
        module = importlib.import_module(f"audacity_mcp.tools.{module_name}")
        module.register(mcp)
    return mcp._tool_manager._tools
