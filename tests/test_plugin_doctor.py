"""The plugin-side half of the diagnostic.

audacity_health_check answers "can I reach Audacity". This answers "is the
plugin itself assembled correctly", which the server cannot: it is running
inside the environment it would be reporting on.
"""
import builtins
import contextlib
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
DOCTOR = REPO / "scripts" / "plugin_doctor.py"
LAUNCHER = REPO / "scripts" / "launch-mcp.sh"

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import plugin_doctor  # noqa: E402

from tests.test_launcher import base_env, make_fake_uv  # noqa: E402


@contextlib.contextmanager
def exactly_this_environment(env):
    """Run the body with `env` as the whole environment, then put the real one back.

    monkeypatch.setenv cannot express this: the point is that nothing else is
    set, so a UV_BIN left over in the developer's shell cannot decide the
    result and make the two resolvers look like they agree when they do not.
    """
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def doctor_find_uv(env):
    """What the doctor resolves under `env`.

    No importlib.reload: find_uv reads the environment when it is called, not
    when the module is imported, so a reload would only add the hazard of
    rebinding a module other tests hold a reference to.
    """
    with exactly_this_environment(env):
        return plugin_doctor.find_uv()


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


def test_pipe_and_config_failure_does_not_break_the_zero_exit_contract(capsys, monkeypatch):
    """A broken install is exactly the situation this diagnostic exists for.

    Simulates audacity_mcp_shared failing to import - a partial checkout, a
    half-merged file, anything that breaks that package - by making the
    import itself raise, then asserts main() still returns 0 and that the
    sections before the pipe/config block (plugin version, uv, venv) still
    printed. A diagnostic that dies halfway through a report is barely better
    than one that exits non-zero.
    """
    real_import = builtins.__import__

    def hostile_import(name, *args, **kwargs):
        if name.startswith("audacity_mcp_shared"):
            raise ImportError("simulated partial install")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", hostile_import)

    rc = plugin_doctor.main()

    captured = capsys.readouterr()
    assert rc == 0
    assert "pipe and config info: unavailable" in captured.out
    assert "plugin version:" in captured.out
    assert "uv:" in captured.out
    assert "venv built:" in captured.out


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

        assert doctor_find_uv(env) == str(fake)

    def test_agree_on_a_missing_uv(self, tmp_path):
        env = base_env(tmp_path, REPO)

        proc = run_launcher(env, tmp_path)
        assert proc.returncode == 127

        assert doctor_find_uv(env) is None

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

        assert doctor_find_uv(env) == str(chosen)

    def test_agree_on_the_hardcoded_default_search_list(self, tmp_path):
        """The three tests above all rely on base_env(), which always sets
        AUDACITY_MCP_UV_SEARCH - so none of them ever exercises either
        resolver's own hardcoded default list. Editing one hardcoded default
        (plugin_doctor.py's or launch-mcp.sh's) without the other still
        passes all three, which is exactly the drift this class exists to
        catch.

        This unsets the override and plants a fake uv at the natural default
        location - $HOME/.local/bin/uv, where uv actually installs - so the
        only way either resolver can succeed is by falling all the way
        through to its own hardcoded list. PATH stays hostile and UV_BIN
        stays unset so nothing else can find it first.
        """
        record = tmp_path / "argv.txt"
        bindir = tmp_path / "home" / ".local" / "bin"
        fake = make_fake_uv(bindir, record)
        env = base_env(tmp_path, REPO)
        del env["AUDACITY_MCP_UV_SEARCH"]

        proc = run_launcher(env, tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert record.exists()

        assert doctor_find_uv(env) == str(fake)
