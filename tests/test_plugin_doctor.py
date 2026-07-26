"""The plugin-side half of the diagnostic.

audacity_health_check answers "can I reach Audacity". This answers "is the
plugin itself assembled correctly", which the server cannot: it is running
inside the environment it would be reporting on.
"""
import builtins
import contextlib
import json
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


def manifest_version():
    """Read the version rather than hardcoding it.

    A literal here fails on the next release for no useful reason. Parity
    between the manifest and pyproject is guarded in test_plugin_manifests.py,
    so the only thing worth asserting here is that the doctor reports whatever
    the manifest says.
    """
    return json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())["version"]


def test_reports_the_plugin_version_and_exits_zero():
    proc = run_doctor()
    assert proc.returncode == 0, proc.stderr
    assert "plugin version: %s" % manifest_version() in proc.stdout


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


class TestUnderAnInterpreterTooOldToImportTheProject:
    """run_doctor() above launches sys.executable - the modern interpreter
    pytest is running under - while commands/doctor.md invokes bare python3.
    That gap is why a doctor that could not import audacity_mcp_shared at all
    on stock macOS passed every test in this file.

    These drive the degraded branch in-process by making the bootstrap report
    that it could not upgrade. tests/test_plugin_bootstrap.py covers the
    bootstrap itself, including against a real old interpreter where the
    machine has one.
    """

    NOTE = "python: 3.9.6 is too old - not a broken install"

    def degraded(self, monkeypatch):
        monkeypatch.setattr(plugin_doctor, "reexec_if_old", lambda *a, **k: self.NOTE)

    def test_it_still_exits_zero_and_reports_what_it_can(self, capsys, monkeypatch):
        self.degraded(monkeypatch)

        rc = plugin_doctor.main()

        out = capsys.readouterr().out
        assert rc == 0
        assert self.NOTE in out
        assert "plugin version:" in out
        assert "uv:" in out

    def test_it_does_not_assert_the_transcription_extra_is_missing(self, capsys, monkeypatch):
        """The extra lives in the plugin's .venv, and a degraded run cannot
        reach it. "not installed" would be a guess presented as a fact."""
        self.degraded(monkeypatch)

        plugin_doctor.main()

        assert "transcription extra: unknown" in capsys.readouterr().out

    def test_the_pipe_line_points_at_the_python_note(self, capsys, monkeypatch):
        """commands/doctor.md keys its guidance on this prefix, so it has to
        keep it - but it must not send the user to reinstall the plugin."""
        self.degraded(monkeypatch)

        plugin_doctor.main()

        out = capsys.readouterr().out
        assert "pipe and config info: unavailable" in out
        assert "python line above" in out


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


class TestMeasurementExtra:
    def test_report_names_the_measurement_extra(self, capsys):
        """A user whose pipelines report 'lufs: null' needs the doctor to say
        why, in the same place it explains every other missing piece."""
        plugin_doctor.main()
        out = capsys.readouterr().out
        assert "measurement:" in out

    def test_states_installed_or_not_installed_or_unknown(self, capsys):
        plugin_doctor.main()
        line = [l for l in capsys.readouterr().out.splitlines() if "measurement:" in l][0]
        assert any(s in line for s in ("installed", "not installed", "unknown"))
