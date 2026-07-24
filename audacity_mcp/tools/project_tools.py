import os
from mcp.server.fastmcp import FastMCP
from audacity_mcp_shared.audio_file import describe
from audacity_mcp_shared.constants import ALLOWED_EXPORT_FORMATS, COMMON_SAMPLE_RATES
from audacity_mcp_shared.error_codes import AudacityMCPError, ErrorCode
from audacity_mcp_shared.pipe_protocol import path_corruption_warning


_BLOCKED_DIRS = None
_TEMP_DIR = None


def _get_blocked_dirs():
    """Directories that should never be written to."""
    global _BLOCKED_DIRS
    if _BLOCKED_DIRS is None:
        _BLOCKED_DIRS = set()
        if os.name == "nt":
            win_dir = os.environ.get("WINDIR", r"C:\Windows")
            _BLOCKED_DIRS.add(os.path.normcase(os.path.realpath(win_dir)))
            prog = os.environ.get("PROGRAMFILES", r"C:\Program Files")
            _BLOCKED_DIRS.add(os.path.normcase(os.path.realpath(prog)))
            prog86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
            _BLOCKED_DIRS.add(os.path.normcase(os.path.realpath(prog86)))
        else:
            for d in ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/var"):
                if os.path.isdir(d):
                    _BLOCKED_DIRS.add(os.path.realpath(d))
    return _BLOCKED_DIRS


def _temp_dir():
    """The system temp directory, resolved. Cached: realpath is not free."""
    global _TEMP_DIR
    if _TEMP_DIR is None:
        import tempfile

        _TEMP_DIR = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
    return _TEMP_DIR


def _safe_path(path: str) -> str:
    """Validate and canonicalize a file path. Returns the resolved absolute path."""
    if not os.path.isabs(path):
        raise AudacityMCPError(ErrorCode.INVALID_PATH, "Path must be absolute")
    resolved = os.path.realpath(path)
    # The temp directory is a legitimate export target and on macOS it lives
    # under /private/var, which the blocklist below would otherwise reject.
    temp_dir = _temp_dir()
    if os.path.normcase(resolved).startswith(temp_dir + os.sep):
        return resolved
    # Block system directories
    for blocked in _get_blocked_dirs():
        check = os.path.normcase(resolved)
        if check.startswith(blocked + os.sep) or check == blocked:
            raise AudacityMCPError(
                ErrorCode.INVALID_PATH,
                f"Cannot access system directory: {blocked}",
            )
    return resolved


def _pipe_state(path: str) -> dict:
    """Existence and age of one script pipe.

    Age matters because the pipe files outlive Audacity on POSIX — a stale
    pair from a previous launch looks exactly like a live one.
    """
    import time

    state = {"path": path, "exists": os.path.exists(path)}
    if state["exists"]:
        try:
            state["age_seconds"] = round(time.time() - os.stat(path).st_mtime, 1)
        except OSError:
            state["age_seconds"] = None
    return state


def _client_module_path() -> str:
    """Which copy of the client is actually imported.

    A stale install shadowing the checkout looks like a protocol bug, not a
    packaging one, so it is worth being able to answer in the same call.
    """
    import audacity_mcp.audacity_client as module

    return module.__file__


def _audacity_cfg_path() -> str | None:
    """Where Audacity keeps audacity.cfg on this platform, if it is there."""
    import sys

    home = os.path.expanduser("~")
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        candidates = [os.path.join(appdata, "audacity", "audacity.cfg")]
    elif sys.platform == "darwin":
        candidates = [os.path.join(home, "Library", "Application Support", "audacity", "audacity.cfg")]
    else:
        candidates = [
            os.path.join(home, ".config", "audacity", "audacity.cfg"),
            os.path.join(home, ".audacity-data", "audacity.cfg"),
        ]
    return next((c for c in candidates if os.path.isfile(c)), None)


def _default_project_sample_rate(cfg_path: str | None) -> int | None:
    """DefaultProjectSampleRate from audacity.cfg.

    An exotic value here is what produces exports at an unexpected rate and
    Audacity's "Error opening sound device" on playback, both of them far away
    from the setting that caused them.
    """
    if not cfg_path:
        return None
    import re

    try:
        with open(cfg_path, "r", errors="replace") as fh:
            match = re.search(r"^DefaultProjectSampleRate=(\d+)", fh.read(), re.MULTILINE)
    except OSError:
        return None
    return int(match.group(1)) if match else None


def register(mcp: FastMCP):
    from audacity_mcp.main import client

    @mcp.tool()
    async def project_new() -> dict:
        """Create a new empty Audacity project."""
        return await client.execute("New")

    @mcp.tool()
    async def project_open(path: str) -> dict:
        """Open an existing Audacity project file (.aup3).

        Args:
            path: Absolute path to the .aup3 project file
        """
        path = _safe_path(path)
        return await client.execute("OpenProject2", Filename=path)

    @mcp.tool()
    async def project_save() -> dict:
        """Save the current Audacity project. ONLY call this when the user explicitly asks to save.
        Do NOT auto-save after effects or pipelines — the user controls when to save."""
        return await client.execute("SaveProject2")

    @mcp.tool()
    async def project_save_as(path: str) -> dict:
        """Save the current project to a new .aup3 file. ONLY call when user explicitly asks.
        Do NOT auto-save after effects or pipelines.

        Args:
            path: Absolute path for the new .aup3 file
        """
        path = _safe_path(path)
        return await client.execute("SaveProject2", Filename=path)

    @mcp.tool()
    async def project_close() -> dict:
        """Close the current Audacity project."""
        return await client.execute("Close")

    @mcp.tool()
    async def project_import_audio(path: str) -> dict:
        """Import an audio file into the current project. Creates a new track.

        Args:
            path: Absolute path to the audio file (wav, mp3, ogg, flac, etc.)
        """
        path = _safe_path(path)
        result = await client.execute("Import2", Filename=path)
        warning = path_corruption_warning(path)
        if warning:
            result.setdefault("warnings", []).append(warning)
        return result

    def _default_music_folder() -> str:
        """Get the user's Music folder, works on any system."""
        home = os.path.expanduser("~")
        music = os.path.join(home, "Music")
        if not os.path.exists(music):
            os.makedirs(music, exist_ok=True)
        return music

    @mcp.tool()
    async def get_default_export_folder() -> dict:
        """Get the default folder for exporting audio files.
        Returns the user's Music folder path. Use this when the user doesn't specify where to save."""
        return {"path": _default_music_folder()}

    @mcp.tool()
    async def audacity_health_check() -> dict:
        """Check whether Audacity is reachable, and why not if it isn't.

        Run this first when anything fails in a way that might not be about the
        command you sent: no response, an empty response, a timeout, or an
        export that came out wrong. Reports both script pipes separately, how
        old they are, whether a round trip actually answers, which copy of the
        client is installed, and the default project sample rate — plus what to
        do about whatever it finds.
        """
        from audacity_mcp_shared.constants import COMMON_SAMPLE_RATES, PipePaths, Timeouts

        to_pipe = _pipe_state(PipePaths.TO_SRV)
        from_pipe = _pipe_state(PipePaths.FROM_SRV)

        round_trip = {"ok": False}
        if to_pipe["exists"] and from_pipe["exists"]:
            try:
                # Type=Tracks is cheap and read-only. Never use Type=Commands
                # here: it pegs Audacity and takes the app down with it.
                await client.execute("GetInfo", Type="Tracks", _timeout=Timeouts.HEALTH_CHECK)
                round_trip["ok"] = True
            except AudacityMCPError as e:
                round_trip["error"] = str(e)
            except Exception as e:  # a non-MCP failure is still a failed round trip
                round_trip["error"] = f"{type(e).__name__}: {e}"

        cfg_path = _audacity_cfg_path()
        sample_rate = _default_project_sample_rate(cfg_path)

        next_steps = []
        if not to_pipe["exists"] and not from_pipe["exists"]:
            next_steps.append(
                "Neither script pipe exists. Audacity is not running, or mod-script-pipe "
                "is not enabled. Enable it under Preferences > Modules, then fully quit "
                "Audacity (Cmd+Q / File > Exit — closing the window is not enough) and relaunch: "
                "the pipes are created at launch."
            )
        elif not (to_pipe["exists"] and from_pipe["exists"]):
            missing = "from" if to_pipe["exists"] else "to"
            next_steps.append(
                f"Only one pipe is present (the '{missing}' pipe is missing), so mod-script-pipe "
                "did not fully activate this launch. Fully quit Audacity and relaunch."
            )
        elif not round_trip["ok"]:
            next_steps.append(
                "Both pipes exist but Audacity did not answer. Either the pipe files are stale "
                "from a previous launch (check the ages reported here), or Audacity restarted "
                "while this server was running and the server is holding dead handles. MCP "
                "servers start with the client session, so that second case is fixed by "
                "restarting the MCP client, not Audacity."
            )
        if sample_rate is not None and sample_rate not in COMMON_SAMPLE_RATES:
            next_steps.append(
                f"The default project sample rate is {sample_rate} Hz, which most sound devices "
                "cannot play. This causes exports at an unexpected rate and Audacity's 'Error "
                "opening sound device'. Change it in Settings > Audio Settings, or edit "
                "DefaultProjectSampleRate in audacity.cfg while Audacity is closed — the config "
                "is rewritten on quit, and the default only applies to new projects."
            )

        return {
            "healthy": round_trip["ok"],
            "pipes": {"to": to_pipe, "from": from_pipe},
            "round_trip": round_trip,
            "client_module": _client_module_path(),
            "config_file": cfg_path,
            "default_project_sample_rate": sample_rate,
            "next_steps": next_steps,
        }

    @mcp.tool()
    async def project_export_audio(
        path: str, num_channels: int = 2, whole_project: bool = True
    ) -> dict:
        """Export the project audio to a file. Format is determined by file extension.

        MANDATORY: ALWAYS tell the user where the file will be saved BEFORE exporting.
        Example: "I'll save your audio to C:\\Users\\Name\\Music\\file.mp3"

        NEVER save directly to the user's home folder (e.g. ~/file.mp3 or C:\\Users\\Name\\file.mp3).
        ALWAYS save to a subfolder. If the user doesn't specify a path, call get_default_export_folder
        to get their Music folder and save there. Acceptable locations:
        - Music folder: ~/Music/file.mp3
        - Documents folder: ~/Documents/file.mp3
        - Desktop: ~/Desktop/file.mp3
        NEVER save to the home folder root — this clutters the user's home directory.

        Args:
            path: Absolute path for exported file. Extension determines format (wav, mp3, ogg, flac, aiff).
            num_channels: Number of channels (1=mono, 2=stereo). Default: 2
            whole_project: Select every track and the whole timeline first. Default: True.
                Audacity's Export2 exports the CURRENT SELECTION, so leaving this
                on is what makes "export the project" mean the project. Set it to
                False only when you deliberately want the current selection.

        The result carries a `verified` block read back from the written file:
        the container actually present, its sample rate, channels and duration.
        Audacity uses the sticky last-used exporter and will write AIFF into a
        path named .wav while reporting a WAV export, and it renders at the
        project sample rate rather than anything asked for here — so a `warning`
        appears when what landed on disk is not what was requested.
        """
        path = _safe_path(path)
        # Block saving directly to user home folder
        home = os.path.realpath(os.path.expanduser("~"))
        parent = os.path.dirname(path)
        if os.path.realpath(parent) == home:
            raise AudacityMCPError(
                ErrorCode.INVALID_PATH,
                f"Do not save directly to the home folder. Use a subfolder like Music or Documents. "
                f"Call get_default_export_folder to get the correct path."
            )
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        if ext not in ALLOWED_EXPORT_FORMATS:
            raise AudacityMCPError(
                ErrorCode.INVALID_FORMAT,
                f"Unsupported format: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXPORT_FORMATS))}",
            )
        if os.path.exists(path):
            raise AudacityMCPError(
                ErrorCode.INVALID_PATH,
                f"File already exists: {path}. Use a different filename to avoid overwriting.",
            )
        # Create output directory if it doesn't exist
        parent_dir = os.path.dirname(path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        if whole_project:
            # Export2 exports the selection. Both halves are needed: SelAllTracks
            # takes every track, SelectAll takes the whole timeline.
            await client.execute("SelAllTracks")
            await client.execute("SelectAll")

        result = await client.execute_long("Export2", Filename=path, NumChannels=num_channels)

        verified = describe(path)
        result["verified"] = verified
        warnings = []
        path_warning = path_corruption_warning(path)
        if path_warning:
            warnings.append(path_warning)
        if verified["container"] == "unknown" and verified["bytes"] is None:
            warnings.append(f"Audacity reported success but no file was written at {path}.")
        else:
            actual = verified["container"]
            requested = "wav" if ext == "wav" else ext
            if actual not in ("unknown", requested):
                warnings.append(
                    f"The file is {actual.upper()} data despite the .{ext} extension. Audacity "
                    "reuses the last exporter it was given and the extension does not override "
                    "it; set the format in Audacity's export dialog, or convert the file."
                )
            if verified["channels"] is not None and verified["channels"] != num_channels:
                warnings.append(
                    f"Exported {verified['channels']} channel(s), not the {num_channels} requested."
                )
            if verified["sample_rate"] is not None and verified["sample_rate"] not in COMMON_SAMPLE_RATES:
                warnings.append(
                    f"Exported at {verified['sample_rate']} Hz, which comes from the project "
                    "sample rate, not from this call. Most sound devices cannot play it back."
                )
        if warnings:
            result["warnings"] = warnings
        return result

    @mcp.tool()
    async def project_export_labels(path: str) -> dict:
        """Export all labels to a text file.

        Args:
            path: Absolute path for the exported labels file
        """
        path = _safe_path(path)
        if os.path.exists(path):
            raise AudacityMCPError(
                ErrorCode.INVALID_PATH,
                f"File already exists: {path}. Use a different filename to avoid overwriting.",
            )
        return await client.execute("ExportLabels", Filename=path)

    @mcp.tool()
    async def project_get_info(info_type: str = "Tracks") -> dict:
        """Get information about the current project.

        Args:
            info_type: Type of info to retrieve. One of: Tracks, Clips, Envelopes, Labels, Boxes

        "Commands" is deliberately not accepted. On Audacity 3.7.8 it pegs the
        CPU, never answers, and takes the application down with it — with no
        autosave recovery, so unsaved work is lost.
        """
        allowed = {"Tracks", "Clips", "Envelopes", "Labels", "Boxes"}
        if info_type == "Commands":
            raise AudacityMCPError(
                ErrorCode.INVALID_PARAMETER,
                "info_type='Commands' is blocked: GetInfo Type=Commands hangs Audacity and "
                "crashes it, losing unsaved work, and it does not recover from autosave. "
                f"Allowed: {', '.join(sorted(allowed))}",
            )
        if info_type not in allowed:
            raise AudacityMCPError(
                ErrorCode.INVALID_PARAMETER,
                f"Invalid info_type: {info_type}. Allowed: {', '.join(sorted(allowed))}",
            )
        return await client.execute("GetInfo", Type=info_type)

    @mcp.tool()
    async def project_edit_metadata() -> dict:
        """Open the metadata editor dialog to view/edit track metadata (title, artist, etc.).
        This opens a modal dialog in Audacity — the command waits until the user closes it."""
        return await client.execute_long("EditMetaData")

    @mcp.tool()
    async def project_import_midi(path: str) -> dict:
        """Import a MIDI file into the current project.

        Args:
            path: Absolute path to the MIDI file (.mid, .midi)
        """
        path = _safe_path(path)
        return await client.execute("ImportMIDI", Filename=path)
