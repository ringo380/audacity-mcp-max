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
#
# `dirname "$0"` alone reports the directory of a SYMLINK to this script, not
# the directory the script actually lives in, which would point uv at the
# wrong tree entirely. Resolve symlinks by hand instead (no `readlink -f`,
# which is not portable to every `readlink` this might run under). The hop
# count is capped so a symlink cycle - or a chain longer than any real install
# would ever have - gives up instead of spinning forever.
_resolve_script_path() {
    target="$1"
    hops=0
    while [ -h "$target" ]; do
        hops=$((hops + 1))
        if [ "$hops" -gt 40 ]; then
            return 1
        fi
        link=$(readlink "$target") || return 1
        case "$link" in
            /*) target="$link" ;;
            *) target="$(dirname "$target")/$link" ;;
        esac
    done
    printf '%s\n' "$target"
}

if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    _resolved_self=$(_resolve_script_path "$0") &&
        CLAUDE_PLUGIN_ROOT=$(cd "$(dirname "$_resolved_self")/.." 2>/dev/null && pwd)
fi

# Whatever the source - unset with no usable fallback, or a fallback whose
# cd/pwd failed - running `uv run --directory ""` would quietly point uv at
# the caller's own cwd instead of refusing, so check explicitly rather than
# trusting the substitution above to have produced something.
if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    echo "[audacity-mcp-max] Could not determine the plugin's own install directory." 1>&2
    echo "[audacity-mcp-max] This launcher could not resolve its own path (a broken or" 1>&2
    echo "[audacity-mcp-max] looping symlink, or an install directory that no longer" 1>&2
    echo "[audacity-mcp-max] exists). Set CLAUDE_PLUGIN_ROOT to the plugin's install" 1>&2
    echo "[audacity-mcp-max] directory and restart your MCP client." 1>&2
    exit 1
fi

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
