"""Facts about the Audacity install on this machine.

Shared by the health-check tool and the plugin's setup command, which is why it
lives here rather than in a tools module: audacity_mcp_shared is stdlib-only and
importable from a bare Python, and setup runs before any dependency has been
resolved.

Nothing here writes. Deciding to write to audacity.cfg is the setup script's
job, and it needs audacity_is_running() to make that decision safely.
"""
import os
import re
import subprocess
import sys

# A Snap-packaged Audacity (Ubuntu's default) keeps its config inside the snap's
# own home, so the XDG path simply does not exist on those machines.
SNAP_CFG_PARTS = ("snap", "audacity", "current", ".config", "audacity", "audacity.cfg")

# Audacity's documented values for the module setting. Anything else is treated
# as absent rather than guessed at.
_MODULE_STATES = {"0": "disabled", "1": "enabled", "2": "ask"}


def audacity_cfg_candidates(platform=None, home=None):
    """Every place audacity.cfg might be, most likely first."""
    platform = platform or sys.platform
    home = home or os.path.expanduser("~")
    if platform == "win32":
        appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return [os.path.join(appdata, "audacity", "audacity.cfg")]
    if platform == "darwin":
        return [os.path.join(home, "Library", "Application Support", "audacity", "audacity.cfg")]
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return [
        os.path.join(xdg, "audacity", "audacity.cfg"),
        os.path.join(home, *SNAP_CFG_PARTS),
        os.path.join(home, ".audacity-data", "audacity.cfg"),
    ]


def audacity_cfg_path(platform=None, home=None):
    """The config file that exists, or None."""
    return next((c for c in audacity_cfg_candidates(platform, home) if os.path.isfile(c)), None)


def _read(cfg_path):
    if not cfg_path:
        return None
    try:
        with open(cfg_path, "r", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def mod_script_pipe_state(cfg_path):
    """'enabled' | 'ask' | 'disabled' | 'absent' | 'no-config'.

    'ask' matters as its own answer: Audacity prompts on every launch and the
    pipes do not appear until someone clicks through, which reads as a broken
    install rather than a pending dialog.
    """
    text = _read(cfg_path)
    if text is None:
        return "no-config"
    match = re.search(r"^mod-script-pipe=(\w+)", text, re.MULTILINE)
    if not match:
        return "absent"
    return _MODULE_STATES.get(match.group(1), "absent")


def default_project_sample_rate(cfg_path):
    """DefaultProjectSampleRate, or None.

    An exotic value here is what produces exports at an unexpected rate and
    Audacity's "Error opening sound device" on playback, both of them far away
    from the setting that caused them.
    """
    text = _read(cfg_path)
    if text is None:
        return None
    match = re.search(r"^DefaultProjectSampleRate=(\d+)", text, re.MULTILINE)
    return int(match.group(1)) if match else None


def _default_run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def audacity_is_running(_run=None, platform=None):
    """True, False, or None when the process table could not be read.

    Setup needs this before touching audacity.cfg: Audacity rewrites that file
    when it quits, so an edit made while it is open is reverted the next time
    the user closes the app - and the edit looks like it worked.
    """
    run = _run or _default_run
    platform = platform or sys.platform
    windows = platform == "win32"
    if windows:
        cmd = ["tasklist", "/FI", "IMAGENAME eq audacity.exe", "/NH"]
        names = {"audacity.exe"}
    else:
        cmd = ["ps", "-Ao", "comm="]
        names = {"audacity"}
    try:
        proc = run(cmd)
    except (OSError, subprocess.SubprocessError):
        return None
    output = proc.stdout or ""
    if getattr(proc, "returncode", 0) != 0 and not output.strip():
        # A probe that exists but fails - busybox ps without -Ao comm=, a
        # container that will not show the process table - said nothing, and
        # nothing is not "Audacity is closed". Falling through to False here
        # would let setup write a config Audacity reverts on quit, which is the
        # exact failure this function exists to prevent.
        return None
    for line in output.splitlines():
        entry = line.strip()
        if not entry:
            continue
        # A tasklist row is "audacity.exe  6244 Console  1  92,116 K", so only
        # its first field is a name; taking the basename of the whole row never
        # matched and the answer was always False while Audacity was open. On
        # POSIX the whole line is the comm, which may contain spaces (an app
        # bundle with a space in its name), so it is compared intact.
        entry = entry.split()[0] if windows else entry
        # Compare the basename exactly. A substring test would match this
        # server's own audacity-mcp-max process and never let setup write.
        if os.path.basename(entry).lower() in names:
            return True
    return False
