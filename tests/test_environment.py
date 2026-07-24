"""Environment detection: where Audacity's config lives, what it says, and
whether Audacity is running.

Everything here is injected. A test that consulted the real config or the real
process table would pass or fail depending on whether the developer happened to
have Audacity open.
"""
import os

import pytest

from audacity_mcp_shared import environment as env


class TestConfigPath:
    def test_darwin_uses_application_support(self, tmp_path):
        cands = env.audacity_cfg_candidates(platform="darwin", home=str(tmp_path))
        assert cands == [
            str(tmp_path / "Library" / "Application Support" / "audacity" / "audacity.cfg")
        ]

    def test_linux_includes_the_snap_location(self, tmp_path):
        cands = env.audacity_cfg_candidates(platform="linux", home=str(tmp_path))
        snap = str(
            tmp_path / "snap" / "audacity" / "current" / ".config" / "audacity" / "audacity.cfg"
        )
        assert snap in cands
        # The plain XDG path is checked first: a machine with both installs
        # should get the one the user is most likely running.
        assert cands.index(str(tmp_path / ".config" / "audacity" / "audacity.cfg")) < cands.index(snap)

    def test_linux_honours_xdg_config_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        cands = env.audacity_cfg_candidates(platform="linux", home=str(tmp_path))
        assert cands[0] == str(tmp_path / "xdg" / "audacity" / "audacity.cfg")

    def test_windows_uses_appdata(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        cands = env.audacity_cfg_candidates(platform="win32", home=str(tmp_path))
        assert cands == [str(tmp_path / "Roaming" / "audacity" / "audacity.cfg")]

    def test_path_returns_the_first_file_that_exists(self, tmp_path):
        snap_dir = tmp_path / "snap" / "audacity" / "current" / ".config" / "audacity"
        snap_dir.mkdir(parents=True)
        (snap_dir / "audacity.cfg").write_text("")
        assert env.audacity_cfg_path(platform="linux", home=str(tmp_path)) == str(
            snap_dir / "audacity.cfg"
        )

    def test_path_is_none_when_nothing_exists(self, tmp_path):
        assert env.audacity_cfg_path(platform="linux", home=str(tmp_path)) is None


class TestModScriptPipeState:
    def write_cfg(self, tmp_path, body):
        cfg = tmp_path / "audacity.cfg"
        cfg.write_text(body)
        return str(cfg)

    @pytest.mark.parametrize(
        "value,expected",
        [("1", "enabled"), ("0", "disabled"), ("2", "ask")],
    )
    def test_reads_each_documented_value(self, tmp_path, value, expected):
        cfg = self.write_cfg(tmp_path, f"[Module]\nmod-script-pipe={value}\n")
        assert env.mod_script_pipe_state(cfg) == expected

    def test_absent_when_the_key_is_missing(self, tmp_path):
        cfg = self.write_cfg(tmp_path, "[Module]\nmod-nyq-workbench=1\n")
        assert env.mod_script_pipe_state(cfg) == "absent"

    def test_no_config_when_there_is_no_file(self):
        assert env.mod_script_pipe_state(None) == "no-config"

    def test_no_config_when_the_file_cannot_be_read(self, tmp_path):
        assert env.mod_script_pipe_state(str(tmp_path / "missing.cfg")) == "no-config"

    def test_an_unrecognised_value_is_not_reported_as_enabled(self, tmp_path):
        # Guessing "enabled" from a value Audacity does not document would send
        # the user hunting for a pipe that is never created.
        cfg = self.write_cfg(tmp_path, "[Module]\nmod-script-pipe=7\n")
        assert env.mod_script_pipe_state(cfg) == "absent"


class TestDefaultProjectSampleRate:
    def test_reads_the_rate(self, tmp_path):
        cfg = tmp_path / "audacity.cfg"
        cfg.write_text("DefaultProjectSampleRate=44100\n")
        assert env.default_project_sample_rate(str(cfg)) == 44100

    def test_none_without_a_config(self):
        assert env.default_project_sample_rate(None) is None

    def test_none_when_the_key_is_absent(self, tmp_path):
        cfg = tmp_path / "audacity.cfg"
        cfg.write_text("Something=1\n")
        assert env.default_project_sample_rate(str(cfg)) is None


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class TestAudacityIsRunning:
    def test_true_when_the_process_table_lists_audacity(self):
        listing = "/Applications/Audacity.app/Contents/MacOS/Audacity\n/bin/zsh\n"
        assert env.audacity_is_running(_run=lambda cmd: FakeProc(listing)) is True

    def test_false_when_it_does_not(self):
        assert env.audacity_is_running(_run=lambda cmd: FakeProc("/bin/zsh\nnode\n")) is False

    def test_our_own_server_does_not_count_as_audacity(self):
        # The MCP server's own process is named audacity-mcp-max. Matching on a
        # substring would make setup refuse to write its config forever.
        listing = "audacity-mcp-max\n/bin/zsh\n"
        assert env.audacity_is_running(_run=lambda cmd: FakeProc(listing)) is False

    def test_none_when_the_probe_itself_fails(self):
        def boom(cmd):
            raise OSError("no ps here")

        assert env.audacity_is_running(_run=boom) is None
