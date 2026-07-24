import asyncio
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from mcp.server.fastmcp import FastMCP
from audacity_mcp_shared.error_codes import AudacityMCPError, ErrorCode

from tests.conftest import register_tools


class TestToolRegistration:
    def test_all_tools_register(self, mock_client):
        mcp = FastMCP("TestAudacityMCP")
        with patch("audacity_mcp.main.client", mock_client):
            from audacity_mcp.tool_registry import register_all_tools
            register_all_tools(mcp)

        # Access internal tool manager directly to avoid async list_tools
        tool_count = len(mcp._tool_manager._tools)
        assert tool_count >= 131, f"Expected at least 131 tools, got {tool_count}"


class TestValidation:
    def test_format_command_injection(self):
        from audacity_mcp_shared.pipe_protocol import format_command
        with pytest.raises(AudacityMCPError) as exc_info:
            format_command("Evil\nCommand")
        assert exc_info.value.code == ErrorCode.INJECTION_DETECTED

    def test_error_code_values(self):
        assert ErrorCode.PIPE_NOT_FOUND == 1000
        assert ErrorCode.COMMAND_FAILED == 2000
        assert ErrorCode.VALIDATION_FAILED == 3000

    def test_every_error_code_is_raised_somewhere(self):
        """Five codes had accumulated that nothing ever raised (issue #14).

        Every member has to appear as `ErrorCode.<NAME>` in the shipped
        packages — its own definition does not count.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        sources = [
            p.read_text()
            for pkg in ("audacity_mcp", "audacity_mcp_shared")
            for p in (root / pkg).rglob("*.py")
            if p.name != "error_codes.py"
        ]
        blob = "\n".join(sources)
        unused = [c.name for c in ErrorCode if f"ErrorCode.{c.name}" not in blob]
        assert unused == [], f"ErrorCode members nothing raises: {unused}"

    def test_audacity_error_message(self):
        err = AudacityMCPError(ErrorCode.PIPE_NOT_FOUND, "not found")
        assert "PIPE_NOT_FOUND" in str(err)
        assert "1000" in str(err)
        assert "not found" in str(err)


class TestPathSafety:
    def test_safe_path_rejects_relative(self):
        from audacity_mcp.tools.project_tools import _safe_path
        with pytest.raises(AudacityMCPError) as exc_info:
            _safe_path("relative/path.wav")
        assert exc_info.value.code == ErrorCode.INVALID_PATH

    def test_safe_path_resolves_traversal(self):
        import os
        from audacity_mcp.tools.project_tools import _safe_path
        # Should resolve .. and return a clean path
        home = os.path.expanduser("~")
        traversal = os.path.join(home, "Music", "..", "Music", "test.wav")
        result = _safe_path(traversal)
        assert ".." not in result

    def test_safe_path_allows_the_temp_dir(self, tmp_path):
        # On macOS the temp dir resolves under /private/var, which the blocklist
        # would otherwise reject — see issue #13.
        import tempfile
        from audacity_mcp.tools.project_tools import _safe_path
        target = os.path.join(tempfile.gettempdir(), "audacity-mcp-max-export.wav")
        assert _safe_path(target) == os.path.realpath(target)
        assert _safe_path(str(tmp_path / "out.wav"))

    def test_safe_path_still_blocks_system_dir_on_posix(self):
        import sys
        if sys.platform == "win32":
            pytest.skip("POSIX-only test")
        from audacity_mcp.tools.project_tools import _safe_path
        with pytest.raises(AudacityMCPError) as exc_info:
            _safe_path("/etc/passwd")
        assert exc_info.value.code == ErrorCode.INVALID_PATH

    def test_safe_path_blocks_system_dir(self):
        import sys
        if sys.platform != "win32":
            pytest.skip("Windows-only test")
        from audacity_mcp.tools.project_tools import _safe_path
        with pytest.raises(AudacityMCPError) as exc_info:
            _safe_path(r"C:\Windows\System32\evil.wav")
        assert exc_info.value.code == ErrorCode.INVALID_PATH


@pytest.fixture
def effect_tools(mock_client):
    return register_tools("effects_tools", mock_client)


class TestEffectValidation:
    """Call the registered tool and assert on the raised error code.

    These replace three placeholders that asserted `True` and `3 % 2 != 0`,
    i.e. they exercised none of the code they claimed to cover.
    """

    def test_amplify_rejects_zero_ratio(self, effect_tools):
        with pytest.raises(AudacityMCPError) as exc_info:
            asyncio.run(effect_tools["effect_amplify"].fn(ratio=0))
        assert exc_info.value.code == ErrorCode.VALUE_OUT_OF_RANGE

    def test_amplify_rejects_negative_ratio(self, effect_tools):
        with pytest.raises(AudacityMCPError) as exc_info:
            asyncio.run(effect_tools["effect_amplify"].fn(ratio=-1.5))
        assert exc_info.value.code == ErrorCode.VALUE_OUT_OF_RANGE

    def test_amplify_passes_valid_ratio_through(self, effect_tools, mock_client):
        result = asyncio.run(effect_tools["effect_amplify"].fn(ratio=1.5))
        assert result["success"] is True
        mock_client.execute_long.assert_awaited_once_with("Amplify", Ratio=1.5)

    def test_phaser_rejects_odd_stages(self, effect_tools):
        with pytest.raises(AudacityMCPError) as exc_info:
            asyncio.run(effect_tools["effect_phaser"].fn(stages=3))
        assert exc_info.value.code == ErrorCode.VALUE_OUT_OF_RANGE

    def test_phaser_rejects_stages_above_range(self, effect_tools):
        with pytest.raises(AudacityMCPError) as exc_info:
            asyncio.run(effect_tools["effect_phaser"].fn(stages=26))
        assert exc_info.value.code == ErrorCode.VALUE_OUT_OF_RANGE

    def test_phaser_accepts_even_stages(self, effect_tools, mock_client):
        asyncio.run(effect_tools["effect_phaser"].fn(stages=4))
        assert mock_client.execute_long.await_args[1]["Stages"] == 4

    def test_equalization_rejects_even_length(self, effect_tools):
        with pytest.raises(AudacityMCPError) as exc_info:
            asyncio.run(effect_tools["effect_equalization"].fn(length=4000))
        assert exc_info.value.code == ErrorCode.INVALID_PARAMETER

    def test_equalization_rejects_length_out_of_range(self, effect_tools):
        with pytest.raises(AudacityMCPError) as exc_info:
            asyncio.run(effect_tools["effect_equalization"].fn(length=9))
        assert exc_info.value.code == ErrorCode.VALUE_OUT_OF_RANGE

    def test_equalization_accepts_odd_length(self, effect_tools, mock_client):
        asyncio.run(effect_tools["effect_equalization"].fn(length=4001))
        assert mock_client.execute_long.await_args[1]["FilterLength"] == 4001


@pytest.fixture
def track_tools(mock_client):
    return register_tools("track_tools", mock_client)


class TestResampleRateWarning:
    def test_uncommon_rate_is_flagged(self, track_tools):
        result = asyncio.run(track_tools["track_resample"].fn(rate=384000))
        assert "warning" in result
        assert "384000" in result["warning"]

    def test_common_rate_is_not_flagged(self, track_tools):
        result = asyncio.run(track_tools["track_resample"].fn(rate=48000))
        assert "warning" not in result

    def test_out_of_range_rate_still_raises(self, track_tools):
        with pytest.raises(AudacityMCPError) as exc_info:
            asyncio.run(track_tools["track_resample"].fn(rate=0))
        assert exc_info.value.code == ErrorCode.VALUE_OUT_OF_RANGE
