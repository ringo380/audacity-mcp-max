import asyncio
import os

import pytest

from audacity_mcp_shared.constants import PipePaths
from audacity_mcp_shared.error_codes import AudacityMCPError, ErrorCode
from tests.conftest import register_tools


@pytest.fixture
def project_tools(mock_client):
    return register_tools("project_tools", mock_client)


def health(project_tools):
    return asyncio.run(project_tools["audacity_health_check"].fn())


def make_pipes(*paths):
    for p in paths:
        os.mkfifo(p)


class TestHealthCheck:
    """isolated_pipe_paths points both pipes at an empty temp dir, so the
    default state here is "Audacity is not running"."""

    def test_no_pipes_reports_unhealthy_with_the_relaunch_step(self, project_tools):
        result = health(project_tools)
        assert result["healthy"] is False
        assert result["pipes"]["to"]["exists"] is False
        assert result["pipes"]["from"]["exists"] is False
        assert len(result["next_steps"]) >= 1
        assert "mod-script-pipe" in result["next_steps"][0]

    def test_one_pipe_is_called_out_as_a_partial_activation(self, project_tools):
        make_pipes(PipePaths.TO_SRV)
        result = health(project_tools)
        assert result["healthy"] is False
        assert "did not fully activate" in result["next_steps"][0]
        assert "'from' pipe is missing" in result["next_steps"][0]

    def test_no_round_trip_is_attempted_without_both_pipes(self, project_tools, mock_client):
        make_pipes(PipePaths.TO_SRV)
        health(project_tools)
        mock_client.execute.assert_not_awaited()

    def test_both_pipes_and_a_reply_is_healthy(self, project_tools, mock_client):
        make_pipes(PipePaths.TO_SRV, PipePaths.FROM_SRV)
        result = health(project_tools)
        assert result["healthy"] is True
        assert result["next_steps"] == []
        assert mock_client.execute.await_args[0][0] == "GetInfo"
        assert mock_client.execute.await_args[1]["Type"] == "Tracks"

    def test_round_trip_uses_the_short_timeout(self, project_tools, mock_client):
        """Stale pipes answer nothing at all, so the default 30s command timeout
        would make the diagnostic tool the slowest call in the session."""
        from audacity_mcp_shared.constants import Timeouts

        make_pipes(PipePaths.TO_SRV, PipePaths.FROM_SRV)
        health(project_tools)
        assert mock_client.execute.await_args[1]["_timeout"] == Timeouts.HEALTH_CHECK
        assert Timeouts.HEALTH_CHECK < Timeouts.COMMAND

    def test_pipes_present_but_silent_reports_the_stale_case(self, project_tools, mock_client):
        make_pipes(PipePaths.TO_SRV, PipePaths.FROM_SRV)
        mock_client.execute.side_effect = AudacityMCPError(ErrorCode.PIPE_TIMEOUT, "no answer")
        result = health(project_tools)
        assert result["healthy"] is False
        assert "PIPE_TIMEOUT" in result["round_trip"]["error"]
        assert "stale" in result["next_steps"][0]

    def test_a_non_mcp_failure_still_counts_as_a_failed_round_trip(self, project_tools, mock_client):
        make_pipes(PipePaths.TO_SRV, PipePaths.FROM_SRV)
        mock_client.execute.side_effect = RuntimeError("something else")
        result = health(project_tools)
        assert result["healthy"] is False
        assert "RuntimeError" in result["round_trip"]["error"]

    def test_pipe_age_is_reported(self, project_tools):
        make_pipes(PipePaths.TO_SRV, PipePaths.FROM_SRV)
        result = health(project_tools)
        assert result["pipes"]["to"]["age_seconds"] >= 0

    def test_client_module_path_points_at_the_imported_copy(self, project_tools):
        import audacity_mcp.audacity_client as module

        assert health(project_tools)["client_module"] == module.__file__


class TestSampleRateReporting:
    def test_exotic_default_rate_is_flagged(self, project_tools, tmp_path, monkeypatch):
        cfg = tmp_path / "audacity.cfg"
        cfg.write_text("[/SamplingRate]\nDefaultProjectSampleRate=384000\n")
        monkeypatch.setattr(
            "audacity_mcp.tools.project_tools._audacity_cfg_path", lambda: str(cfg)
        )
        result = health(project_tools)
        assert result["default_project_sample_rate"] == 384000
        assert any("384000 Hz" in s for s in result["next_steps"])

    def test_normal_default_rate_is_not_flagged(self, project_tools, tmp_path, monkeypatch):
        cfg = tmp_path / "audacity.cfg"
        cfg.write_text("[/SamplingRate]\nDefaultProjectSampleRate=44100\n")
        monkeypatch.setattr(
            "audacity_mcp.tools.project_tools._audacity_cfg_path", lambda: str(cfg)
        )
        result = health(project_tools)
        assert result["default_project_sample_rate"] == 44100
        assert not any("Hz" in s for s in result["next_steps"])

    def test_missing_config_is_not_an_error(self, project_tools, monkeypatch):
        monkeypatch.setattr(
            "audacity_mcp.tools.project_tools._audacity_cfg_path", lambda: None
        )
        result = health(project_tools)
        assert result["config_file"] is None
        assert result["default_project_sample_rate"] is None


class TestGetInfoCommandsIsBlocked:
    """GetInfo Type=Commands hangs Audacity and takes it down with unsaved
    work. It used to be in the allowed set, reachable by enumerating the
    documented info_type values (issue #7)."""

    def test_commands_is_rejected(self, project_tools, mock_client):
        with pytest.raises(AudacityMCPError) as exc:
            asyncio.run(project_tools["project_get_info"].fn(info_type="Commands"))
        assert exc.value.code == ErrorCode.INVALID_PARAMETER
        assert "crashes it" in str(exc.value)
        mock_client.execute.assert_not_awaited()

    def test_the_other_types_still_work(self, project_tools, mock_client):
        asyncio.run(project_tools["project_get_info"].fn(info_type="Labels"))
        assert mock_client.execute.await_args[1]["Type"] == "Labels"

    def test_an_unknown_type_does_not_mention_commands(self, project_tools):
        with pytest.raises(AudacityMCPError) as exc:
            asyncio.run(project_tools["project_get_info"].fn(info_type="Nonsense"))
        assert "Commands" not in str(exc.value)


class TestWindowsPathCorruptionWarning:
    """A path with a backslash-then-n segment (C:\\new) cannot survive
    Audacity's parser (issue #5). Doubling is still correct; the tools warn so
    the corruption is not silent."""

    def _fresh_replies(self, mock_client):
        async def _execute(command, extra_params=None, **params):
            return {"success": True, "raw": "", "message": "", "data": {}}
        mock_client.execute.side_effect = _execute
        mock_client.execute_long.side_effect = _execute

    # The corruption is about a literal backslash-then-n reaching the wire; a
    # real Windows path (C:\new) is rejected as non-absolute on this POSIX host,
    # so these use absolute paths whose filename carries the same "\n" sequence.
    def test_import_warns_on_a_corrupting_path(self, project_tools, mock_client, tmp_path):
        self._fresh_replies(mock_client)
        bad = str(tmp_path) + "/hold\\new.wav"  # ...\new -> backslash + n
        result = asyncio.run(project_tools["project_import_audio"].fn(path=bad))
        assert any("newline" in w for w in result.get("warnings", []))

    def test_import_does_not_warn_on_a_safe_path(self, project_tools, mock_client, tmp_path):
        self._fresh_replies(mock_client)
        safe = str(tmp_path / "New" / "a.wav")
        result = asyncio.run(project_tools["project_import_audio"].fn(path=safe))
        assert not any("newline" in w for w in result.get("warnings", []))

    def test_export_warns_on_a_corrupting_path(self, project_tools, mock_client, tmp_path):
        self._fresh_replies(mock_client)
        path = str(tmp_path) + "/hold\\new_take.wav"  # ...\new_take -> backslash + n
        result = asyncio.run(project_tools["project_export_audio"].fn(path=path))
        assert any("newline" in w for w in result.get("warnings", []))
