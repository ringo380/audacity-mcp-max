# Fork notes

audacity-mcp-max started as a fork of
[xDarkzx/Audacity-MCP](https://github.com/xDarkzx/Audacity-MCP) and is now
maintained under its own name. Findings below are from running the server
against Audacity 3.7.8 on macOS (Apple Silicon), 2026-07-23.

The Python import paths are deliberately still `audacity_mcp` and
`audacity_mcp_shared`, so upstream merges stay tractable. Only the
distribution name, console script, and server identity carry the new name.

## Local setup

Installed editable so edits in this tree are live:

```bash
pip3 install --break-system-packages -e ~/git/audacity-mcp-max
```

Confirm which copy is actually loaded (this is worth checking whenever behavior
looks stale):

```bash
python3 -c "import audacity_mcp.audacity_client as c; print(c.__file__)"
```

MCP servers load at client session start, so a restart of the MCP client is
required after changing code here.

Syncing with upstream:

```bash
git fetch upstream
git merge upstream/main
```

## Do not install from PyPI

PyPI `audacity-mcp` is upstream's package, not this one. At 0.1.8 the published
wheel was older than the GitHub tree carrying the same version number, and it
ships the pre-fix pipe code:

- caches pipe fds across commands
- no retry around the relay's teardown/reopen cycle
- reads through a buffered file object, which drops replies

Symptom is nasty to diagnose: every response arrives one call late (each reply
reflects the previous command) with intermittent empty results. It reads as a
hang or a desync rather than a packaging problem.

`audacity_client.py` in this tree has the fix already (`_posix_open_pipes` opens
FROM before TO with O_NONBLOCK and keeps raw int fds, `_send_raw` retries up to
`_POSIX_SEND_ATTEMPTS` reopening fresh each time). Keep that behavior when
merging upstream.

## Known limitations found in testing

### Mute and Solo silently no-op

`SetTrackStatus` with `Track=<index>` targets the correct track and `Name`
applies (rename works), but `Mute` and `Solo` return
`BatchCommand finished: OK` while the flag stays `0` in a follow-up `GetInfo`.
`track_mute` hits the same wall.

This is Audacity-side, not a bug in this server: the tools forward the command
faithfully and Audacity reports success. So it is not fixable here, only
workaroundable. Options if it becomes worth addressing:

- expose a helper that sets track gain to the minimum instead of muting
- have callers structure work to avoid needing mute (operate on a single track,
  treat the on-disk source file as the backup)
- document it in the tool docstrings so callers do not rely on it

### `GetInfo Type=Commands` crashes Audacity

`project_get_info(info_type="Commands")` pegs CPU, times out at 30s, and takes
the application down with it. Audacity's autosave did not help afterwards:
`~/Library/Application Support/audacity/SessionData/` was empty and no recovery
was offered on relaunch, so unsaved work was lost.

Candidate change: drop `Commands` from the accepted `info_type` values, or guard
it behind an explicit opt-in with a warning in the docstring.

### Export behavior worth documenting in the tool

Three separate traps, all reachable from a single `project_export_audio` call:

1. `Export2` exports the current SELECTION. Without a preceding `select_all`
   you silently get only the selected region (observed: 3.8s written from a
   78.7s project, reported as success).

2. Audacity writes AIFF data regardless of a `.wav` extension, using the sticky
   last-used exporter. The extension does not override it and the tool still
   reports "Exported to WAV format". Verify the container rather than trusting
   the message:

   ```bash
   xxd -l 16 out.wav   # RIFF....WAVE = real wav, FORM....AIFF = not
   ```

   Both are PCM, so remuxing is lossless:

   ```bash
   ffmpeg -i out.wav -acodec pcm_s16le -ar 44100 real.wav
   ```

3. Export renders at the project sample rate, which comes from
   `DefaultProjectSampleRate` in `~/Library/Application Support/audacity/audacity.cfg`.
   A stray `384000` there produces 384 kHz exports and also causes Audacity's
   "Error opening sound device" on playback, since typical interfaces cannot do
   384 kHz output. Edit that file only while Audacity is closed (it rewrites on
   quit); the value applies to new projects, existing ones need
   Settings > Audio Settings > Project Sample Rate.

Possible improvements here: call `SelectAll` inside `project_export_audio` (or
add an explicit `whole_project: bool = True` parameter), and verify the written
container against the requested extension before reporting success.

### Effects apply to the selection, not a track index

Selection does not reliably survive the previous operation, so issue a
`select_all` or `select_region` before every effect rather than assuming state
carries over.

## Verifying an effect chain

ffmpeg gives cheap objective confirmation that effects actually ran, which is
useful when the pipe layer is suspect:

```bash
ffmpeg -i out.wav -af volumedetect -f null -
```

- `max_volume` should match the normalize target exactly
- duration change confirms `truncate_silence` ran
- for gain-independent tone checks, compare `lowpass=f=200` and
  `highpass=f=4000` mean_volume against the full-band mean within each file,
  then compare those ratios across files
