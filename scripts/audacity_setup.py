#!/usr/bin/env python3
"""Report the state of the Audacity install, and optionally fix the module setting.

Reads by default. The only write it ever makes is mod-script-pipe=1, and it
refuses to make that one while Audacity is running: Audacity rewrites
audacity.cfg on quit, so an edit made while it is open is reverted the next time
the user closes the app, having reported success on the way.
"""
import argparse
import os
import pathlib
import re
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from audacity_mcp_shared.constants import COMMON_SAMPLE_RATES, PipePaths  # noqa: E402
from audacity_mcp_shared.environment import (  # noqa: E402
    audacity_cfg_candidates,
    audacity_cfg_path,
    audacity_is_running,
    default_project_sample_rate,
    mod_script_pipe_state,
)

RUNNING_MESSAGE = (
    "Audacity is running. It rewrites audacity.cfg when it quits, so a change made "
    "now would be reverted the next time the app is closed. Fully quit Audacity "
    "(Cmd+Q, or File > Exit - closing the window is not enough) and run this again."
)
UNKNOWN_MESSAGE = (
    "Could not read the process table, so there is no way to tell whether Audacity "
    "is running. Refusing to write rather than risk a change that gets reverted on "
    "quit. Quit Audacity if it is open, then set mod-script-pipe to Enabled by hand "
    "under Preferences > Modules."
)


def enable_module(cfg_path, running):
    """Set mod-script-pipe=1 in cfg_path unless Audacity is running.

    `running` is True, False, or None for "could not tell". Returns a dict with
    `changed`, and on refusal a `refused` reason and a `message`.
    """
    if running is not False:
        return {
            "changed": False,
            "refused": "audacity-running" if running else "unknown-state",
            "message": RUNNING_MESSAGE if running else UNKNOWN_MESSAGE,
        }

    text = pathlib.Path(cfg_path).read_text(errors="replace")
    if re.search(r"^mod-script-pipe=1$", text, re.MULTILINE):
        return {"changed": False, "message": "mod-script-pipe was already enabled."}

    shutil.copy(cfg_path, cfg_path + ".bak")
    if re.search(r"^mod-script-pipe=", text, re.MULTILINE):
        updated = re.sub(r"^mod-script-pipe=\w+$", "mod-script-pipe=1", text, flags=re.MULTILINE)
    elif "[Module]" in text:
        updated = text.replace("[Module]", "[Module]\nmod-script-pipe=1", 1)
    else:
        updated = text.rstrip("\n") + "\n[Module]\nmod-script-pipe=1\n"
    pathlib.Path(cfg_path).write_text(updated)
    return {
        "changed": True,
        "message": "mod-script-pipe enabled. Restart Audacity for it to take effect.",
    }


def report():
    """Everything setup knows, as ordered lines."""
    lines = []
    cfg = audacity_cfg_path()
    running = audacity_is_running()

    lines.append(f"audacity.cfg: {cfg or 'not found'}")
    if not cfg:
        lines.append("  looked in: " + ", ".join(audacity_cfg_candidates()))
        lines.append("  Open Audacity once to generate it, then run this again.")
    lines.append(f"mod-script-pipe: {mod_script_pipe_state(cfg)}")
    lines.append(
        "audacity running: " + {True: "yes", False: "no", None: "unknown"}[running]
    )

    rate = default_project_sample_rate(cfg)
    lines.append(f"default project sample rate: {rate if rate is not None else 'unknown'}")
    if rate is not None and rate not in COMMON_SAMPLE_RATES:
        lines.append(
            f"  WARNING: {rate} Hz is not a rate most sound devices can play. It causes "
            "exports at an unexpected rate and 'Error opening sound device'. Change it "
            "in Settings > Audio Settings while Audacity is closed."
        )

    PipePaths.rediscover()
    for label, path in (("to", PipePaths.TO_SRV), ("from", PipePaths.FROM_SRV)):
        lines.append(f"pipe ({label}): {path} {'present' if os.path.exists(path) else 'missing'}")

    try:
        import faster_whisper  # noqa: F401

        lines.append("transcription extra: installed")
    except ImportError:
        lines.append("transcription extra: not installed")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--enable-module",
        action="store_true",
        help="set mod-script-pipe=1 (refused while Audacity is running)",
    )
    args = parser.parse_args()

    for line in report():
        print(line)

    if not args.enable_module:
        return 0

    cfg = audacity_cfg_path()
    if not cfg:
        print("\nNo audacity.cfg to edit. Open Audacity once first.", file=sys.stderr)
        return 2
    result = enable_module(cfg, audacity_is_running())
    print("\n" + result["message"])
    return 2 if result.get("refused") else 0


if __name__ == "__main__":
    sys.exit(main())
