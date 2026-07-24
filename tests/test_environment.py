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

    def test_a_posix_comm_containing_spaces_is_not_truncated(self):
        # `ps -Ao comm=` prints the whole executable path on macOS, and an app
        # bundle may have a space in its name. Taking the first field the way
        # the Windows branch has to would leave "/Applications/My" and miss a
        # running Audacity - which is the failure this whole probe prevents.
        listing = "/Applications/My Audacity.app/Contents/MacOS/Audacity\n"
        assert env.audacity_is_running(_run=lambda cmd: FakeProc(listing)) is True

    def test_our_own_server_does_not_count_as_audacity(self):
        # The MCP server's own process is named audacity-mcp-max. Matching on a
        # substring would make setup refuse to write its config forever.
        listing = "audacity-mcp-max\n/bin/zsh\n"
        assert env.audacity_is_running(_run=lambda cmd: FakeProc(listing)) is False

    def test_none_when_the_probe_itself_fails(self):
        def boom(cmd):
            raise OSError("no ps here")

        assert env.audacity_is_running(_run=boom) is None

    def test_none_when_the_probe_runs_but_fails(self):
        # busybox ps rejects -Ao comm=, a locked-down container refuses the
        # process table: the command exists, exits non-zero, and says nothing.
        # Reading that as "not running" lets setup write a config Audacity
        # reverts on quit - the callers treat None as a refusal instead.
        proc = FakeProc(stdout="", returncode=1)
        assert env.audacity_is_running(_run=lambda cmd: proc) is None

    def test_a_nonzero_exit_that_still_answered_is_believed(self):
        # tasklist's filter exits non-zero on some Windows builds while still
        # printing the matching row. Discarding output we actually have would
        # turn a plain "yes" into "could not tell".
        listing = "audacity.exe                  6244 Console                    1     92,116 K\n"
        proc = FakeProc(stdout=listing, returncode=1)
        assert env.audacity_is_running(_run=lambda cmd: proc, platform="win32") is True


class TestAudacityIsRunningOnWindows:
    """The Windows branch, which had no test at all.

    tasklist prints a whole row per process, not a bare name, so the POSIX
    basename comparison never matched and the answer was permanently False -
    silently reintroducing the revert-on-quit bug this milestone exists to fix.

    Note what the two negative cases below can and cannot hold. The Windows
    name set is {"audacity.exe"}, and no realistic row contains that string
    without being that process, so neither can catch a substring-instead-of-
    equality mutation - only the POSIX pair can, where the set is {"audacity"}
    and this server's own `audacity-mcp-max` is a substring match. What they do
    hold is that output must be matched rather than counted, which is the
    mutation the /FI filter invites. The first-field rule is held by
    test_true_for_a_real_tasklist_row.
    """

    def probe(self, stdout):
        return env.audacity_is_running(
            _run=lambda cmd: FakeProc(stdout), platform="win32"
        )

    def test_true_for_a_real_tasklist_row(self):
        listing = "audacity.exe                  6244 Console                    1     92,116 K\n"
        assert self.probe(listing) is True

    def test_false_for_the_no_tasks_banner(self):
        # What tasklist prints when the filter matches nothing. It is a
        # sentence, not a row, and must not be mistaken for a process name.
        #
        # The rule this one holds: output has to be *matched*, not counted.
        # /FI filters server-side, so "any non-empty line means Audacity is
        # running" is the tempting simplification - and this banner is the
        # counterexample that makes it wrong. It cannot catch the substring
        # mutation the POSIX pair catches; see the class docstring.
        banner = "INFO: No tasks are running which match the specified criteria.\n"
        assert self.probe(banner) is False

    def test_our_own_server_does_not_count_on_windows_either(self):
        # Same rule as the banner, from the other side: a row that is a real
        # process but not this one. Windows also reaches here with no filtering
        # at all when /FI is unsupported, which is when it matters.
        listing = "audacity-mcp-max.exe          6244 Console                    1     92,116 K\n"
        assert self.probe(listing) is False

    def test_it_asks_tasklist_and_not_ps(self):
        seen = []

        def record(cmd):
            seen.append(cmd)
            return FakeProc("")

        env.audacity_is_running(_run=record, platform="win32")
        assert seen[0][0] == "tasklist"
