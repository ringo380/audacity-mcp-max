#!/usr/bin/env python3
"""What the MCP server cannot report about itself.

audacity_health_check runs inside the server, so it cannot say whether uv was
found, whether the venv was ever built, or which plugin version is installed -
by the time it runs, all of that already worked. This covers the half of "why
isn't it connecting" that lives outside the server process.

Always exits 0. A diagnostic that exits non-zero when it finds a problem reads
as a broken diagnostic.
"""
import json
import os
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
UV_INSTALL_URL = "https://docs.astral.sh/uv/"


def find_uv():
    """The same resolution order as scripts/launch-mcp.sh.

    Duplicated here on purpose: the launcher cannot shell out to Python to
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


def main() -> int:
    try:
        plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        print(f"plugin version: {plugin.get('version')}")
    except (OSError, ValueError) as e:
        print(f"plugin version: unreadable ({e})")

    print(f"plugin root: {REPO}")

    uv = find_uv()
    if uv:
        print(f"uv: {uv}")
    else:
        print(f"uv: not found - install it from {UV_INSTALL_URL}, or set UV_BIN")

    venv = REPO / ".venv"
    print(f"venv built: {'yes' if venv.is_dir() else 'no (first launch will build it)'}")

    try:
        import faster_whisper  # noqa: F401

        print("transcription extra: installed")
    except ImportError:
        print("transcription extra: not installed (/audacity:setup --transcription)")

    sys.path.insert(0, str(REPO))
    from audacity_mcp_shared.constants import PipePaths
    from audacity_mcp_shared.environment import audacity_cfg_path, mod_script_pipe_state

    PipePaths.rediscover()
    print(f"pipe (to):   {PipePaths.TO_SRV} {'present' if os.path.exists(PipePaths.TO_SRV) else 'missing'}")
    print(f"pipe (from): {PipePaths.FROM_SRV} {'present' if os.path.exists(PipePaths.FROM_SRV) else 'missing'}")

    cfg = audacity_cfg_path()
    print(f"audacity.cfg: {cfg or 'not found'}")
    print(f"mod-script-pipe: {mod_script_pipe_state(cfg)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
