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
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from plugin_bootstrap import (  # noqa: E402
    UV_INSTALL_URL,
    find_uv,
    measurement_state,
    reexec_if_old,
    transcription_state,
)


def main() -> int:
    # Before anything else, because /audacity:doctor invokes this as bare
    # python3 and on stock macOS that is 3.9.6, which cannot import
    # audacity_mcp_shared at all. Returns a line to print when it cannot find
    # anything better; the report continues either way.
    old_python = reexec_if_old(__file__, REPO)
    if old_python:
        print(old_python)

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

    state, detail = transcription_state(REPO, degraded=old_python)
    if state == "installed":
        print("transcription extra: installed")
    elif state == "missing":
        print("transcription extra: not installed (/audacity:setup --transcription)")
    else:
        print("transcription extra: unknown (%s)" % detail)

    # Checked the same way as transcription: in the plugin's venv when that is
    # not the interpreter running this script, since `uv sync --extra
    # measurement` installs there and not here. Asserting "not installed" from
    # the wrong interpreter is how the transcription check told users forever
    # that they had not completed a step they had.
    state, detail = measurement_state(REPO, degraded=old_python)
    if state == "installed":
        print("measurement: installed")
    elif state == "missing":
        print("measurement: not installed (/audacity:setup --measurement)")
    else:
        print("measurement: unknown (%s)" % detail)

    if old_python:
        # audacity_mcp_shared uses 3.10 syntax, so there is nothing below this
        # that a 3.9 can reach. Say so with the same prefix the healthy failure
        # uses, since commands/doctor.md keys its guidance on that line.
        print("pipe and config info: unavailable (see the python line above)")
        return 0

    try:
        sys.path.insert(0, str(REPO))
        from audacity_mcp_shared.constants import PipePaths
        from audacity_mcp_shared.environment import audacity_cfg_path, mod_script_pipe_state

        PipePaths.rediscover()
        print(f"pipe (to):   {PipePaths.TO_SRV} {'present' if os.path.exists(PipePaths.TO_SRV) else 'missing'}")
        print(f"pipe (from): {PipePaths.FROM_SRV} {'present' if os.path.exists(PipePaths.FROM_SRV) else 'missing'}")

        cfg = audacity_cfg_path()
        print(f"audacity.cfg: {cfg or 'not found'}")
        print(f"mod-script-pipe: {mod_script_pipe_state(cfg)}")
    except Exception as e:
        # Broad on purpose: this is the section most likely to break on a
        # partial install (a stale checkout missing audacity_mcp_shared, a
        # syntax error in a half-merged file, a permissions error walking
        # /proc during pipe rediscovery on Linux). An ImportError is only one
        # of the ways that goes wrong, and the whole point of this script is
        # that none of those ways may turn into a non-zero exit.
        print(f"pipe and config info: unavailable ({e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
