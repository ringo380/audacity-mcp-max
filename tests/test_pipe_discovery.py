"""Locating the script FIFOs when they are not under the host's /tmp (issue #7).

A Snap-packaged Audacity — what `apt`-less Ubuntu installs by default — runs in
its own mount namespace, so /tmp/audacity_script_pipe.* never appears on the
host and the whole server looks like "Audacity is not running". The files are
reachable at /proc/<pid>/root/tmp instead.

None of this can run on the developer's machine (there is no /proc on macOS, and
a real Snap is needed to produce the layout), so the discovery walk is tested
against a fake /proc tree and the caller against a stubbed walk.
"""

import os

import pytest

from audacity_mcp_shared import constants
from audacity_mcp_shared.constants import PipePaths, _pipe_names, discover_snap_pipe_dir

UID = 4242
TO_NAME = f"audacity_script_pipe.to.{UID}"
FROM_NAME = f"audacity_script_pipe.from.{UID}"


def fake_proc(root, pid, comm, pipes=(TO_NAME, FROM_NAME)):
    """One process in a stand-in /proc, with `pipes` in its private /tmp."""
    proc_dir = root / str(pid)
    (proc_dir / "root" / "tmp").mkdir(parents=True)
    (proc_dir / "comm").write_text(comm + "\n")  # the real file is newline-terminated
    for name in pipes:
        (proc_dir / "root" / "tmp" / name).write_text("")
    return str(proc_dir / "root" / "tmp")


class TestDiscoverSnapPipeDir:
    def test_finds_the_private_tmp_of_a_confined_audacity(self, tmp_path):
        expected = fake_proc(tmp_path, 1234, "audacity")
        assert discover_snap_pipe_dir(str(tmp_path), UID) == expected

    def test_ignores_processes_that_are_not_audacity(self, tmp_path):
        fake_proc(tmp_path, 1234, "firefox")
        assert discover_snap_pipe_dir(str(tmp_path), UID) is None

    def test_requires_both_pipes_before_believing_a_process(self, tmp_path):
        # A confined Audacity with mod-script-pipe disabled has the namespace but
        # no FIFOs. Accepting it would redirect every open at an empty directory,
        # which reads as a protocol failure rather than a disabled module.
        fake_proc(tmp_path, 1234, "audacity", pipes=(TO_NAME,))
        assert discover_snap_pipe_dir(str(tmp_path), UID) is None

    def test_matches_only_this_uid(self, tmp_path):
        fake_proc(tmp_path, 1234, "audacity")
        assert discover_snap_pipe_dir(str(tmp_path), UID + 1) is None

    def test_survives_entries_that_are_not_readable_processes(self, tmp_path):
        (tmp_path / "self").mkdir()          # not a pid, and has no comm here
        (tmp_path / "9999").mkdir()          # a pid whose comm is unreadable/gone
        (tmp_path / "cmdline").write_text("")  # a plain file where a dir is expected
        expected = fake_proc(tmp_path, 1234, "audacity")
        assert discover_snap_pipe_dir(str(tmp_path), UID) == expected

    def test_returns_none_when_there_is_no_proc_at_all(self, tmp_path):
        assert discover_snap_pipe_dir(str(tmp_path / "nope"), UID) is None


@pytest.fixture
def on_linux(monkeypatch, tmp_path):
    """A Linux server whose default (non-Snap) pipe paths do not exist."""
    monkeypatch.setattr(constants.sys, "platform", "linux")
    monkeypatch.delenv("AUDACITY_PIPE_DIR", raising=False)
    defaults = (str(tmp_path / "default_to"), str(tmp_path / "default_from"))
    monkeypatch.setattr(PipePaths, "_DEFAULTS", defaults)
    monkeypatch.setattr(PipePaths, "TO_SRV", defaults[0])
    monkeypatch.setattr(PipePaths, "FROM_SRV", defaults[1])
    monkeypatch.setattr(PipePaths, "_discovered", None)
    monkeypatch.setattr(os, "getuid", lambda: UID)
    return defaults


def stub_discovery(monkeypatch, result):
    calls = []

    def fake(*args, **kwargs):
        calls.append(True)
        return result() if callable(result) else result

    monkeypatch.setattr(constants, "discover_snap_pipe_dir", fake)
    return calls


class TestRediscover:
    def test_repoints_at_the_snap_pipes_when_the_plain_ones_are_missing(
        self, on_linux, monkeypatch
    ):
        stub_discovery(monkeypatch, "/proc/1234/root/tmp")
        PipePaths.rediscover()
        assert PipePaths.TO_SRV == f"/proc/1234/root/tmp/{TO_NAME}"
        assert PipePaths.FROM_SRV == f"/proc/1234/root/tmp/{FROM_NAME}"

    def test_leaves_the_defaults_alone_when_no_snap_is_found(self, on_linux, monkeypatch):
        stub_discovery(monkeypatch, None)
        PipePaths.rediscover()
        assert (PipePaths.TO_SRV, PipePaths.FROM_SRV) == on_linux

    def test_reruns_after_an_audacity_restart_changes_the_pid(self, on_linux, monkeypatch):
        stub_discovery(monkeypatch, "/proc/1234/root/tmp")
        PipePaths.rediscover()
        stub_discovery(monkeypatch, "/proc/5678/root/tmp")
        PipePaths.rediscover()
        assert PipePaths.TO_SRV == f"/proc/5678/root/tmp/{TO_NAME}"

    def test_falls_back_to_the_defaults_when_the_discovered_pid_is_gone(
        self, on_linux, monkeypatch
    ):
        stub_discovery(monkeypatch, "/proc/1234/root/tmp")
        PipePaths.rediscover()
        stub_discovery(monkeypatch, None)
        PipePaths.rediscover()
        assert (PipePaths.TO_SRV, PipePaths.FROM_SRV) == on_linux

    def test_does_not_scan_when_the_plain_pipes_are_present(self, on_linux, monkeypatch):
        for path in on_linux:
            open(path, "w").close()
        calls = stub_discovery(monkeypatch, "/proc/1234/root/tmp")
        PipePaths.rediscover()
        assert calls == []
        assert (PipePaths.TO_SRV, PipePaths.FROM_SRV) == on_linux

    def test_an_explicit_pipe_dir_wins_outright(self, on_linux, monkeypatch):
        monkeypatch.setenv("AUDACITY_PIPE_DIR", "/somewhere/else")
        calls = stub_discovery(monkeypatch, "/proc/1234/root/tmp")
        PipePaths.rediscover()
        assert calls == []
        assert (PipePaths.TO_SRV, PipePaths.FROM_SRV) == on_linux

    def test_does_nothing_off_linux(self, on_linux, monkeypatch):
        monkeypatch.setattr(constants.sys, "platform", "darwin")
        calls = stub_discovery(monkeypatch, "/proc/1234/root/tmp")
        PipePaths.rediscover()
        assert calls == []
        assert (PipePaths.TO_SRV, PipePaths.FROM_SRV) == on_linux

    def test_never_touches_paths_it_did_not_set(self, on_linux, monkeypatch):
        # This is what keeps discovery out of the test suite's isolated FIFOs
        # (conftest points them at a temp dir) and out of the way of anyone who
        # sets the paths by hand at runtime.
        monkeypatch.setattr(PipePaths, "TO_SRV", "/tmp/mine.to")
        monkeypatch.setattr(PipePaths, "FROM_SRV", "/tmp/mine.from")
        calls = stub_discovery(monkeypatch, "/proc/1234/root/tmp")
        PipePaths.rediscover()
        assert calls == []
        assert (PipePaths.TO_SRV, PipePaths.FROM_SRV) == ("/tmp/mine.to", "/tmp/mine.from")


class TestCallers:
    """rediscover() is a no-op off Linux, so nothing else in the suite can show
    that the two places that need it actually call it."""

    def test_the_posix_open_path_rediscovers_first(self, monkeypatch):
        from audacity_mcp.audacity_client import AudacityClient

        calls = []
        monkeypatch.setattr(PipePaths, "rediscover", classmethod(lambda cls: calls.append(True)))
        with pytest.raises(OSError):  # the isolated paths do not exist
            AudacityClient()._posix_open_pipes()
        assert calls == [True]

    def test_the_health_check_rediscovers_before_reporting_the_pipes(
        self, monkeypatch, mock_client
    ):
        import asyncio

        from tests.conftest import register_tools

        calls = []
        monkeypatch.setattr(PipePaths, "rediscover", classmethod(lambda cls: calls.append(True)))
        tools = register_tools("project_tools", mock_client)
        asyncio.run(tools["audacity_health_check"].fn())
        assert calls == [True]


class TestEnvironmentOverride:
    """The directory is resolved once at import, from _resolve_pipe_dir().

    Tested through that function rather than by reloading the module: a reload
    rebinds PipePaths to a new class object, which the autouse isolation fixture
    is no longer patching, and the rest of the suite then reaches the real FIFOs.
    """

    def test_pipe_dir_env_var_replaces_tmp(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUDACITY_PIPE_DIR", str(tmp_path))
        assert constants._resolve_pipe_dir() == str(tmp_path)

    def test_defaults_to_tmp_when_unset_or_empty(self, monkeypatch):
        monkeypatch.delenv("AUDACITY_PIPE_DIR", raising=False)
        assert constants._resolve_pipe_dir() == "/tmp"
        monkeypatch.setenv("AUDACITY_PIPE_DIR", "")
        assert constants._resolve_pipe_dir() == "/tmp"

    def test_the_pipe_names_carry_the_uid_audacity_uses(self):
        assert _pipe_names(7) == (
            "audacity_script_pipe.to.7",
            "audacity_script_pipe.from.7",
        )
