import os
import sys
from pathlib import Path


def _resolve_pipe_dir() -> str:
    """Where to look for the POSIX script FIFOs.

    AUDACITY_PIPE_DIR is the escape hatch for any layout this code cannot work
    out for itself (a Flatpak, a container mount, a Snap whose pid is not
    visible). When it is set it wins outright and `PipePaths.rediscover()` keeps
    its hands off.
    """
    return os.environ.get("AUDACITY_PIPE_DIR") or "/tmp"


def _pipe_names(uid: int) -> tuple[str, str]:
    """The (to, from) FIFO filenames Audacity creates for `uid`."""
    return (f"audacity_script_pipe.to.{uid}", f"audacity_script_pipe.from.{uid}")


def discover_snap_pipe_dir(proc_root: str = "/proc", uid: int | None = None) -> str | None:
    """Find a Snap-confined Audacity's private /tmp, or None if there isn't one.

    Ubuntu installs Audacity as a Snap by default, and a Snap runs in its own
    mount namespace with a private /tmp - so its FIFOs never appear under the
    host's /tmp and everything here looks like "Audacity is not running"
    (upstream issue #7). The same files are reachable from outside at
    /proc/<pid>/root/tmp.

    Reads /proc/<pid>/comm rather than shelling out to pgrep, and only accepts a
    directory that actually holds both FIFOs for this uid, so a differently
    confined or unrelated process named audacity cannot point us at an empty
    directory.
    """
    if uid is None:
        uid = os.getuid()
    to_name, from_name = _pipe_names(uid)
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return None
    for entry in entries:
        # No pid filter: an entry that is not a process has no readable comm, and
        # one that is (/proc/self) is this server, whose comm is not "audacity".
        try:
            with open(os.path.join(proc_root, entry, "comm")) as fh:
                if fh.read().strip() != "audacity":
                    continue
        except OSError:
            continue  # exited between listdir and open, or not ours to read
        candidate = os.path.join(proc_root, entry, "root", "tmp")
        if os.path.exists(os.path.join(candidate, to_name)) and os.path.exists(
            os.path.join(candidate, from_name)
        ):
            return candidate
    return None


class PipePaths:
    if sys.platform == "win32":
        TO_SRV = r"\\.\pipe\ToSrvPipe"
        FROM_SRV = r"\\.\pipe\FromSrvPipe"
    else:
        _uid = os.getuid()
        _dir = _resolve_pipe_dir()
        _to_name, _from_name = _pipe_names(_uid)
        TO_SRV = os.path.join(_dir, _to_name)
        FROM_SRV = os.path.join(_dir, _from_name)

    _DEFAULTS = (TO_SRV, FROM_SRV)
    _discovered = None  # the (to, from) pair rediscover() last installed

    @classmethod
    def rediscover(cls) -> None:
        """Repoint at a Snap Audacity's private /tmp when the plain paths are empty.

        Called before each open rather than once at import because the MCP
        server starts with the client session, usually before Audacity - a pid
        resolved at import time would be the wrong answer, or no answer, for the
        rest of the session. Re-running also survives an Audacity restart, which
        changes the pid and so the whole path.

        It is a no-op unless it has something to fix, and it refuses to touch
        paths it did not set itself, which is what keeps it out of the way of the
        test suite's isolated FIFOs.
        """
        if sys.platform != "linux":
            return  # /proc/<pid>/root is a Linux thing
        if os.environ.get("AUDACITY_PIPE_DIR"):
            return
        current = (cls.TO_SRV, cls.FROM_SRV)
        if current != cls._DEFAULTS and current != cls._discovered:
            return  # someone set these deliberately; leave them alone
        if os.path.exists(cls.TO_SRV) and os.path.exists(cls.FROM_SRV):
            return
        snap_dir = discover_snap_pipe_dir()
        if snap_dir is None:
            # Whatever we found before is gone (Audacity restarted, or was
            # never a Snap). Fall back rather than keep a dead pid's path.
            cls.TO_SRV, cls.FROM_SRV = cls._DEFAULTS
            cls._discovered = None
            return
        to_name, from_name = _pipe_names(os.getuid())
        cls._discovered = (
            os.path.join(snap_dir, to_name),
            os.path.join(snap_dir, from_name),
        )
        cls.TO_SRV, cls.FROM_SRV = cls._discovered


class Timeouts:
    PIPE_OPEN = 5.0
    PIPE_READ = 10.0
    # How long to wait, when tearing a POSIX pipe down, for the relay to close
    # its own write end before we drop our reader. Closing early SIGPIPEs the
    # relay and takes Audacity with it (issue #19); a responsive relay hits EOF
    # in well under a millisecond, so this only bounds a hung one.
    PIPE_DRAIN = 2.0
    COMMAND = 30.0
    # The health check is what a caller runs when things are already wrong, so
    # it must not spend the full command timeout confirming silence.
    HEALTH_CHECK = 5.0
    LONG_COMMAND = 600.0  # 10 minutes — large files (2-3hr podcasts) need this
    # How long a caller waits for an abandoned send to unwind after its command
    # timed out. The worker checks the same deadline the caller did, so it is
    # already on its way out; this only covers the unwinding itself.
    WORKER_EXIT = 2.0


ALLOWED_EXPORT_FORMATS = {"wav", "mp3", "ogg", "flac", "aiff", "mp4"}

# Rates any sound device can be expected to play back. Resampling to something
# outside this set is allowed — Audacity accepts it — but it is worth flagging,
# because an exotic project rate is what makes Audacity fail to open the output
# device at playback time, far away from the call that caused it.
COMMON_SAMPLE_RATES = {8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000}

MAX_TRACKS = 500
MAX_LABEL_LENGTH = 1000

WHISPER_MODEL_SIZES = {"tiny", "base", "small", "medium", "large-v3"}
TRANSCRIPTION_TASKS = {"transcribe", "translate"}
SUBTITLE_FORMATS = {"srt", "vtt", "txt"}
