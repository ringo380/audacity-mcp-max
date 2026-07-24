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
