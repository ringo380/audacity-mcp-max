"""The bootstrap that gets the plugin's scripts onto a usable interpreter.

Stock macOS ships /usr/bin/python3 as 3.9.6, and both `commands/setup.md` and
`commands/doctor.md` invoke the scripts as bare `python3`. Before this existed,
Step 2 of `/audacity:setup` died with a TypeError out of `constants.py` and the
doctor reported a healthy install as broken.

Almost everything here drives `reexec_if_old` directly with an injected version
and an injected exec, because a portable test cannot count on an interpreter
older than 3.10 being installed. The one test that does use a real old
interpreter skips when the machine has none - it is a bonus on top of this
coverage, not a substitute for it.
"""
import os
import pathlib
import shutil
import stat
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import plugin_bootstrap  # noqa: E402

OLD = (3, 9, 6)
NEW = (3, 12, 1)


class Exec:
    """Stand-in for os.execv, which would otherwise replace the test runner."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, path, argv):
        self.calls.append((path, argv))
        if self.error:
            raise self.error


def make_executable(path, body="#!/bin/sh\nexit 0\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture(autouse=True)
def no_inherited_guard(monkeypatch):
    monkeypatch.delenv(plugin_bootstrap.GUARD_ENV, raising=False)


class TestReexecIfOld:
    def test_a_modern_interpreter_is_left_alone(self, tmp_path):
        exec_ = Exec()
        assert plugin_bootstrap.reexec_if_old("s.py", tmp_path, version=NEW, exec_=exec_) is None
        assert exec_.calls == []

    def test_prefers_the_plugins_own_venv(self, tmp_path, monkeypatch):
        venv = make_executable(tmp_path / ".venv" / "bin" / "python")
        monkeypatch.setattr(sys, "argv", ["audacity_setup.py", "--enable-module"])
        exec_ = Exec()

        plugin_bootstrap.reexec_if_old(
            tmp_path / "audacity_setup.py", tmp_path, uv="/nope/uv", version=OLD, exec_=exec_
        )

        assert exec_.calls == [
            (
                str(venv),
                [str(venv), str(tmp_path / "audacity_setup.py"), "--enable-module"],
            )
        ]

    def test_finds_the_windows_venv_layout(self, tmp_path):
        venv = make_executable(tmp_path / ".venv" / "Scripts" / "python.exe")
        assert plugin_bootstrap.venv_python(tmp_path) == str(venv)

    def test_falls_back_to_uv_when_there_is_no_venv(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["plugin_doctor.py"])
        exec_ = Exec()

        plugin_bootstrap.reexec_if_old(
            tmp_path / "plugin_doctor.py", tmp_path, uv="/bin/uv", version=OLD, exec_=exec_
        )

        assert exec_.calls == [
            (
                "/bin/uv",
                [
                    "/bin/uv",
                    "run",
                    "--directory",
                    str(tmp_path),
                    "python",
                    str(tmp_path / "plugin_doctor.py"),
                ],
            )
        ]

    def test_degrades_readably_when_there_is_nothing_better(self, tmp_path, monkeypatch):
        monkeypatch.setattr(plugin_bootstrap, "find_uv", lambda: None)
        exec_ = Exec()

        note = plugin_bootstrap.reexec_if_old(
            "s.py", tmp_path, version=OLD, exec_=exec_
        )

        assert exec_.calls == []
        # The version it found has to be in the line: "too old" without a
        # number leaves the user guessing which python3 ran.
        assert "3.9.6" in note
        # And it must not read as a broken install. The doctor's old wording
        # sent people to reinstall the plugin over an old interpreter.
        assert "not a broken install" in note

    def test_does_not_exec_the_interpreter_already_running(self, tmp_path, monkeypatch):
        """A .venv/bin/python that resolves to the running interpreter is not an
        upgrade, and execing it would loop forever."""
        venv = tmp_path / ".venv" / "bin" / "python"
        venv.parent.mkdir(parents=True)
        os.symlink(sys.executable, str(venv))
        monkeypatch.setattr(plugin_bootstrap, "find_uv", lambda: None)
        exec_ = Exec()

        note = plugin_bootstrap.reexec_if_old("s.py", tmp_path, version=OLD, exec_=exec_)

        assert exec_.calls == []
        assert "3.9.6" in note

    def test_a_second_generation_process_does_not_exec_again(self, tmp_path, monkeypatch):
        """The venv's own python being too old would otherwise fork bomb."""
        make_executable(tmp_path / ".venv" / "bin" / "python")
        monkeypatch.setenv(plugin_bootstrap.GUARD_ENV, "1")
        exec_ = Exec()

        note = plugin_bootstrap.reexec_if_old("s.py", tmp_path, version=OLD, exec_=exec_)

        assert exec_.calls == []
        assert "not a broken install" in note

    def test_the_guard_is_set_for_the_child(self, tmp_path, monkeypatch):
        make_executable(tmp_path / ".venv" / "bin" / "python")
        monkeypatch.setattr(sys, "argv", ["s.py"])
        plugin_bootstrap.reexec_if_old("s.py", tmp_path, version=OLD, exec_=Exec())
        assert os.environ.get(plugin_bootstrap.GUARD_ENV) == "1"

    def test_a_failed_exec_degrades_instead_of_raising(self, tmp_path, monkeypatch):
        make_executable(tmp_path / ".venv" / "bin" / "python")
        monkeypatch.setattr(sys, "argv", ["s.py"])
        exec_ = Exec(error=OSError("Exec format error"))

        note = plugin_bootstrap.reexec_if_old("s.py", tmp_path, version=OLD, exec_=exec_)

        assert "Exec format error" in note
        # The guard has to come back off, or a later attempt in the same
        # process would refuse for the wrong reason.
        assert plugin_bootstrap.GUARD_ENV not in os.environ


class TestTranscriptionState:
    def test_degraded_says_unknown_rather_than_not_installed(self, tmp_path):
        state, _ = plugin_bootstrap.transcription_state(tmp_path, degraded="old python")
        assert state == "unknown"

    def test_probes_the_venv_rather_than_the_running_interpreter(self, tmp_path):
        """The extra is installed into the plugin's .venv. Answering from this
        process's own imports told a user who had just run
        `uv sync --extra transcription` that they had not."""
        make_executable(tmp_path / ".venv" / "bin" / "python", "#!/bin/sh\nexit 0\n")
        assert plugin_bootstrap.transcription_state(tmp_path)[0] == "installed"

    def test_a_venv_without_the_extra_reads_as_missing(self, tmp_path):
        make_executable(tmp_path / ".venv" / "bin" / "python", "#!/bin/sh\nexit 1\n")
        assert plugin_bootstrap.transcription_state(tmp_path)[0] == "missing"

    def test_an_unrunnable_venv_python_is_unknown_not_missing(self, tmp_path, monkeypatch):
        make_executable(tmp_path / ".venv" / "bin" / "python")

        def boom(*a, **k):
            raise OSError("no such file")

        monkeypatch.setattr(plugin_bootstrap.subprocess, "run", boom)
        assert plugin_bootstrap.transcription_state(tmp_path)[0] == "unknown"

    def test_a_broken_native_dependency_is_unknown_not_a_crash(self, tmp_path, monkeypatch):
        """ctranslate2/onnxruntime failing its dlopen raises OSError, not
        ImportError. That used to escape the doctor's catch and end the report
        before the pipe section, breaking its always-exit-0 contract."""
        import builtins

        real_import = builtins.__import__

        def hostile(name, *args, **kwargs):
            if name == "faster_whisper":
                raise OSError("dlopen(libctranslate2): image not found")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", hostile)
        state, detail = plugin_bootstrap.transcription_state(tmp_path)

        assert state == "unknown"
        assert "OSError" in detail


def older_python():
    """Any interpreter on this machine older than the project's floor."""
    for name in ("/usr/bin/python3", "python3.9", "python3.8", "python3.7"):
        path = name if os.path.isabs(name) else shutil.which(name)
        if not path or not os.access(path, os.X_OK):
            continue
        try:
            out = subprocess.run(
                [path, "-c", "import sys; print('%d %d' % sys.version_info[:2])"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode != 0:
            continue
        try:
            major, minor = (int(p) for p in out.stdout.split())
        except ValueError:
            continue
        if (major, minor) < plugin_bootstrap.MIN_PYTHON:
            return path
    return None


class TestUnderARealOldInterpreter:
    """The regression proper: run the shipped scripts the way the shipped
    commands do. `commands/doctor.md` and `commands/setup.md` both invoke bare
    `python3`, which on stock macOS is 3.9.6 - the case the rest of this file
    can only simulate. Skips where no such interpreter exists, which is why the
    injected-version tests above exist and do not skip.
    """

    @pytest.fixture
    def old(self):
        path = older_python()
        if not path:
            pytest.skip("no interpreter older than 3.10 on this machine")
        return path

    def run(self, python, script, env=None):
        environ = dict(os.environ)
        # Force the degraded path, which is the one that has to stay well
        # behaved; the re-exec path is covered above with an injected exec.
        # The guard is what does it, rather than hiding uv: a developer who has
        # run the server once has a .venv in the tree, and hiding uv would then
        # send this into a re-exec and quietly stop testing anything.
        environ[plugin_bootstrap.GUARD_ENV] = "1"
        environ["PATH"] = "/usr/bin:/bin"
        environ["AUDACITY_MCP_UV_SEARCH"] = str(REPO / "no" / "such" / "uv")
        environ.pop("UV_BIN", None)
        environ.update(env or {})
        return subprocess.run(
            [python, str(REPO / "scripts" / script)],
            capture_output=True,
            text=True,
            cwd=str(REPO),
            env=environ,
            timeout=120,
        )

    def test_the_doctor_still_exits_zero_and_says_why(self, old):
        proc = self.run(old, "plugin_doctor.py")
        assert proc.returncode == 0, proc.stderr
        assert "plugin version:" in proc.stdout
        assert "not a broken install" in proc.stdout
        assert "Traceback" not in proc.stderr

    def test_the_doctor_does_not_claim_the_extra_is_missing(self, old):
        proc = self.run(old, "plugin_doctor.py")
        assert "transcription extra: unknown" in proc.stdout

    def test_setup_exits_nonzero_with_one_line_and_no_traceback(self, old):
        proc = self.run(old, "audacity_setup.py")
        assert proc.returncode != 0
        assert "Traceback" not in proc.stderr
        assert "not a broken install" in proc.stderr
        assert len(proc.stderr.strip().splitlines()) == 1
