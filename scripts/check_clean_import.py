#!/usr/bin/env python3
"""Import audacity_mcp.main and fail on any warning.

Importing main constructs the client and registers the atexit handler, so this
catches the class of bug where shutdown wiring is wrong but nothing raises —
e.g. registering an async close() with atexit, which only ever produced a
"coroutine was never awaited" RuntimeWarning (issue #3).
"""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

SNIPPET = """
import warnings
warnings.simplefilter("error")
import audacity_mcp.main  # noqa: F401
import inspect
# atexit callbacks run at interpreter shutdown, where a discarded coroutine
# would surface as an unraisable warning rather than an error; check directly.
assert not inspect.iscoroutinefunction(audacity_mcp.main.client.close_sync)
print("import clean")
"""


def main() -> int:
    proc = subprocess.run(
        [sys.executable, "-W", "error", "-c", SNIPPET],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        print("ERROR: importing audacity_mcp.main was not warning-free", file=sys.stderr)
        return 1
    if proc.stderr.strip():
        print("ERROR: import wrote to stderr", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
