---
description: Diagnose why Audacity is not responding - checks the plugin, the server, the pipes, and Audacity itself, then says what to do about what it finds.
disable-model-invocation: true
allowed-tools: ["Bash", "Read"]
---

# /audacity:doctor

Answer "why isn't this working" across both halves of the plugin. Run this
before anything else when a tool call fails, times out, returns an empty
result, or an export comes out wrong.

## Step 1: the plugin side

The MCP server cannot report on the environment that launched it, so check that
first:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plugin_doctor.py"
```

Read the output and note anything that is not in a good state:

- **`uv: not found`** - the server never started. This is the whole problem.
  Tell the user to install uv from https://docs.astral.sh/uv/ (or set `UV_BIN`
  to its path) and restart their MCP client. Stop here; nothing below will work.
- **`venv built: no`** - normal before the first successful launch. Harmless on
  its own, but combined with a working uv it means the server has not started
  yet this session.
- **`mod-script-pipe: disabled` / `ask` / `absent`** - Audacity will not create
  the pipes. Point the user at `/audacity:setup`.
- **`mod-script-pipe: no-config`** - no `audacity.cfg` was found at all, meaning
  Audacity has never been launched on this machine (or its config was reset).
  Tell the user to launch Audacity once so it writes its config, then run
  `/audacity:setup`.
- **`pipe (to)` or `pipe (from)` missing** - Audacity is not running, or was not
  restarted after the module was enabled.
- **`python: ... is too old`** - `python3` on this machine is older than 3.10
  (stock macOS still ships 3.9), and there was no plugin venv and no uv to
  re-run under. The install is fine; the interpreter is not. Tell the user to
  install uv from https://docs.astral.sh/uv/ - that also fixes `uv: not found`
  above, and the server needs it anyway. Everything below this line in the
  report will say `unknown` or `unavailable`, so treat none of it as a finding.
- **`pipe and config info: unavailable`** - the report could not get far enough
  to check the pipes, and nothing below it is trustworthy. Two causes, and the
  detail in the parentheses says which:
  - `(see the python line above)` - the old-interpreter case above. Fix that,
    not the install.
  - anything else - the plugin's own files are broken. Reinstall the plugin.

## Step 2: the server side

If the plugin side looks healthy, call the `audacity_health_check` tool. It
reports both pipes with their ages, whether a round trip actually answers, which
copy of the client is imported, and the default project sample rate.

Note the distinction it draws: the pipe files outlive Audacity, so their
existence proves nothing on its own. A stale pair from a previous launch looks
exactly like a live one until the round trip fails.

## Step 3: report

Give the user one short summary: what is wrong, which layer owns it, and the one
next action. Do not list every check that passed.

If everything passes and the user still has a problem, the likely causes are:

- Audacity restarted while this session was running, so the server is holding
  dead handles. MCP servers start with the client session, so this is fixed by
  restarting the MCP client, not Audacity.
- The command being attempted is one of the known Audacity misbehaviours: check
  `CLAUDE.md` in the repo root for the current list.
