#!/bin/zsh
# Run the Win32 pipe probe, one scenario per process, under CrossOver/Wine.
#
# Not part of scripts/verify.sh: it needs a Windows Python, and on a Mac that
# means a CrossOver bottle. See README.md in this directory for the setup.
#
#   CX_BOTTLE=audmcp64 PY=C:\\py\\python.exe ./tests/win32_probe/run_all.sh
#
# One process per scenario is required, not tidiness: a relay thread blocked in
# ConnectNamedPipe cannot be stopped, so a second relay in the same process
# leaves a stale server listening and every connection count after it is
# attributed to the wrong one.

set -u
CX_BIN=${CX_BIN:-/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin}
CX_BOTTLE=${CX_BOTTLE:-audmcp64}
PY=${PY:-'C:\py\python.exe'}
export CX_BOTTLE

REPO=${0:A:h:h:h}
PROBE="Z:${REPO//\//\\}\\tests\\win32_probe\\run_probe.py"

scenarios=(
  no_pipes_is_a_clear_error
  happy_path
  handles_are_reused
  retry_after_hangup
  retry_after_truncated_reply
  single_attempt_cannot_recover
)

failed=0
for s in $scenarios; do
  out=$("$CX_BIN/wine" "$PY" "$PROBE" "$s" 2>&1 | grep -E '^(\[(PASS|FAIL)\]|PRECONDITION)')
  rc=$?
  [ -z "$out" ] && out="[FAIL] $s: no verdict line (wine or python failed)"
  print -- "$out"
  case "$out" in (*'[FAIL]'*|*PRECONDITION*) failed=$((failed + 1));; esac
done

if [ $failed -gt 0 ]; then
  print -- "\n$failed scenario(s) failed"
  exit 1
fi
print -- "\nall ${#scenarios} scenarios passed"
