#!/usr/bin/env bash
# One command that verifies the tree. Runs from a clean clone, with or without
# Audacity running — nothing here talks to the real script pipe.
#
#   ./scripts/verify.sh
#
# Exits non-zero if any step fails. Each step's own exit code is captured
# immediately, so a trailing pipe or tail cannot mask a failure.
set -uo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
FAILED=""
FAIL_COUNT=0

run_step() {
    step_name="$1"
    shift
    echo ""
    echo "=== ${step_name} ==="
    "$@"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "FAIL: ${step_name} (exit ${rc})"
        FAILED="${FAILED}${step_name}; "
        FAIL_COUNT=$((FAIL_COUNT + 1))
    else
        echo "ok: ${step_name}"
    fi
}

run_step "unit tests" "$PYTHON" -m pytest -q
run_step "tool registration" "$PYTHON" scripts/check_registration.py
run_step "clean import" "$PYTHON" scripts/check_clean_import.py

echo ""
if [ "$FAIL_COUNT" -ne 0 ]; then
    echo "VERIFY FAILED (${FAIL_COUNT}): ${FAILED}"
    exit 1
fi
echo "VERIFY PASSED"
