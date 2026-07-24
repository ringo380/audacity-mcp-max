#!/usr/bin/env python3
"""Getting the plugin's scripts onto an interpreter that can import the project.

This is the one module here that has to run on any Python at all. It is imported
before the first `from audacity_mcp_shared ...`, and that import is what fails:
`constants.py` uses `int | None`, which needs 3.10, while stock macOS still ships
3.9.6 as /usr/bin/python3. The plugin's whole pitch is that there is no pip step
and uv handles Python, so the user most likely to run these scripts by hand is
exactly the one with no modern interpreter on PATH.

Keep it stdlib-only and valid 3.8 syntax - no annotations, no f-strings with
`=`, nothing this file could trip over. It runs before anything is known to work.
"""
import os
import shutil
import subprocess
import sys

MIN_PYTHON = (3, 10)
UV_INSTALL_URL = "https://docs.astral.sh/uv/"

# Set on the re-exec so a second-generation process cannot try again. Without it
# a .venv holding an interpreter that is itself too old would exec forever.
GUARD_ENV = "AUDACITY_MCP_PY_BOOTSTRAP"


def find_uv():
    """The same resolution order as scripts/launch-mcp.sh.

    Duplicated there on purpose: the launcher cannot shell out to Python to
    find the thing that starts Python, so this logic has to exist twice. The
    agreement tests in tests/test_plugin_doctor.py exist precisely to catch
    the two copies drifting apart.
    """
    explicit = os.environ.get("UV_BIN")
    if explicit and os.access(explicit, os.X_OK):
        return explicit
    found = shutil.which("uv")
    if found:
        return found
    home = os.path.expanduser("~")
    default_search = ":".join(
        [
            os.path.join(home, ".local", "bin", "uv"),
            os.path.join(home, ".cargo", "bin", "uv"),
            "/opt/homebrew/bin/uv",
            "/usr/local/bin/uv",
        ]
    )
    for candidate in os.environ.get("AUDACITY_MCP_UV_SEARCH", default_search).split(":"):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def venv_python(plugin_root):
    """The interpreter inside the plugin's own venv, or None if uv never built it."""
    root = os.path.join(str(plugin_root), ".venv")
    for parts in (("bin", "python"), ("Scripts", "python.exe")):
        candidate = os.path.join(root, *parts)
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _is_current_interpreter(path):
    """True when `path` is the interpreter already running.

    The exec-loop guard. A .venv/bin/python that is a symlink back to the
    /usr/bin/python3 we are running under looks like an upgrade and is not.
    """
    try:
        return os.path.samefile(path, sys.executable)
    except OSError:
        return os.path.realpath(path) == os.path.realpath(sys.executable)


def modern_interpreter_argv(plugin_root, uv=None):
    """The argv prefix for an interpreter new enough to import the project.

    The venv comes first because it is the environment the server itself runs
    in, so anything it can import is what the server can import. uv is the
    fallback because it works on a fresh install, where .venv does not exist
    yet - `uv run` builds it.
    """
    venv = venv_python(plugin_root)
    if venv and not _is_current_interpreter(venv):
        return [venv]
    uv = find_uv() if uv is None else uv
    if uv:
        return [uv, "run", "--directory", str(plugin_root), "python"]
    return None


def _too_old(version, detail):
    """The degraded-mode line.

    It names the version it found and says outright that this is not a broken
    install: the previous wording sent people to reinstall the plugin over a
    Python that was simply old.
    """
    return (
        "python: %d.%d.%d is too old - this project needs %d.%d or newer. "
        "That is an old interpreter on PATH, not a broken install (%s). "
        "Install uv from %s, or run these scripts with a newer python3."
        % (
            version[0],
            version[1],
            version[2],
            MIN_PYTHON[0],
            MIN_PYTHON[1],
            detail,
            UV_INSTALL_URL,
        )
    )


def reexec_if_old(script, plugin_root, uv=None, version=None, exec_=None):
    """Re-run `script` under a newer interpreter, or explain why it could not.

    Returns None when nothing needed doing (the running interpreter is new
    enough, or the exec succeeded and this never returns at all), otherwise a
    single line the caller can print. It never raises: a bootstrap that dies
    while explaining that the interpreter is old is worse than the old
    interpreter.
    """
    version = tuple(sys.version_info)[:3] if version is None else tuple(version)[:3]
    if version[:2] >= MIN_PYTHON:
        return None
    if os.environ.get(GUARD_ENV):
        return _too_old(version, "the interpreter this was already re-run under is old too")
    argv = modern_interpreter_argv(plugin_root, uv)
    if argv is None:
        return _too_old(version, "no newer interpreter and no uv were found")
    os.environ[GUARD_ENV] = "1"
    try:
        (exec_ or os.execv)(argv[0], argv + [str(script)] + list(sys.argv[1:]))
    except OSError as exc:
        os.environ.pop(GUARD_ENV, None)
        return _too_old(version, "re-running under %s failed (%s)" % (argv[0], exc))
    return None


def transcription_state(plugin_root, degraded=None):
    """('installed' | 'missing' | 'unknown', detail) for the transcription extra.

    A plain import in whatever interpreter is running answers a different
    question than the one asked. `uv sync --extra transcription` installs into
    the plugin's own .venv, so a user who had just finished that step was told
    forever that they had not. Probe the venv whenever it is not us.
    """
    if degraded:
        return ("unknown", "cannot check under this interpreter")
    venv = venv_python(plugin_root)
    if venv and not _is_current_interpreter(venv):
        try:
            proc = subprocess.run(
                [venv, "-c", "import faster_whisper"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ("unknown", "%s: %s" % (type(exc).__name__, exc))
        return ("installed", "") if proc.returncode == 0 else ("missing", "")
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return ("missing", "")
    except Exception as exc:
        # ctranslate2 and onnxruntime are native. A wheel built for the wrong
        # architecture, or a missing system library, fails the dlopen with
        # OSError rather than ImportError - which escaped the doctor's catch
        # and took the whole report down with it before the pipe section.
        return ("unknown", "%s: %s" % (type(exc).__name__, exc))
    return ("installed", "")
