"""The launcher, executed rather than linted.

An MCP host does not necessarily start a server with the user's login PATH, and
uv installs to ~/.local/bin by default, so a bare `command -v uv` check reports
"not found" for a uv that is sitting right there. And whatever the launcher says
when it gives up has to go to stderr: stdout is the JSON-RPC channel, and an
English sentence written there corrupts the protocol rather than explaining
anything.
"""
import os
import pathlib
import subprocess
import stat

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "scripts" / "launch-mcp.sh"


def make_fake_uv(directory, record):
    """A stand-in uv that records how it was called and exits 0."""
    directory.mkdir(parents=True, exist_ok=True)
    fake = directory / "uv"
    fake.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{record}"\n'
        "exit 0\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def run_launcher(env, tmp_path):
    return subprocess.run(
        [str(LAUNCHER)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=30,
    )


def base_env(tmp_path, plugin_root):
    """A deliberately hostile environment: nothing useful on PATH."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        # Every absolute fallback points inside the temp tree, so the test does
        # not depend on whether this machine happens to have uv installed.
        "AUDACITY_MCP_UV_SEARCH": str(tmp_path / "nowhere" / "uv"),
    }


class TestLauncher:
    def test_execs_uv_run_against_the_plugin_root(self, tmp_path):
        record = tmp_path / "argv.txt"
        bindir = tmp_path / "bin"
        make_fake_uv(bindir, record)
        env = base_env(tmp_path, REPO)
        env["UV_BIN"] = str(bindir / "uv")

        proc = run_launcher(env, tmp_path)

        assert proc.returncode == 0, proc.stderr
        assert record.read_text().split("\n")[:4] == [
            "run",
            "--directory",
            str(REPO),
            "audacity-mcp-max",
        ]

    def test_finds_uv_in_the_search_list_when_it_is_not_on_path(self, tmp_path):
        record = tmp_path / "argv.txt"
        bindir = tmp_path / "home" / ".local" / "bin"
        make_fake_uv(bindir, record)
        env = base_env(tmp_path, REPO)
        env["AUDACITY_MCP_UV_SEARCH"] = str(bindir / "uv")

        proc = run_launcher(env, tmp_path)

        assert proc.returncode == 0, proc.stderr
        assert "audacity-mcp-max" in record.read_text()

    def test_prefers_uv_bin_over_everything_else(self, tmp_path):
        chosen = tmp_path / "argv-chosen.txt"
        ignored = tmp_path / "argv-ignored.txt"
        make_fake_uv(tmp_path / "chosen", chosen)
        make_fake_uv(tmp_path / "ignored", ignored)
        env = base_env(tmp_path, REPO)
        env["UV_BIN"] = str(tmp_path / "chosen" / "uv")
        env["AUDACITY_MCP_UV_SEARCH"] = str(tmp_path / "ignored" / "uv")

        proc = run_launcher(env, tmp_path)

        assert proc.returncode == 0, proc.stderr
        assert chosen.exists()
        assert not ignored.exists()

    def test_missing_uv_exits_127_and_says_nothing_on_stdout(self, tmp_path):
        env = base_env(tmp_path, REPO)

        proc = run_launcher(env, tmp_path)

        assert proc.returncode == 127
        assert proc.stdout == ""
        assert "uv" in proc.stderr
        assert "astral.sh/uv" in proc.stderr

    def test_falls_back_to_its_own_location_without_claude_plugin_root(self, tmp_path):
        record = tmp_path / "argv.txt"
        bindir = tmp_path / "bin"
        make_fake_uv(bindir, record)
        env = base_env(tmp_path, REPO)
        env["UV_BIN"] = str(bindir / "uv")
        del env["CLAUDE_PLUGIN_ROOT"]

        proc = run_launcher(env, tmp_path)

        assert proc.returncode == 0, proc.stderr
        # Resolved from the script's own path, not from the cwd the host chose.
        assert str(REPO) in record.read_text()
