# Design: ship audacity-mcp-max as a Claude Code plugin

**Date:** 2026-07-24
**Status:** Approved, not yet implemented
**Scope:** Milestone 0 of a five-part roadmap (see [Roadmap context](#roadmap-context))

## Problem

Installing audacity-mcp-max today means three separate acts of faith: get the
Python package in (past PEP 668 on Ubuntu, past brew-vs-system Python on macOS),
enable mod-script-pipe by hand, and hand-write MCP client config. Each has its
own failure mode, and all of them surface as the same symptom - the AI says
Audacity is not running.

The Python server is not the problem; it is 132 tools across 11 modules with a
pipe layer that has been debugged hard. What is missing is a way to install and
operate it that does not require knowing any of the above.

## Goal

The plugin becomes the product. `audacity-mcp-max` keeps its Python identity -
importable, pip-installable, unchanged import paths - and gains a plugin
identity at the same repo root, so a user installs one thing and gets a working
server, a setup path for the parts people actually get wrong, and a diagnostic
for when it still does not work.

## Architecture

One repository, two products:

```
audacity-mcp-max/
├── .claude-plugin/plugin.json     # name, version, author, license, homepage
├── .mcp.json                      # one stdio server -> scripts/launch-mcp.sh
├── commands/                      # /audacity:setup, /audacity:doctor
├── scripts/launch-mcp.sh          # resolves uv, execs the server
├── audacity_mcp/                  # unchanged
├── audacity_mcp_shared/           # gains environment.py
├── tests/                         # gains launcher + environment tests
└── pyproject.toml                 # transcription becomes an extra
```

### Launch path

`.mcp.json` declares a single stdio server whose `command` is
`${CLAUDE_PLUGIN_ROOT}/scripts/launch-mcp.sh`. The script:

1. Resolves `uv` in order: `$UV_BIN`, then `PATH`, then the known install
   locations (`~/.local/bin/uv`, `~/.cargo/bin/uv`, `/opt/homebrew/bin/uv`).
   The order matters. An MCP host does not necessarily start a server with the
   user's login PATH, and `uv` installs to `~/.local/bin` by default - a
   `command -v uv` check alone reports "not found" for a uv that is present.
2. Exits 127 with the install URL on **stderr** if it finds nothing.
3. `exec`s `uv run --directory "$CLAUDE_PLUGIN_ROOT" audacity-mcp-max`.

`uv run` builds the venv from `pyproject.toml` on first launch and reuses it
after, so there is no pip step and no PEP 668 wall.

Everything the launcher prints goes to stderr. stdout is the JSON-RPC channel;
a friendly English error written there corrupts the protocol rather than
explaining anything.

### Why one repo

The plugin's cache directory *is* the Python project, so "which copy is
installed" stops being a question - what `uv` runs is what the marketplace `ref`
pinned. That removes the failure documented in `FORK-NOTES.md`, where a stale
PyPI wheel shadowed the checkout and presented as a protocol desync rather than
a packaging problem.

That covers the *code*, but not the dependency graph on its own: `mcp[cli]` is
constrained as `>=1.0.0`, so without a lockfile `uv sync` could still resolve a
different `mcp` (and its transitive dependencies) on two machines running the
same pinned `ref`. `uv.lock` is committed for exactly this reason - it is the
one file that makes "the code that runs is the code the ref pinned" true for
dependency versions too, not just for this repository's own source.

### Versioning

`plugin.json.version` and `pyproject.toml` version are the same number, asserted
by `verify.sh`. One tag per release; the marketplace entry pins that tag.

### Naming

Plugin name `audacity`, so commands read `/audacity:setup` and
`/audacity:doctor`. The distribution name stays `audacity-mcp-max`.

## Commands

Both are user-invoked only (`disable-model-invocation: true`): one writes to
Audacity's config, and the other is diagnostic noise if it fires on its own.

### `/audacity:setup`

Detects first, writes only on confirmation. One pass reports:

| Check | Why it is here |
|---|---|
| `uv` resolvable | Catching it here beats a cryptic MCP startup failure |
| `audacity.cfg` located | Platform paths, plus the Snap path |
| `mod-script-pipe` state | `=1` enabled, `=0`/`=2` off-or-ask, or absent |
| Audacity running right now | See below |
| `DefaultProjectSampleRate` | An exotic rate breaks playback and exports in a way that reads as a server bug |
| Transcription extra present | Offers `uv sync --extra transcription` |
| Pipe state | Ends by calling the same health check `/audacity:doctor` uses |

**The running check is a bug fix.** `install.sh` today edits `audacity.cfg`
regardless of whether Audacity is open, and Audacity rewrites that file on quit.
So the installer can report success and have the setting reverted the next time
the user quits. Setup refuses to write while Audacity is running and says why.
`install.sh` gets the same guard.

### `/audacity:doctor`

Wraps `audacity_health_check` and adds what the server cannot see about itself:
plugin version, which `uv` resolved, whether the venv is built, whether the
transcription extra is present, and the pipe directory actually in use
(including a Snap `/proc/<pid>/root/tmp` path).

### Where the logic lives

`audacity_mcp_shared/environment.py`, stdlib-only like the rest of that package,
so it is importable from a bare Python and testable in `verify.sh`. Both the
setup command and the health-check tool use it, which consolidates config-path
knowledge that currently exists in three places.

## Dependencies and failure behavior

`faster-whisper` moves from `dependencies` to
`[project.optional-dependencies] transcription`. Base install is `mcp[cli]`
alone.

The degradation path already exists: `transcription_tools.py` imports
`faster_whisper` only inside functions, behind `_check_whisper_installed()`,
which raises a clean `AudacityMCPError`. The 7 transcription tools continue to
register and remain self-describing; they fail at call time with that error,
reworded from `pip install faster-whisper` to name the fix a plugin user needs:
`/audacity:setup --transcription`.

Each failure names the layer that owns it:

- **`uv` missing** - launcher exits 127 with the install URL on stderr; the host
  shows a failed server rather than a hung one.
- **venv not built** - `uv run` builds it on first launch; the launcher notes
  this on stderr so a slow first connect is not mysterious.
- **Audacity not running, or module off** - unchanged `PIPE_NOT_FOUND` with the
  Preferences path.
- **Snap private `/tmp`** - handled transparently by pipe discovery.
- **Transcription not installed** - the existing error, pointing at setup.
- **Config write refused** - setup declines while Audacity is open and explains
  that the setting would be reverted on quit.

**Behavior change to document.** A plain `pip install audacity-mcp-max` stops
including transcription. This goes in `CHANGELOG.md` under `Changed`, with
`pip install "audacity-mcp-max[transcription]"` spelled out.

## Verification

`verify.sh` gains four steps, all offline, none requiring Audacity or `uv`:

1. **Version parity** - `plugin.json.version` equals `pyproject.toml` version.
2. **Manifest sanity** - both JSON files parse, and the `command` path in
   `.mcp.json` exists and is executable. A typo there surfaces as a dead server
   at session start, hours away from the edit.
3. **Launcher behavior, executed rather than linted.** Two tests in the spirit
   of the existing relay stub:
   - point the script at a temp dir holding a fake `uv` that records its argv,
     and assert it execs `uv run --directory <plugin root> audacity-mcp-max`;
   - run it with `uv` absent from every search location and assert exit 127 with
     the message on stderr and **nothing on stdout**.
4. **`environment.py` unit tests** - config-path resolution per platform
   including Snap, `mod-script-pipe` state parsing across `=0`/`=1`/`=2`/absent,
   and the Audacity-is-running check.

New guards get the usual mutation check: break each in turn, confirm exactly the
intended test fails, and treat a zero-failure row as dead code or an unguarded
rule rather than a pass.

## Shipping

One tag `v0.3.0` on `ringo380/audacity-mcp-max`, a GitHub release, then a
marketplace entry in `robworks-claude-code-plugins` pinning that ref.

Three things need care:

- **License is Apache-2.0, not MIT.** The catalog derives licensing from
  `marketplace.json`, and `build-site.js` fails the build if blanket
  "every plugin is MIT" copy survives alongside a non-MIT entry. `sharding`
  (BUSL-1.1) already forced that path, but the plugins OG card and FAQ copy may
  need regenerating now that a second non-MIT plugin exists.
- **Attribution.** The repo is dual-authored (Daniel Hodgetts upstream, Ryan
  Robson). Apache-2.0 requires that attribution survive. `plugin.json` uses the
  catalog convention `Ryan Robson and Robworks Software LLC`; upstream's credit
  stays where it already lives, in `pyproject.toml`, `README.md`, and
  `FORK-NOTES.md`.
- **Public repo.** No AI-authorship traces in the manifest, commands, README,
  commit messages, or the marketplace PR.

`install.sh` and `install.bat` remain for people who want the server without the
plugin, but stop being the recommended path in the README.

## Deliberately excluded

- **No `SessionStart` hook.** A hook that pings for Audacity every session is
  noise in the sessions that are not audio work, and per-session hook spam is
  hard to switch off once shipped. `/audacity:doctor` covers it on demand.
- **No skills yet.** No `skills/` directory is created - git cannot track an
  empty one, and a placeholder file would ship as a component that does nothing.
  Judgment content belongs to milestone C, where it can be written against
  pipelines whose behavior is measurable.
- **No new audio capability.** This milestone changes packaging and setup only.

## Roadmap context

This is milestone 0 of five. The later ones each get their own design and plan:

| | Milestone | Rests on |
|---|---|---|
| 0 | Plugin shell - install, setup, doctor, ship | - |
| A | Measurement and verification core - real-audio fixtures, numeric proof a chain did what it claimed, honest no-op reporting | 0 |
| B | Work Audacity cannot do - ffmpeg/librosa offline, batch, stems, headless | A |
| C | Judgment layer - skills for which tool, what target, what order | A |
| D | End-to-end deliverables - recording to finished, chaptered, documented episode | B, C |

A and B share machinery: verifying a chain hit -16 LUFS and measuring loudness
without Audacity open are the same code. C is cheap to write and worthless
before A, because prose about when to compress is only as good as the pipeline's
ability to prove what it did.
