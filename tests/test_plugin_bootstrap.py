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
    """Stand-in for os.execv, which would otherwise replace the test runner.

    It records the guard variable as it stood *at the moment of the call*. The
    real os.execv never returns, so the environment this process is left
    holding afterwards is a state production never reaches: asserting on it let
    the guard assignment move below the exec - unreachable in production, and a
    fork bomb on a too-old venv python - with the whole suite still green.
    """

    def __init__(self, error=None):
        self.calls = []
        self.guards = []
        self.error = error

    def __call__(self, path, argv):
        self.calls.append((path, argv))
        self.guards.append(os.environ.get(plugin_bootstrap.GUARD_ENV))
        if self.error:
            raise self.error


def make_executable(path, body="#!/bin/sh\nexit 0\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def fake_plugin_root(tmp_path):
    """A copy of the plugin's own scripts, with no .venv and no uv near it.

    The real repo has a .venv as soon as anyone has launched the server once,
    so a test that ran the shipped scripts in place could never reach the uv
    branch - it would take the venv every time and quietly stop testing the
    thing it was written for.
    """
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True)
    for name in ("plugin_bootstrap.py", "plugin_doctor.py"):
        shutil.copy(str(REPO / "scripts" / name), str(root / "scripts" / name))
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text('{"version": "0.0.0-test"}\n')
    return root


@pytest.fixture(autouse=True)
def no_inherited_guard(monkeypatch):
    """Unset the guard for the test, and restore whatever was there after it.

    setenv first so monkeypatch records the pre-test state even when it is
    "absent": production sets this variable itself, and a plain delenv records
    nothing to restore when the variable did not exist yet. That leaked the
    flag into the pytest process, where test_plugin_doctor.py's subprocesses
    (which pass env=None) inherited a silently disarmed bootstrap.
    """
    monkeypatch.setenv(plugin_bootstrap.GUARD_ENV, "")
    monkeypatch.delenv(plugin_bootstrap.GUARD_ENV)


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
        uv = make_executable(tmp_path / "bin" / "uv")
        exec_ = Exec()

        plugin_bootstrap.reexec_if_old(
            tmp_path / "plugin_doctor.py", tmp_path, uv=str(uv), version=OLD, exec_=exec_
        )

        assert exec_.calls == [
            (
                str(uv),
                [
                    str(uv),
                    "run",
                    # Without --frozen, `uv run` may rewrite uv.lock - a tracked
                    # file - so running the doctor would dirty a working tree.
                    "--frozen",
                    "--directory",
                    str(tmp_path),
                    "python",
                    str(tmp_path / "plugin_doctor.py"),
                ],
            )
        ]

    def test_the_script_path_is_absolute(self, tmp_path, monkeypatch):
        """`uv run --directory X` cds into X, so a relative path would be
        opened from somewhere other than where the caller found it."""
        make_executable(tmp_path / ".venv" / "bin" / "python")
        monkeypatch.setattr(sys, "argv", ["plugin_doctor.py"])
        monkeypatch.chdir(tmp_path)
        exec_ = Exec()

        plugin_bootstrap.reexec_if_old("plugin_doctor.py", tmp_path, version=OLD, exec_=exec_)

        passed = exec_.calls[0][1][1]
        assert os.path.isabs(passed)
        assert os.path.basename(passed) == "plugin_doctor.py"

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
        exec_ = Exec()

        plugin_bootstrap.reexec_if_old("s.py", tmp_path, version=OLD, exec_=exec_)

        # What the child inherits at exec time. Checking os.environ after the
        # call instead passes even with the assignment moved below the exec,
        # where production - which never returns from execv - would never run
        # it, and a too-old venv python would re-exec forever.
        assert exec_.guards == ["1"]

    def test_a_failed_exec_degrades_instead_of_raising(self, tmp_path, monkeypatch):
        make_executable(tmp_path / ".venv" / "bin" / "python")
        monkeypatch.setattr(sys, "argv", ["s.py"])
        exec_ = Exec(error=OSError("Exec format error"))

        note = plugin_bootstrap.reexec_if_old("s.py", tmp_path, version=OLD, exec_=exec_)

        assert "Exec format error" in note
        # Set for the child that was about to exist...
        assert exec_.guards == ["1"]
        # ...and back off afterwards, or a later attempt in the same process
        # would refuse for the wrong reason.
        assert plugin_bootstrap.GUARD_ENV not in os.environ


class TestTheCandidateIsProbedBeforeExecing:
    """os.execv is a point of no return.

    Once it succeeds the child's exit code is the doctor's, so an interpreter
    that starts and dies takes the report with it: a .venv whose base
    interpreter was removed or upgraded exits 1 with `dyld: Library not
    loaded: libpython` and nothing else, and a `uv run` that cannot sync exits
    2. Both broke plugin_doctor.py's always-exit-0 contract.
    """

    def test_a_dead_venv_falls_through_to_uv(self, tmp_path, monkeypatch):
        make_executable(
            tmp_path / ".venv" / "bin" / "python",
            "#!/bin/sh\necho 'dyld: Library not loaded: libpython' >&2\nexit 1\n",
        )
        uv = make_executable(tmp_path / "bin" / "uv")
        monkeypatch.setattr(sys, "argv", ["s.py"])
        exec_ = Exec()

        note = plugin_bootstrap.reexec_if_old(
            "s.py", tmp_path, uv=str(uv), version=OLD, exec_=exec_
        )

        assert note is None
        assert [call[0] for call in exec_.calls] == [str(uv)]

    def test_a_dead_venv_with_no_uv_degrades_rather_than_execing(self, tmp_path, monkeypatch):
        venv = make_executable(tmp_path / ".venv" / "bin" / "python", "#!/bin/sh\nexit 1\n")
        monkeypatch.setattr(plugin_bootstrap, "find_uv", lambda: None)
        exec_ = Exec()

        note = plugin_bootstrap.reexec_if_old("s.py", tmp_path, version=OLD, exec_=exec_)

        assert exec_.calls == []
        assert "3.9.6" in note
        assert str(venv) in note

    def test_a_uv_that_cannot_run_degrades_rather_than_execing(self, tmp_path):
        """`uv run` exits 2 on a first sync with no network, a lock that no
        longer matches pyproject, or a plugin directory it cannot write."""
        uv = make_executable(tmp_path / "bin" / "uv", "#!/bin/sh\nexit 2\n")
        exec_ = Exec()

        note = plugin_bootstrap.reexec_if_old(
            "s.py", tmp_path, uv=str(uv), version=OLD, exec_=exec_
        )

        assert exec_.calls == []
        assert "not a broken install" in note
        assert str(uv) in note

    def test_the_probe_rejects_an_interpreter_that_runs_but_is_too_old(self):
        """A .venv built by the old python3 starts perfectly well and still
        cannot import the project, so execing it only moves the same failure
        one process along."""
        path = older_python()
        if not path:
            pytest.skip("no interpreter older than 3.10 on this machine")
        assert plugin_bootstrap.interpreter_works([path]) is False

    def test_a_probe_that_hangs_is_given_up_on(self, tmp_path):
        """An MCP host kills the Bash call these run under after a couple of
        minutes, so a `uv run` stuck downloading an interpreter would cost the
        user the whole report. Answering late is recoverable; hanging is not."""
        stuck = make_executable(tmp_path / "bin" / "uv", "#!/bin/sh\nsleep 30\n")
        assert plugin_bootstrap.interpreter_works([str(stuck)], timeout=0.5) is False

    def test_the_probe_accepts_a_usable_interpreter(self):
        # The one that has to say yes: without this the probe could reject
        # everything and every test above would still pass.
        assert plugin_bootstrap.interpreter_works([sys.executable]) is True


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

    def test_setup_exits_three_with_one_line_and_no_traceback(self, old):
        proc = self.run(old, "audacity_setup.py")
        # The exact code, not merely non-zero: CHANGELOG and commands/setup.md
        # both name it, and 2 already means "refused to write the config".
        assert proc.returncode == 3
        assert "Traceback" not in proc.stderr
        assert "not a broken install" in proc.stderr
        assert len(proc.stderr.strip().splitlines()) == 1


class TestTheReexecItselfUnderARealOldInterpreter:
    """The other half: everything above forces the degraded branch, so the
    re-exec proper was evidenced only by a manual transcript. These run the
    shipped plugin_doctor.py under a real old python3, against a copied plugin
    root so the developer's own .venv cannot decide the outcome.
    """

    @pytest.fixture
    def old(self):
        path = older_python()
        if not path:
            pytest.skip("no interpreter older than 3.10 on this machine")
        return path

    def run(self, python, root, uv_search):
        environ = dict(os.environ)
        environ["PATH"] = "/usr/bin:/bin"
        environ["AUDACITY_MCP_UV_SEARCH"] = uv_search
        environ.pop("UV_BIN", None)
        environ.pop(plugin_bootstrap.GUARD_ENV, None)
        return subprocess.run(
            [python, str(root / "scripts" / "plugin_doctor.py")],
            capture_output=True,
            text=True,
            cwd=str(root),
            env=environ,
            timeout=120,
        )

    def test_it_reexecs_under_uv_with_the_arguments_the_launcher_would_use(self, old, tmp_path):
        root = fake_plugin_root(tmp_path)
        log = tmp_path / "uv-argv.txt"
        uv = make_executable(
            tmp_path / "bin" / "uv",
            "#!/bin/sh\nprintf '%%s\\n' \"$@\" >> '%s'\nexit 0\n" % log,
        )

        proc = self.run(old, root, str(uv))

        assert proc.returncode == 0, proc.stderr
        lines = log.read_text().splitlines()
        # The last invocation is the exec; the one before it is the probe that
        # now has to pass before any exec happens at all.
        assert lines[-6:] == [
            "run",
            "--frozen",
            "--directory",
            str(root),
            "python",
            str(root / "scripts" / "plugin_doctor.py"),
        ]
        assert "-c" in lines[:-6]

    def test_a_venv_that_cannot_load_still_produces_a_report(self, old, tmp_path):
        """The regression this class exists for: exec succeeded, the child died
        loading libpython, and the doctor exited 1 with no report at all."""
        root = fake_plugin_root(tmp_path)
        make_executable(
            root / ".venv" / "bin" / "python",
            "#!/bin/sh\necho 'dyld: Library not loaded: libpython' >&2\nexit 1\n",
        )

        proc = self.run(old, root, str(tmp_path / "no" / "such" / "uv"))

        assert proc.returncode == 0, proc.stderr
        assert "plugin version: 0.0.0-test" in proc.stdout
        assert "not a broken install" in proc.stdout
        assert "Traceback" not in proc.stderr

    def test_a_uv_that_cannot_sync_still_produces_a_report(self, old, tmp_path):
        root = fake_plugin_root(tmp_path)
        uv = make_executable(
            tmp_path / "bin" / "uv",
            "#!/bin/sh\necho 'error: failed to sync' >&2\nexit 2\n",
        )

        proc = self.run(old, root, str(uv))

        assert proc.returncode == 0, proc.stderr
        assert "plugin version: 0.0.0-test" in proc.stdout
        assert "not a broken install" in proc.stdout
