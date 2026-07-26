# Changelog

All notable changes to audacity-mcp-max will be documented in this file.

## [Unreleased]

### Added

- Pipelines now report what they actually did. Every `auto_` pipeline measures the audio before and after itself and returns a `measurement` block through `check_pipeline_status` with before, after, the delta, and its declared target. A run that moved nothing says so instead of returning the same completion string as a run that worked. Pass `verify=False` to skip the two exports on a very long project. (docs/design/2026-07-24-measurement-verification-core.md)
- LUFS and true peak, via a new optional `measurement` extra (`uv sync --extra measurement`). Integrated loudness follows ITU-R BS.1770-4 and is validated against the EBU Tech 3341 compliance cases rather than against itself; true peak uses 4x oversampling per Annex 2. Without the extra these read `null` and any target depending on them reports `unknown` rather than `missed` - a target that could not be checked is not a target that failed. `/audacity:doctor` reports whether the extra is present. (docs/design/2026-07-24-measurement-verification-core.md)
- `auto_analyze_audio` returns `lufs` and `true_peak_dbtp`. (docs/design/2026-07-24-measurement-verification-core.md)
- Measurement reads 24-bit PCM WAV. Audacity's export dialog offers "Signed 24-bit PCM" and its exporter is sticky, so a user who chose it once keeps producing it; that width previously failed outright and took the whole measurement with it. (docs/design/2026-07-24-measurement-verification-core.md)

### Fixed

- Measurement exports were a mono downmix of the project, so LUFS read about 3 dB off on uncorrelated stereo and a true peak could miss a clip entirely (L at +0.99 against R at -0.99 sums to about zero). Removing `NumChannels=1` did not fix it: Audacity's `ExportCommand` declares `S.Define(mnChannels, "NumChannels", 1)`, so an omitted parameter takes the same default. The channel count is now queried from the track list and sent explicitly - two whenever any track is stereo, and two whenever the query cannot be answered, since a wrong guess of mono loses a channel while a wrong guess of stereo loses nothing. (docs/design/2026-07-24-measurement-verification-core.md)
- 24-bit PCM was unreachable on Python 3.10 and 3.11. The stdlib `wave` module only learned `WAVE_FORMAT_EXTENSIBLE` in 3.12, and that is the header libsndfile most often writes for 24-bit, so on the two oldest supported interpreters the file fell through to a fallback reader that rejected everything that was not IEEE float - telling the user their PCM WAV "is not IEEE float". The fallback now reads PCM as well. (docs/design/2026-07-24-measurement-verification-core.md)
- A short final block counted as a whole one in the noise-floor percentile. Three seconds of full-scale tone followed by 50 ms of digital silence reported a noise floor of -13.5 dB and a dynamic range of 10.5 dB, against -3.0 and 0.0 for the same tone cut at three seconds exactly - 1.6% of the audio deciding a third of the answer. Short tails no longer vote; a file shorter than one block still measures. (docs/design/2026-07-24-measurement-verification-core.md)
- A measurement that could not be taken failed the whole pipeline. A verified run measures three times and `result.success` was computed from the failed-step list, so a project whose export was blocked by the metadata dialog reported `success: false` for a pipeline whose every processing step applied. Measurement failures are warnings now, and `measurement.verified` reports whether the numbers actually landed rather than echoing the `verify` argument back. (docs/design/2026-07-24-measurement-verification-core.md)
- Measuring blocked the event loop. `measure_file` is a CPU-bound pass over the whole export and a verified run makes three of them, so `check_pipeline_status` - the documented way to watch a background job - could not answer while a long project was being measured, and `current_step` sat frozen. It runs in a thread now. (docs/design/2026-07-24-measurement-verification-core.md)
- An invalid `style` or `intensity` left a pipeline permanently "running". The job was created before the preset was validated and nothing marked it errored, so a typo blocked every later pipeline with "a pipeline is already running" until the ten-minute stale timeout expired. Presets are validated before the job slot is claimed. `auto_lofi_effect` also built its job name from the un-normalised intensity, so `intensity="Medium"` produced a name no target lookup matches. (docs/design/2026-07-24-measurement-verification-core.md)
- `/audacity:setup` uninstalled the extra it had just installed. `uv sync` is exact, so running the transcription step and then the measurement step left measurement only, and the doctor reported `transcription: not installed` immediately after a successful setup. The two steps are one step that names every wanted extra at once. (docs/design/2026-07-24-measurement-verification-core.md)
- `noise_floor_db` was not a noise floor. It was the RMS of the first 0.5 seconds, which assumes the recording opens with room tone; on a file that opens with speech it read tens of dB too high, and `auto_analyze_audio`'s advice and the noise-reduction routing followed it. It is now the 10th percentile of per-block RMS across the whole file. **This changes the advice you get on existing files**, which is the point, but it is a behaviour change rather than an addition. (docs/design/2026-07-24-measurement-verification-core.md)
- Pipelines did not stop transport before running. Audacity refuses scripted commands while playing or paused and simply does not reply, so a pipeline started during playback produced the exact signature of a dead pipe. Every pipeline now issues `Stop` first, which is idempotent. (docs/design/2026-07-24-measurement-verification-core.md)
- An export that never produced a file blamed the pipe. The export metadata editor is modal, and while it is open Audacity does not reply, so the most common cause of a "hung" export was reported as a connection failure. Measurement failures now name the dialog as a possible cause rather than asserting a broken pipe. (docs/design/2026-07-24-measurement-verification-core.md)
- `.mcp.json` is now tracked (see Added, below), which has two consequences worth knowing about. First, anyone who already had a clone from before this change and had let `.mcp.json` sit untracked (the old `.gitignore` entry called it "Local MCP config (contains user-specific paths)") will hit "untracked working tree file would be overwritten by merge" on their next pull - remove or stash the local file before pulling. Second, opening this repo directly in Claude Code (not through the plugin) now offers a project-scoped `audacity` MCP server whose `command` is the literal, unexpanded string `${CLAUDE_PLUGIN_ROOT}/scripts/launch-mcp.sh` - that variable is only set inside plugin scope, so the server fails to start from a plain repo checkout. Use the plugin install path (or one of the other MCP client configs in `docs/INSTALLATION.md`) instead of accepting that project-scoped server as-is.
- The plugin's own commands ran under an interpreter that cannot import the plugin. `commands/setup.md` and `commands/doctor.md` invoke `python3`, which on stock macOS is 3.9.6, while `audacity_mcp_shared/constants.py` needs 3.10 - so Step 2 of `/audacity:setup` died with a `TypeError` out of `constants.py`, and the doctor printed `pipe and config info: unavailable (unsupported operand type(s) for |)`, a line `commands/doctor.md` told the assistant meant a broken install. That is the worst possible place for it: the plugin's pitch is that uv handles Python, so the target user is exactly the one with no modern Python on PATH. Both scripts now re-run themselves under the plugin's `.venv` interpreter, or under `uv run`, before importing anything (`scripts/plugin_bootstrap.py`, which is stdlib-only and 3.8-valid syntax because it runs before anything is known to work). Where neither exists the doctor continues in degraded mode and keeps its always-exit-0 contract, and setup prints one line and exits 3; both say plainly that this is an old interpreter and not a broken install, and name the version they found. `commands/doctor.md` documents the case. (docs/design/2026-07-24-plugin-packaging.md)
- The re-exec above could still take the doctor's always-exit-0 contract down with it. `os.execv` is a point of no return: once it succeeds the child's exit code is the doctor's, so a `.venv` whose base interpreter had been removed or upgraded printed `dyld: Library not loaded: libpython` and exited 1 with no report at all, and a `uv run` that could not sync - offline on a first run, a lock that no longer matched `pyproject.toml`, a read-only plugin directory - did the same with exit 2. Both candidates are now probed first (run with a one-line argument that exits 0 only on a usable 3.10+ interpreter) and a failure falls through to the next candidate and then to the degraded report, so a dead venv is now the case uv rescues rather than a fatal one. The probe is bounded at 60s because an MCP host kills the Bash call these run under after a couple of minutes: answering "could not tell" late is recoverable, hanging is not. `uv run` also gets `--frozen`, so a read-only diagnostic cannot rewrite the now-tracked `uv.lock`, and the script path is made absolute because `uv run --directory` changes the working directory. `commands/setup.md` documents exit 3 alongside exit 2, and both command files say what to do on a machine with no `python3` at all. (docs/design/2026-07-24-plugin-packaging.md)
- `install.bat` enabled `mod-script-pipe` without checking whether Audacity was running, the bug `install.sh` had fixed one release note above. It now asks `tasklist`, refuses while Audacity is open, and - matching the policy `install.sh` adopted - also refuses when the check itself cannot be run, rather than reading silence as "not running". Windows is where this matters most: the plugin's launcher is a POSIX shell script, so `install.bat` is the whole install path there. (docs/design/2026-07-24-plugin-packaging.md)
- The transcription-extra check probed the wrong interpreter. The extra is installed by `uv sync --extra transcription` into the plugin's `.venv`, but both scripts did a plain `import faster_whisper` in whatever interpreter was running, so a user who had just completed Step 4 of `/audacity:setup` was told forever that they had not. The check now runs in the venv when that is not the current interpreter, and reports `unknown` rather than asserting `not installed` when it cannot tell. A broken native dependency - ctranslate2 or onnxruntime failing its `dlopen` - raises `OSError` rather than `ImportError`, which escaped the doctor's catch and ended the report before the pipe section; that is `unknown` now too, which closes the remaining hole in the always-exit-0 contract. `scripts/audacity_setup.py` also wraps its report, so an unexpected failure is one readable line instead of a traceback. (docs/design/2026-07-24-plugin-packaging.md)
- `audacity_is_running()` always returned False on Windows, and a failed probe read as "not running". A `tasklist` row is `audacity.exe    6244 Console    1    92,116 K`, so the basename of the whole row never equalled `audacity.exe` - the function reported "positively closed" while Audacity was open, silently reintroducing the revert-on-quit bug this milestone exists to fix. It now compares the first field on Windows and the intact line on POSIX (where a comm may contain spaces, and where a substring test would match this server's own `audacity-mcp-max` process). Separately, a probe that runs but exits non-zero with no output - busybox `ps` without `-Ao comm=`, a restricted container - now returns `None`, which callers already treat as a refusal to write, rather than falling through to False. The Windows branch had no test at all; it does now, including the `INFO: No tasks are running` banner. (docs/design/2026-07-24-plugin-packaging.md)
- `scripts/plugin_doctor.py` could exit non-zero. The pipe and config section at the end of `main()` was not guarded like the sections before it, so a broken or partial install - the exact situation the doctor exists to diagnose - could make an import raise and the process exit with a traceback instead of a report. That section is now wrapped the same way, and `commands/doctor.md` documents the `mod-script-pipe: no-config` state (no `audacity.cfg` found at all) alongside the others. (docs/design/2026-07-24-plugin-packaging.md)
- Shutdown never closed the pipes. `atexit` was handed the async `close()`, so it built a coroutine and discarded it. `AudacityClient.close_sync()` is the sync entry point and is what `atexit` gets now. (#3)
- `_safe_path` rejected the system temp directory. On macOS `$TMPDIR` resolves under `/private/var`, which the POSIX blocklist covers, so every export aimed at a temp path failed with a misleading "system directory" message. Temp paths are allowed ahead of the blocklist; the blocklist is otherwise unchanged. (#13)
- `project_export_audio` exported only the current selection. `Export2` acts on the selection, and nothing established one first, so a 78.7s project could produce a 3.8s file reported as a successful export. It now selects every track and the whole timeline first; pass `whole_project=False` for the old behaviour. (#6)
- `project_export_audio` reported whatever Audacity said. It now reads the written file back and returns a `verified` block — the container actually present, sample rate, channels, duration, size — and warns when that does not match what was asked for: AIFF data in a `.wav` path (Audacity reuses the last exporter and the extension does not override it), a channel count that does not match, a render at an unplayable project sample rate, or no file at all. (#9)
- The analyzer could not read its own exports. `_measure_wav` used `wave.open`, which rejects the AIFF that Audacity writes into a `.wav` path, and the failure surfaced as "could not parse audio data". Both containers are now read directly (`aifc` is gone from the stdlib as of Python 3.13), and a genuinely unreadable export names the container it found. (#4)
- A pipeline whose measurement failed recorded "loudness skipped" as an *applied* step, so a run that never touched loudness reported unqualified success. It now lands in the failed steps, which surfaces as a warning on the job result. (#4)
- `project_get_info(info_type="Commands")` is blocked. On Audacity 3.7.8 it pegs the CPU, never answers, and takes the application down with no autosave recovery — reachable just by enumerating the documented `info_type` values. The error says why. (#7)
- `track_mute` and `track_set_properties(mute=..., solo=...)` reported success while doing nothing. Audacity 3.7.8 accepts `SetTrackStatus` with `Mute`/`Solo` and leaves the flag alone (`Name` on the same command does apply, so the targeting is fine). Both tools now read the track back and return a warning naming the flags that did not change, with the workaround. The Audacity-side behaviour is unchanged — this converts a silent no-op into something a caller can branch on. (#8)
- Starting a cleanup pipeline while a transcription was running crashed with `RuntimeError: coroutine raised StopIteration`. The "already running" error scanned only the pipeline job store, so a transcription conflict fell off the end of a bare `next()`. The conflicting job is now identified inside `_create_job` while it still holds the lock, covering both stores and closing the race where the blocking job finishes before the caller re-scans. (#10)
- Resolved the Windows backslash question: doubling every backslash in `_quote_value` is correct, not a bug. Audacity parses a value with `wxCmdLineParser::ConvertStringToArgs` (DOS mode, which never consumes a backslash) then `CommandParameters::Unescape`, whose `\\` -> `\` rule is the exact inverse of the doubling. Verified against a live Audacity 3.7.8: the doubled path arrives intact, a single-backslash path arrives corrupted (a UNC `\\server\share` loses its leading pair). The `xfail(strict=True)` test that expected doubling to be wrong is replaced with assertions of the correct, verified behaviour. There is one path Audacity mangles no matter what the client sends - a segment beginning with `n` (e.g. `C:\new`), because `Unescape` turns `\n` into a newline before collapsing `\\`; `project_import_audio` and `project_export_audio` now warn when a path contains that sequence, since no escaping can save it. (#5)
- Closing the FROM pipe while Audacity's relay was still writing a reply killed Audacity with SIGPIPE, destroying unsaved work. The relay (`PipeServer.cpp`) writes each reply with an unprotected `fwrite`/`fflush` and does not ignore SIGPIPE, so our per-command and between-retry closes of the read end terminated the whole application whenever one landed mid-reply — reachable from ordinary use, not just error paths, and confirmed by a `launchd` "exited due to SIGPIPE" log. POSIX teardown is now graceful: close the write end first so the relay's read loop ends, then drain the read end to EOF (which also unblocks a relay stuck writing a reply larger than the pipe buffer) before closing it, so the relay finishes and closes its own end first. Bounded by `Timeouts.PIPE_DRAIN`; a hung relay falls back to the hard close. Reproduced and regression-tested offline with a relay stub that restores SIGPIPE to its default disposition — no Windows or running Audacity needed. (#19)
- A timed-out command left its worker thread running on the pipe. The send loop ran in an executor under `asyncio.wait_for`, and a thread cannot be cancelled: when the caller gave up, the thread kept working through its remaining attempts, each able to sit in a read gate for the full `PIPE_READ`, while the event loop closed the fds it was still using and released the lock so the next command could start writing into the same pipe. Measured against a silent relay: a 1s command left a thread busy for 52s, and the process took that long to exit. The loop now carries the caller's deadline and stops itself (1.0s for the same case), the caller waits for it to unwind instead of closing its fds, and a worker that will not stop is reported to the next command rather than being written over. (#18)
- A missing FIFO on POSIX reported `PIPE_OPEN_FAILED` with a bare errno string. It now reports `PIPE_NOT_FOUND` with the same "is mod-script-pipe enabled" guidance the Windows path already gave.
- `install.sh` enabled `mod-script-pipe` without checking whether Audacity was running. Audacity rewrites `audacity.cfg` when it quits, so the change was reverted the next time the user closed the app - and the installer reported success on the way. It now refuses to write while Audacity is running, and says why. (docs/design/2026-07-24-plugin-packaging.md)

### Added

- The server ships as a Claude Code plugin. `.claude-plugin/plugin.json` and `.mcp.json` sit at the repo root, and the MCP server starts through `scripts/launch-mcp.sh`, which resolves `uv` itself (`UV_BIN`, then `PATH`, then the places uv actually installs to) before running `uv run --directory $CLAUDE_PLUGIN_ROOT`. That removes the pip step, the PATH problem, and the PEP 668 "externally managed environment" wall on Debian-family systems in one move, and it means the code that runs is the code the marketplace ref pinned rather than whatever a stale wheel left behind. Adds `/audacity:setup` and `/audacity:doctor`. (docs/design/2026-07-24-plugin-packaging.md)
- Snap-packaged Audacity is found without any manual setup. A Snap runs in its own mount namespace with a private `/tmp`, so `/tmp/audacity_script_pipe.*` never appears on the host and the server looked exactly like "Audacity is not running" - on Ubuntu, where Snap is the default install, this made the whole thing unusable and the install script fail silently. The POSIX open path now locates the pipes under `/proc/<pid>/root/tmp` when the plain ones are absent, matching on a process whose `comm` is `audacity` and which actually holds both FIFOs for this uid. Discovery runs per open rather than once at import, because the MCP server starts with the client session - usually before Audacity - and because an Audacity restart changes the pid. `AUDACITY_PIPE_DIR` overrides the directory outright for any layout that cannot be worked out this way (Flatpak, containers), and when it is set nothing is auto-detected. `audacity_health_check` reports the resolved paths and, on Linux, says what to do about a Snap. (upstream #7)
- `audacity_health_check`: one call that answers "is mod-script-pipe actually live right now". Reports each script pipe separately with its age (the files outlive Audacity, so existence proves nothing), whether a round trip answers, which copy of the client is imported, and the default project sample rate — plus the specific next step for whatever it found. The round trip uses a 5s timeout rather than the 30s command timeout, since it is run precisely when nothing is answering. (#15)

### Changed

- The repository moved to `robworks-code/audacity-mcp-max`. GitHub redirects the old `ringo380/audacity-mcp-max` URLs, so existing clones, `pip install git+https://...` commands and bookmarks keep working, but the canonical home is the organisation now and every URL in the docs, the installers and the plugin manifest points there. Update an existing clone with `git remote set-url origin https://github.com/robworks-code/audacity-mcp-max.git`. The earlier changelog entry naming the old path is left as it was written - it records where the 0.2.0 release was actually distributed from.
- `faster-whisper` is an optional extra rather than a base dependency. A plain `pip install audacity-mcp-max` no longer pulls ctranslate2 and onnxruntime, which together are a large download that a user who only wants to clean up a recording never needed. The 7 transcription tools still register and describe themselves; calling one without the extra raises a clean error naming the fix. To keep transcription: `pip install "audacity-mcp-max[transcription]"`, or run `/audacity:setup --transcription` in Claude Code. (docs/design/2026-07-24-plugin-packaging.md)
- The send retry loop is no longer POSIX-only. `_send_raw` retries on every platform and only `_send_attempt` branches: POSIX reopens per attempt, Windows keeps its named-pipe handles and reopens after a failed one. Windows previously got a single attempt with no retry on an empty or truncated read. The Win32 primitives under the loop have now been executed against a stand-in for Audacity's own relay — see `tests/win32_probe` for what that establishes and what it does not. (#11)
- `_win32_send_raw` no longer closes the handles itself when a write or read fails. The retry loop already drops both ends after any attempt that produced no usable reply, so the extra close could not change any outcome — no probe scenario could tell the two versions apart — and the comment explaining that the send function was what made the next attempt reopen was wrong. (#11)
- `execute_long` is now a thin wrapper around `execute` with the long timeout, instead of a second copy of the same 40 lines. Call sites are unchanged. (#12)
- Removed constants nothing referenced: `Timeouts.PIPE_WRITE`, and the `PIPE_DISCONNECTED`, `COMMAND_NOT_FOUND`, `COMMAND_TIMEOUT`, `COMMAND_REJECTED` and `MISSING_PARAMETER` error codes. Their numbers are recorded in `ErrorCode`'s docstring so a code that becomes useful later can return at its old value, and a test now fails if a member nothing raises reappears. (#14)
- `ALLOWED_SAMPLE_RATES` was an unused list that duplicated no check; it is now `COMMON_SAMPLE_RATES` and `track_resample` attaches a warning when the requested rate is outside it. The 1-384000 Hz range check is unchanged, so nothing that worked before is rejected — but the rate that later makes Audacity fail to open the sound device is called out at the point it is set. (#14)

### Testing

- The bootstrap's exec-loop guard had a test that could not fail. It asserted on `os.environ` after `reexec_if_old` returned, but the real `os.execv` never returns, so it checked a state production never reaches - moving the guard assignment below the exec (unreachable in production, and a fork bomb on a too-old venv python) left the whole suite green. The fake exec now records the variable as the child would inherit it. The bootstrap tests also leaked that variable into the pytest process, where `tests/test_plugin_doctor.py`'s subprocesses inherited a silently disarmed bootstrap; the autouse fixture restores it now. Added coverage for the re-exec path itself - the shipped `plugin_doctor.py` run under a real old `python3` against a copied plugin root and a stub `uv`, asserting the arguments it is invoked with - and for a broken venv and a failing `uv` each still producing a full report and exit 0.
- Repaired the test suite: patch targets still pointed at the pre-rename `server.*` modules, the POSIX pipe tests mocked `builtins.open` while the client had moved to `os.open`, and three "validation" tests asserted tautologies instead of calling anything. (#1)
- Tests no longer touch the real FIFOs at `/tmp/audacity_script_pipe.*`. An autouse fixture repoints the pipe paths per test, so the outcome no longer depends on whether Audacity happens to be running. Suite runtime went from ~41s to under 1s.
- Added `scripts/verify.sh`: unit tests, per-module tool registration, and a warning-free import of `audacity_mcp.main`, in one command with a real exit code. (#2)
- Added `tests/win32_probe`: a named-pipe relay stub translated from the WIN32 branch of Audacity's `PipeServer.cpp`, and six scenarios that drive the real client against it — handle reuse across commands, recovery from a mid-cycle hangup, recovery from a reply with no terminator, and an inverted control asserting a one-attempt client cannot survive the hangup. One process per scenario, because a relay thread blocked in `ConnectNamedPipe` cannot be stopped and a stale server would be credited with the next scenario's connections. Runs under CrossOver/Wine on macOS, or natively on Windows; not part of `scripts/verify.sh`. (#11)

## [0.2.0] - 2026-07-23

### Renamed

- The project is now **audacity-mcp-max**, distributed from `ringo380/audacity-mcp-max`. The distribution name, console script, and MCP server identity all changed; the Python import paths (`audacity_mcp`, `audacity_mcp_shared`) are unchanged.
- Install is from git rather than PyPI. The PyPI `audacity-mcp` package is upstream's build and at 0.1.8 it carried a pre-fix pipe layer that reports every response one call late.
- Version moved off 0.1.8 so it no longer collides with that PyPI release.

### Pipe layer

- `_posix_open_pipes` opens FROM before TO with `O_NONBLOCK` and keeps raw integer fds. Audacity's relay opens its write end first and blocks for a reader, so the reverse order races it and yields immediate empty reads; a buffered reader's readahead drops the reply outright.
- `_send_raw` retries up to six times, opening fresh and tearing our ends down between attempts. The relay closes both FIFO ends after every command cycle, so a single attempt succeeds only about half the time by design.

## [0.1.7] - 2026-04-10

### macOS / Linux Compatibility

- **Fixed macOS import crash**: `ctypes.wintypes.HANDLE` type annotation was evaluated at class definition time on all platforms, causing `NameError` on macOS/Linux. Fixed with `from __future__ import annotations` for lazy evaluation.
- **Fixed cross-platform CUDA detection**: `_cuda_is_available()` hardcoded Windows DLL (`cublas64_12.dll`). Now uses `torch.cuda.is_available()` with platform-specific fallbacks (returns `False` on macOS, checks `libcublas.so` on Linux).
- **Fixed macOS pipe paths**: Merged community PR — pipes now use `os.getuid()` for correct user-specific paths instead of hardcoded UID 0.
- **Added macOS/Linux system directory protection**: `_safe_path()` now blocks `/System`, `/Library`, `/usr`, `/bin`, `/sbin`, `/etc`, `/var` on Unix systems. Previously only blocked Windows system directories.
- **Fixed path comparison**: Replaced `.lower()` with `os.path.normcase()` for correct case handling on all platforms.
- **Updated docstrings**: Export path examples changed from Windows-only (`C:\Users\Name\Music`) to platform-neutral (`~/Music`) format.

### Memory Leaks Fixed

- **Whisper model GPU/CPU memory leak**: When switching model sizes (e.g., `large-v3` → `small`), the old model was replaced but never explicitly freed. CUDA/CTranslate2 held references preventing garbage collection. Now explicitly `del`s the old model and calls `gc.collect()` before loading a new one.
- **Job dict memory growth**: Completed job cleanup (`_cleanup_stale_jobs`) only ran when creating new jobs. If 100+ jobs completed without new ones starting, all remained in memory. Now also runs on every `check_pipeline_status` / `check_transcription_status` call.

### Race Conditions Fixed

- **`transcription_set_model` bypassed job lock**: Created jobs and wrote to `_jobs` dict without acquiring `_job_lock`, risking corruption if called simultaneously with `_start_transcription`. Now properly acquires the lock.
- **Pipeline/transcription interleaving**: A running transcription didn't block starting a pipeline (and vice versa). Both send commands to the same Audacity pipe — interleaved commands could corrupt Audacity state. Now cross-check each other before starting.
- **Stale background tasks kept running**: `_cleanup_stale_jobs()` marked timed-out jobs as errored but the `asyncio.Task` continued executing. Now stores task references and calls `task.cancel()` on timeout.

### Pipe Reliability

- **Handle leak on partial pipe open**: If the first pipe opened but the second failed, the first handle was leaked. Now calls `_close_pipes()` in all error paths.
- **No shutdown cleanup**: Pipe handles (especially Windows kernel handles) were never released on server exit. Added `atexit.register(client.close)`.
- **POSIX pipes could hang forever**: `readline()` blocked with no timeout. If Audacity crashed mid-response, the server thread hung permanently (even `asyncio` cancellation can't interrupt OS-level blocking reads). Now uses `select.select()` with configurable timeout.
- **Backslash escaping in pipe protocol**: `_quote_value()` escaped `"` but not `\`. A path like `C:\new\test` could have `\n` and `\t` misinterpreted. Now escapes backslashes before quotes.

### Other Fixes

- **Temp file race on Windows**: Transcription used `NamedTemporaryFile` which on Windows creates then immediately closes a file — another process could grab the same path. Now uses UUID-based paths (matching the pattern already used in cleanup pipelines).

## [0.1.4] - 2026-03-16

### Easy Setup

- **One-click installer**: Added `install.bat` (Windows) and `install.sh` (macOS/Linux) that automatically install from PyPI and configure Claude Desktop — no git clone, no manual JSON editing.
- **`pip install audacity-mcp`** is now the primary install method (was previously git clone + `pip install -e .`).
- **README rewritten** to lead with one-click install and `pip install` from PyPI. Manual git clone steps moved to a collapsible section.
- **Installation guide updated**: Three clear options — one-click (easiest), pip install (recommended), from source (developers).

### Documentation

- Fixed tool counts in README: updated from 96 to 131 tools across all categories.
- Fixed test count in project structure: updated from 40 to 60 tests.
- Updated all references from `pip install -e .` to `pip install audacity-mcp`.

## [0.1.3] - 2026-03-15

### Added

- Added 32 new tools (99 → 131 total) across effects, editing, tracks, selection, transcription, and labels
- Fixed pipeline settings
- Live-tested on production audio

## [0.1.1] - 2026-03-15

### Security

- **Path traversal protection**: All file paths are now canonicalized with `os.path.realpath()` before use, preventing `../` traversal attacks. System directories (Windows, Program Files) are blocked.
- **Command injection hardening**: Fixed `_quote_value()` in pipe protocol to properly escape embedded double quotes, preventing malformed commands from reaching Audacity.
- **File overwrite protection**: Export tools (audio, labels, sample data, transcription) now refuse to overwrite existing files, preventing accidental data loss from AI-hallucinated paths.

### Bug Fixes

- **Memory leak**: Pipeline and transcription job stores (`_jobs` dicts) now cap at 50 completed entries and evict oldest automatically. Previously grew unbounded for the lifetime of the server process.
- **Stale job timeout**: Added 10-minute timeout to cleanup pipelines (was only in transcription). Stuck pipelines no longer block all future pipeline runs forever.
- **Race condition**: Pipeline and transcription job creation now uses `asyncio.Lock` to prevent near-simultaneous MCP calls from bypassing the concurrent-run check and starting two pipelines at once.
- **Temp file collision**: Analysis WAV files now use unique filenames (`uuid` suffix) instead of a fixed path, preventing data corruption if multiple server instances run simultaneously.
- **Removed `wma` from allowed export formats** — Audacity doesn't natively support WMA export; including it caused confusing errors.
- **`select_zero_crossing` called wrong command**: Was calling `SnapToOff` (disables snapping) instead of `ZeroCross` (find zero crossings). Users thought they were snapping to zero crossings but were actually turning snapping off.
- **`auto_analyze_audio` track info never parsed**: `GetInfo` returns JSON in the message field but code expected it in `data` dict. Track count and metadata were always empty. Now properly parses the JSON response.
- **Transcription export missing `SelAllTracks`**: `Export2` requires both track and time selection. Transcription only called `SelectAll` (time) but not `SelAllTracks`, which could cause incomplete exports on multi-track projects.
- **`parse_response` overwrote error messages**: When Audacity returned an error message followed by `BatchCommand finished: Failed!`, the batch line overwrote the actual error text. Error details are now preserved.
- **`effect_amplify` accepted ratio=0**: A ratio of 0 silences audio entirely. Now rejects values <= 0.
- **`check_pipeline_status` deleted other jobs**: Querying one completed job triggered cleanup that could delete other users' job results. Job eviction now only happens during `_create_job`.

### Validation

- **Effect parameter validation**: Added range checks to `reverb` (7 params), `phaser` (6 params), `wahwah` (5 params), `distortion`, and `equalization`. Previously these accepted any value, potentially crashing Audacity.
- **Generator duration caps**: `generate_tone`, `generate_noise`, and `generate_chirp` now enforce a 1-hour maximum duration to prevent runaway generation.
- **Analysis parameter validation**: Added bounds checking to `analyze_find_clipping` (duty cycle 1-1000) and `analyze_sample_data_export` (limit 1-1,000,000).

### Reliability

- **Narrowed exception handlers**: CUDA setup in transcription now catches only `ImportError`, `AttributeError`, `OSError` instead of bare `Exception`, so real bugs surface instead of being silently swallowed.
- **Thread-safe Whisper model loading**: Added `threading.Lock` around model initialization with double-checked locking pattern. Prevents concurrent transcription jobs from loading the model simultaneously and wasting memory.

### Tests

- Added 19 new tests (41 → 60 total): pipe protocol edge cases (negative floats, Unicode, Windows paths, embedded quotes, empty strings, large numbers), path safety validation, parse_response edge cases.

## [0.1.0] - 2025-12-01

### Added

- Initial release with 99 MCP tools across 11 modules
- Named pipe bridge to Audacity via mod-script-pipe
- 9 automated audio pipelines (analyze, cleanup, podcast, audiobook, interview, vocal, live, music mastering, lo-fi)
- Background job system with start/poll pattern for long-running operations
- Transcription support via faster-whisper (local, offline)
- Cross-platform pipe protocol (Windows Win32 API + Unix named pipes)
- Injection detection on pipe commands
- 41 passing tests
