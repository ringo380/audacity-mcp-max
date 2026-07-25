---
description: Guided first-run setup - check uv, locate Audacity's config, enable mod-script-pipe safely, optionally install transcription, and verify the connection.
disable-model-invocation: true
allowed-tools: ["Bash", "Read"]
---

# /audacity:setup

Take the user from "plugin installed" to "Claude is editing audio" in one pass.

Three things have to be true, and each fails differently:

1. **uv is installed** - the MCP server will not start without it.
2. **mod-script-pipe is enabled in Audacity** - it is off by default, and
   Audacity only creates the script pipes at launch.
3. **Audacity is running** - the pipes exist only while it is open.

**If the user invoked this as `/audacity:setup --transcription`, do Step 4 only
and stop.** That is the exact command the transcription tools name when the
extra is missing, so someone arriving here has a working setup already and
wants the one thing they are missing, not another pass over all of it.
**`/audacity:setup --measurement` is the same shortcut for Step 5.**

## Step 1: check uv

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plugin_doctor.py"
```

If the shell reports `python3: command not found`, or opens a Command Line Tools
install prompt instead of running anything, this machine has no `python3` at
all. That is a supported state - uv is what provides Python for this plugin - so
do not send the user to install Python: tell them to install uv from
https://docs.astral.sh/uv/, then re-run this command.

If it reports `uv: not found`, stop and tell the user to install it from
https://docs.astral.sh/uv/ (or set `UV_BIN`), then restart their MCP client.
Nothing else in this command matters until that is fixed.

## Step 2: read the current state

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/audacity_setup.py"
```

This writes nothing. Read the output and decide what is needed:

- **`audacity.cfg: not found`** - Audacity has never been run on this machine.
  Ask the user to open it once and close it, then run this command again.
- **`mod-script-pipe: enabled`** - nothing to do here, go to step 4.
- **`mod-script-pipe: ask`** - Audacity prompts on every launch and creates no
  pipes until someone clicks through. Treat it the same as disabled.
- **`mod-script-pipe: disabled` or `absent`** - go to step 3.

## Step 3: enable the module

Only if step 2 said so. Check `audacity running` in that same output first:

- **`yes`** - tell the user to fully quit Audacity (Cmd+Q, or File > Exit;
  closing the window is not enough) and say why: Audacity rewrites its config on
  quit, so a change made now is reverted when they close it, after reporting
  success. Wait for them to confirm, then continue.
- **`unknown`** - the process table could not be read. Ask the user to confirm
  Audacity is closed before continuing.

Ask for explicit confirmation before writing - this edits a file the user owns.
Then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/audacity_setup.py" --enable-module
```

It backs the file up to `audacity.cfg.bak` first and refuses to write if it
finds Audacity running, so a "yes" that turns out to be wrong is safe. Two exit
codes mean it did nothing, and they are different problems:

- **2** - it refused to write (Audacity running, or the process table could not
  be read). Read the message and follow it.
- **3** - it could not run at all under this `python3`, and found no plugin venv
  and no uv to re-run itself under. The message says so on stderr in one line.
  Install uv from https://docs.astral.sh/uv/; nothing else here works until
  then.

## Step 4: transcription (optional)

Only if the user wants local transcription. It is a large download
(ctranslate2 and onnxruntime), and the Whisper model itself is fetched on first
use.

Use the `uv:` path Step 1 printed rather than a bare `uv` - the same reason
that step exists at all is that an MCP host does not necessarily put uv on the
PATH this command runs with, so `uv sync` can fail here for exactly the user
the resolved-path lookup was for:

```bash
cd "${CLAUDE_PLUGIN_ROOT}" && "<uv path from Step 1>" sync --extra transcription
```

## Step 5: measurement (optional)

Only if the user wants loudness verification (LUFS, true-peak, target-met
checks) in pipeline reports. Without it, those fields report `null` or
`unknown` rather than a number - not a fault, just an optional extra.

Use the `uv:` path Step 1 printed rather than a bare `uv`, for the same reason
as Step 4:

```bash
cd "${CLAUDE_PLUGIN_ROOT}" && "<uv path from Step 1>" sync --extra measurement
```

## Step 6: verify

Ask the user to launch Audacity, then call the `audacity_health_check` tool.

- `healthy: true` - done. Tell them what to try first, for example "remove the
  hiss from this recording" or "master this for a podcast".
- Not healthy - read `next_steps` in the result; it names the specific fix for
  whatever it found. If the pipes are still missing after a restart with the
  module enabled, run `/audacity:doctor` for the plugin-side half.
