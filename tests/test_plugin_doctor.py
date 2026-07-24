"""The plugin-side half of the diagnostic.

audacity_health_check answers "can I reach Audacity". This answers "is the
plugin itself assembled correctly", which the server cannot: it is running
inside the environment it would be reporting on.
"""
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
DOCTOR = REPO / "scripts" / "plugin_doctor.py"
LAUNCHER = REPO / "scripts" / "launch-mcp.sh"

sys.path.insert(0, str(REPO))

from tests.test_launcher import base_env, make_fake_uv  # noqa: E402


def run_doctor(env=None):
    return subprocess.run(
        [sys.executable, str(DOCTOR)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=env,
        timeout=30,
    )


def run_launcher(env, tmp_path, args=()):
    return subprocess.run(
        [str(LAUNCHER), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=30,
    )


def test_reports_the_plugin_version_and_exits_zero():
    proc = run_doctor()
    assert proc.returncode == 0, proc.stderr
    assert "plugin version: 0.3.0" in proc.stdout


def test_reports_uv_as_missing_without_blowing_up(tmp_path):
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:/bin"
    env["HOME"] = str(tmp_path)
    env["AUDACITY_MCP_UV_SEARCH"] = str(tmp_path / "nowhere" / "uv")
    env.pop("UV_BIN", None)

    proc = run_doctor(env)

    assert proc.returncode == 0, proc.stderr
    assert "uv: not found" in proc.stdout
    # A missing uv is the single most likely reason the server never started,
    # so the report has to say what to do rather than just noting the absence.
    assert "astral.sh/uv" in proc.stdout


def test_reports_whether_the_transcription_extra_is_present():
    proc = run_doctor()
    assert proc.returncode == 0, proc.stderr
    assert "transcription extra:" in proc.stdout


class TestFindUvAgreesWithTheLauncher:
    """plugin_doctor.find_uv() deliberately re-implements launch-mcp.sh's
    resolution order in Python, because the launcher cannot shell out to
    Python to find the thing that starts Python. That duplication was
    accepted on the condition that a test catches the two drifting apart -
    this is that test.

    The launcher side runs as a subprocess (it is a shell script, there is
    nothing else to do); the doctor side is imported directly, since
    find_uv() is a plain function and importing it gives a clean return-value
    assertion instead of scraping a line out of stdout.
    """

    def test_agree_on_a_found_uv(self, tmp_path):
        record = tmp_path / "argv.txt"
        bindir = tmp_path / "home" / ".local" / "bin"
        fake = make_fake_uv(bindir, record)
        env = base_env(tmp_path, REPO)
        env["AUDACITY_MCP_UV_SEARCH"] = str(fake)

        proc = run_launcher(env, tmp_path)
        assert proc.returncode == 0, proc.stderr

        old_environ = dict(os.environ)
        os.environ.clear()
        os.environ.update(env)
        try:
            sys.path.insert(0, str(REPO / "scripts"))
            import plugin_doctor
            import importlib

            importlib.reload(plugin_doctor)
            found = plugin_doctor.find_uv()
        finally:
            os.environ.clear()
            os.environ.update(old_environ)

        assert found == str(fake)

    def test_agree_on_a_missing_uv(self, tmp_path):
        env = base_env(tmp_path, REPO)

        proc = run_launcher(env, tmp_path)
        assert proc.returncode == 127

        old_environ = dict(os.environ)
        os.environ.clear()
        os.environ.update(env)
        try:
            sys.path.insert(0, str(REPO / "scripts"))
            import plugin_doctor
            import importlib

            importlib.reload(plugin_doctor)
            found = plugin_doctor.find_uv()
        finally:
            os.environ.clear()
            os.environ.update(old_environ)

        assert found is None

    def test_agree_that_uv_bin_wins(self, tmp_path):
        chosen_record = tmp_path / "argv-chosen.txt"
        ignored_record = tmp_path / "argv-ignored.txt"
        chosen = make_fake_uv(tmp_path / "chosen", chosen_record)
        ignored = make_fake_uv(tmp_path / "ignored", ignored_record)
        env = base_env(tmp_path, REPO)
        env["UV_BIN"] = str(chosen)
        env["AUDACITY_MCP_UV_SEARCH"] = str(ignored)

        proc = run_launcher(env, tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert chosen_record.exists()
        assert not ignored_record.exists()

        old_environ = dict(os.environ)
        os.environ.clear()
        os.environ.update(env)
        try:
            sys.path.insert(0, str(REPO / "scripts"))
            import plugin_doctor
            import importlib

            importlib.reload(plugin_doctor)
            found = plugin_doctor.find_uv()
        finally:
            os.environ.clear()
            os.environ.update(old_environ)

        assert found == str(chosen)
