#!/bin/sh
# audacity-mcp-max plugin launcher.
#
# Runs the bundled MCP server via `uv`, resolving the virtualenv inside this
# plugin's own directory. The server speaks stdio JSON-RPC to the MCP host and
# talks to Audacity over mod-script-pipe.
#
# Every diagnostic here goes to stderr. stdout is the JSON-RPC channel: a
# friendly English error written there corrupts the protocol instead of
# explaining anything.
set -u

# Set by Claude Code when it invokes plugin scripts. The fallback keeps the
# launcher usable from a manual invocation or a different MCP host, and it
# resolves from the script's own location rather than the cwd, which the host
# chooses and we do not control.
: "${CLAUDE_PLUGIN_ROOT:=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)}"

# Absolute places uv installs itself. Overridable so the test suite can point
# the search somewhere controlled instead of depending on what this machine
# happens to have.
: "${AUDACITY_MCP_UV_SEARCH:=$HOME/.local/bin/uv:$HOME/.cargo/bin/uv:/opt/homebrew/bin/uv:/usr/local/bin/uv}"

find_uv() {
    # An explicit override wins, so a user with several uv installs can pick.
    if [ -n "${UV_BIN:-}" ] && [ -x "$UV_BIN" ]; then
        printf '%s\n' "$UV_BIN"
        return 0
    fi
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi
    # PATH is not enough. uv installs to ~/.local/bin, and an MCP host does not
    # necessarily start its servers with the user's login PATH - so checking
    # only PATH reports "uv not found" for a uv that is present.
    IFS=':'
    for candidate in $AUDACITY_MCP_UV_SEARCH; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

UV=$(find_uv) || {
    echo "[audacity-mcp-max] Could not find 'uv', which this plugin needs to start its" 1>&2
    echo "[audacity-mcp-max] MCP server. Install it (https://docs.astral.sh/uv/), or set" 1>&2
    echo "[audacity-mcp-max] UV_BIN to its full path, then restart your MCP client." 1>&2
    exit 127
}

# `uv run` creates and syncs the venv from pyproject.toml on first launch, so
# the first connection is slow and every one after it is not.
exec "$UV" run --directory "$CLAUDE_PLUGIN_ROOT" audacity-mcp-max "$@"
