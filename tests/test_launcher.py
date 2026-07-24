"""The launcher, executed rather than linted.

An MCP host does not necessarily start a server with the user's login PATH, and
uv installs to ~/.local/bin by default, so a bare `command -v uv` check reports
"not found" for a uv that is sitting right there. And whatever the launcher says
when it gives up has to go to stderr: stdout is the JSON-RPC channel, and an
English sentence written there corrupts the protocol rather than explaining
anything.
"""
import pathlib
import subprocess
import stat

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


def run_launcher(env, tmp_path, args=()):
    return subprocess.run(
        [str(LAUNCHER), *args],
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

    def test_passes_its_own_arguments_through_to_the_server(self, tmp_path):
        # Without this, dropping "$@" from the exec line breaks no test: the
        # other five all invoke the launcher bare, so a launcher that silently
        # swallowed every argument would look perfectly healthy.
        record = tmp_path / "argv.txt"
        bindir = tmp_path / "bin"
        make_fake_uv(bindir, record)
        env = base_env(tmp_path, REPO)
        env["UV_BIN"] = str(bindir / "uv")

        proc = run_launcher(env, tmp_path, args=["--transcription-check", "extra arg"])

        assert proc.returncode == 0, proc.stderr
        argv = record.read_text().split("\n")
        assert argv[:6] == [
            "run",
            "--directory",
            str(REPO),
            "audacity-mcp-max",
            "--transcription-check",
            # A quoted argument containing a space must arrive as one argument,
            # not two - "$@" rather than $@.
            "extra arg",
        ]

    def test_resolves_plugin_root_through_a_symlink_to_the_launcher(self, tmp_path):
        # A naive `dirname "$0"` reports the SYMLINK's own directory, not the
        # directory the launcher actually lives in - so a symlinked launcher
        # would tell uv to run against wherever the symlink happens to sit
        # rather than the real plugin checkout.
        record = tmp_path / "argv.txt"
        bindir = tmp_path / "bin"
        make_fake_uv(bindir, record)

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        linked_launcher = elsewhere / "launch-mcp.sh"
        linked_launcher.symlink_to(LAUNCHER)

        env = base_env(tmp_path, REPO)
        env["UV_BIN"] = str(bindir / "uv")
        del env["CLAUDE_PLUGIN_ROOT"]

        proc = subprocess.run(
            [str(linked_launcher)],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            timeout=30,
        )

        assert proc.returncode == 0, proc.stderr
        argv = record.read_text().split("\n")
        # The real plugin root (REPO), not `elsewhere` where the symlink lives.
        assert argv[:3] == ["run", "--directory", str(REPO)]

    def test_refuses_to_run_uv_with_an_empty_directory(self, tmp_path):
        # If resolving the launcher's own location ever comes out empty (its
        # cd/pwd fallback fails - here because $0's directory does not exist
        # at all), the launcher must refuse loudly rather than exec `uv run
        # --directory "" ...`, which silently runs uv against the caller's cwd
        # instead of the plugin root.
        record = tmp_path / "argv.txt"
        bindir = tmp_path / "bin"
        make_fake_uv(bindir, record)

        script = LAUNCHER.read_text()
        env = base_env(tmp_path, REPO)
        env["UV_BIN"] = str(bindir / "uv")
        del env["CLAUDE_PLUGIN_ROOT"]

        # `sh -c SCRIPT arg0 ...` runs the launcher's real content unmodified
        # with $0 set to whatever string we like - no filesystem path needs to
        # actually exist for a `-c` script, so this cleanly exercises the
        # fallback's failure path without needing the OS to execute anything
        # at that bogus location.
        fake_argv0 = str(tmp_path / "this" / "directory" / "does-not-exist" / "launch-mcp.sh")

        proc = subprocess.run(
            ["sh", "-c", script, fake_argv0],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            timeout=30,
        )

        assert proc.returncode != 0
        assert proc.stdout == ""
        assert proc.stderr.strip() != ""
        assert not record.exists(), "must not have run uv at all"
