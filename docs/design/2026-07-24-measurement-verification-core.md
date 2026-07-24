# Design: the measurement and verification core

**Date:** 2026-07-24
**Status:** Approved, not yet implemented
**Scope:** Milestone A of the five-part roadmap in
[the plugin packaging design](2026-07-24-plugin-packaging.md#roadmap-context)

## Problem

This server can ask Audacity to hit a loudness target but cannot tell whether it
did. `loudness_normalize` hands `LUFSLevel=-16.0` to Audacity; `_measure_wav`
returns peak, RMS, DC offset, clipping, clicks, silence gaps and dynamic range,
and **no LUFS and no true peak**. Every loudness claim in the tool docstrings is
therefore unverifiable by the code that makes it.

The gap is wider than loudness:

- **`noise_floor_db` is not a noise floor.** It is the RMS of the first 0.5
  seconds, which is an assumption that the recording opens with room tone. On a
  file that opens with speech it reports a number far too high, and several
  pipelines route on it.
- **A pipeline reports a fixed string, not an outcome.** `_complete_job` says
  "peaks reduced if hot, never boosted" whether or not anything moved.
- **A silent step is indistinguishable from a working one.** `SetTrackStatus`
  accepts `Mute` and does nothing; an effect on an empty selection does nothing.
  Both report success.
- **Two conditions make a live command look dead.** Audacity refuses scripted
  commands while transport is playing or paused, and an export can raise a modal
  metadata dialog that blocks the reply. Both surface as the signature of a
  broken pipe.

Each of these makes a measurement wrong or a success claim hollow, so they are
one piece of work rather than four.

## Goal

A pipeline reports what it actually did, in numbers, against a declared target -
and says plainly when it cannot measure something rather than guessing. Nothing
downstream is allowed to claim an outcome this layer cannot substantiate.

This milestone adds no new tools and changes no existing return shape. The tool
count stays 132, so `scripts/check_registration.py`'s per-module counts are
untouched.

## Architecture

`audacity_mcp/measurement/` is a new package that is a pure function of a file
path. No MCP, no client, no async - which is what lets the loudness math be
tested with Audacity nowhere in the picture.

```
audacity_mcp/measurement/
├── __init__.py    capability probe (HAVE_NUMPY, HAVE_SCIPY) + describe_capability()
├── reader.py      block iterator over open_pcm -> float blocks
├── metrics.py     peak, RMS, DC, clipping, clicks, silence gaps,
│                  dynamic range, percentile noise floor
├── loudness.py    BS.1770-4 gated LUFS + oversampled true peak (needs the extra)
├── targets.py     named target specs per pipeline
└── report.py      Measurement, delta(), check_targets(), to_dict()
```

`audacity_mcp_shared/` is deliberately untouched. It is stdlib-only so the Win32
pipe probe can run under a bare embeddable Python, and measurement needs numpy and scipy;
putting it there would cost that guarantee for no gain.

`reader.py` yields fixed-size blocks of float samples from the existing
`open_pcm`. Two backends sit behind one interface - numpy when it is present,
today's struct-unpack loop when it is not.

`metrics.py` carries the same split, and that is not an optimisation for its own
sake. Iterating a numpy array element by element in Python is *slower* than
iterating a list, so a single Python loop above a numpy reader would make the
optional extra actively worse at this half of the job: measured on this machine,
a one-hour mono file costs around 70 seconds per pass in the Python loop, and a
verified pipeline runs two passes. Both accumulators fill the same state object
and the tests assert the two produce identical answers over one file, so the
duplication is guarded rather than trusted.

Lifting measurement out also takes about 165 lines off `cleanup_tools.py`, which
is 1490 lines and by a wide margin the largest module in the repo.

### numpy and scipy are an optional extra

`pyproject.toml` gains a `measurement` extra alongside the existing
`transcription` one, holding numpy and scipy. K-weighting is an IIR filter, and
the recursion cannot be vectorised in numpy alone - a pure-numpy biquad is still
a per-sample Python loop - so `scipy.signal.sosfilt` is what makes loudness
viable at real durations. Both imports are confined to `loudness.py` and the fast
paths in `reader.py` and `metrics.py`.

Without them, metrics still work through the stdlib path. LUFS and true peak read
`null` with `"unavailable: install the measurement extra"`, and **any target that
depends on them reports `unknown`, never `failed`** - an unmeasurable target is
not a missed one, and reporting it as missed would be exactly the dishonesty this
milestone exists to remove.

### Noise floor changes meaning

`metrics.py` computes the noise floor as the **10th percentile of per-block RMS
across the whole file** rather than the RMS of the opening 0.5 seconds.

This is a behaviour change, not an addition. `auto_analyze_audio`'s
recommendations and the noise-reduction routing will shift on files that do not
open with room tone. That is the intent, but it must be called out in the
changelog rather than shipped quietly, because a user who knows the old
behaviour will see different advice on the same file.

## Loudness

`loudness.py` implements ITU-R BS.1770-4: K-weighting (a high-shelf stage
followed by an RLB high-pass), 400 ms blocks at 75 % overlap, an absolute gate at
-70 LUFS, and a relative gate 10 LU below the ungated mean.

Two details decide whether the numbers are usable at all.

**Coefficients are designed per sample rate.** The published K-weighting
coefficients are defined at 48 kHz. Applying them unchanged to 44.1 kHz material
gives a quietly wrong answer - wrong in a way that looks plausible, which is the
worst kind. The filters are derived from the analog prototype for the file's
actual rate.

**Mono handling matches Audacity's.** `loudness_normalize` passes
`DualMono=True` by default, which duplicates mono to two channels and shifts the
reading by about 3 dB. A meter using the other convention would disagree with the
target Audacity had just normalised to, and the verification layer would report a
failure that did not exist. The meter takes the same `dual_mono` flag and
defaults to matching Audacity.

True peak follows Annex 2: 4x polyphase FIR oversampling, via
`scipy.signal.resample_poly`, which the extra already carries for the
K-weighting filter.

### Validating against external ground truth

The meter is checked against EBU Tech 3341 compliance cases, which are
synthesizable rather than requiring shipped audio:

| Case | Signal | Expected |
|---|---|---|
| 1 | stereo 1 kHz sine, -23 dBFS | -23.0 LUFS ±0.1 |
| 2 | stereo 1 kHz sine, -33 dBFS | -33.0 LUFS ±0.1 |
| 3-5 | gated sequences constructed from the spec | per spec, ±0.1 |

A meter that only agrees with itself proves nothing. These cases are the reason
the package is a pure function - they run with no Audacity, no pipe and no
client.

## No-op detection, and its limit

Two mechanisms:

1. **Reply-level.** `_run_pipeline_step` records each step into `job["steps"]` as
   `{name, ok, reply, noop_reason}`, flagging failures and empty replies.
2. **Whole-pipeline.** If the entry/exit delta shows nothing moved, the report
   says so.

A third was planned - a denylist of commands documented to report success while
doing nothing, `SetTrackStatus` with `Mute` or `Solo` being the known case - and
was dropped before implementation. No pipeline calls `SetTrackStatus`; it appears
only in `track_tools.py`, which already handles the problem by reading the track
back. A denylist here would have been unreachable code with no call site, and a
rule with nowhere to fire is dead code rather than a guard.

**The limit is stated in the report, not hidden.** With entry and exit
measurement only, the report can say the pipeline changed nothing; it cannot
numerically attribute a no-op to one step. Per-step attribution would cost one
full export and analysis per step - a six-step pipeline on a one-hour project
becomes minutes of export overhead, with each export another chance to hit the
AIFF-into-`.wav` trap. That trade is deliberate, and the report names it rather
than implying a precision it does not have.

## Preconditions

**Transport.** Issue `Stop` at pipeline entry. It is idempotent, needs no state
query, and removes the entire class of "the commands did nothing because Audacity
was paused" outright.

**Export metadata dialog.** Two halves, because one of them is not yet confirmed.

The robust half is symptom-side and does not depend on knowing any config key:
when an export command times out and the file never appears, report *"a modal
dialog may be blocking Audacity - check its window"* rather than the current
pipe-failure message.

The other half is a config check in `plugin_doctor.py` for the
show-metadata-on-export preference. **The exact `audacity.cfg` key must be
confirmed empirically during implementation.** It is not asserted here; putting a
guessed key into the doctor would make the diagnostic lie, which is worse than
not having the check.

## Pipeline lifecycle

Four touch points in `cleanup_tools.py`, all additive:

| Point | Change |
|---|---|
| `_create_job` | `Stop`, then measure entry into `job["measurement"]["before"]` |
| `_run_pipeline_step` | record step outcome into `job["steps"]` |
| `_complete_job` | measure exit, compute delta, evaluate targets |
| `check_pipeline_status` | return the `measurement` block on terminal states |

Every `auto_` pipeline gains `verify: bool = True`, so someone working on a
two-hour file can skip the two exports.

## Targets

`targets.py` declares a spec per pipeline:

| Pipeline | Target |
|---|---|
| `auto_cleanup_podcast` | -16 LUFS, true peak <= -1 dBTP |
| `auto_audiobook_mastering` | ACX: RMS -23 to -18 dBFS, peak <= -3 dBFS, noise floor <= -60 dBFS |
| `auto_cleanup_interview`, `auto_cleanup_vocal`, `auto_cleanup_live` | -16 LUFS, true peak <= -1 dBTP |
| `auto_master_music` | per genre: EDM and hip-hop -9 to -11 LUFS, rock and pop -11 to -14, acoustic and classical -16 to -18; true peak <= -1 dBTP |
| `auto_cleanup_audio` | none - it is explicitly "just clean"; delta only |
| `auto_lofi_effect` | none - a creative effect has no correctness target |

### "Not met" is the useful answer

The pipelines deliberately do not chase LUFS. `_loudness_step` is peak-only by
design and never boosts, because a blind LUFS boost on quiet material can add
20-30 dB and destroy the file. A podcast run will therefore routinely land short
of -16 LUFS.

The report must say:

> -21.3 LUFS, 5.3 LU below the -16 target - run `loudness_normalize(-16)` to
> close it.

and not present that as a failure of the pipeline. That sentence is the actual
product of this milestone. It is also precisely the input milestone C's skills
layer needs: knowing how far off a result is, and what closes the gap, is the
judgment content that cannot be written honestly today.

## Verification

`./scripts/verify.sh` stays the single gate. It gains:

- **Loudness unit tests** against the EBU Tech 3341 cases above.
- **Metrics unit tests** against synthesized signals with known ground truth -
  a known-amplitude sine for peak and RMS, a constructed file with a known quiet
  section for the percentile noise floor, a known DC-shifted signal for offset.
- **Pipeline lifecycle tests** with the mock client, asserting the shape of the
  `measurement` block, the `steps` array, and that `verify=False` skips both
  exports.
- **A numpy-absent path test**, by patching `HAVE_NUMPY`, asserting LUFS reads
  `null` with a reason and that LUFS-dependent targets read `unknown` rather than
  `failed`.
- **A mutation matrix** over the new guards, per the repo convention: invert or
  delete each guard in turn and record which tests fail. A zero-failure row is
  either dead code or an unguarded rule, and the matrix is what tells them apart.

The suite stays isolated from the real FIFOs and needs neither Audacity nor a
network.

## Deliberately excluded

- **No new MCP tools.** Verification rides on the existing pipeline status
  payload. A standalone `measure_audio` tool is defensible but makes verification
  opt-in, which is how it gets skipped. Reconsider it in milestone C, when the
  skills layer has a concrete need for an ad-hoc check.
- **No per-step measurement.** Covered above - the cost is real and the report is
  honest about what it gives up.
- **No loudness range (LRA) or short-term/momentary series.** Integrated LUFS and
  true peak are what the targets need. Adding a time series with no consumer is
  scope for its own sake.
- **No change to what the pipelines actually do.** This milestone measures and
  reports. Making a pipeline chase a target is a separate decision with its own
  risk, and it should be made with measurement already in place.

## What this unblocks

Milestone B (work Audacity cannot do - offline ffmpeg/librosa, batch, stems,
headless) shares this machinery directly: measuring loudness without Audacity
open is the same code as verifying a chain hit its target.

Milestone C (the judgment layer - skills for which tool, what target, what order,
plus an audio-engineer agent that operates them) depends on it for a different
reason. Prose about when to compress is only as good as the pipeline's ability to
prove what it did, and a skill that cannot check its own advice is a confident
guess.

## Source note

The transport and modal-dialog preconditions were prompted by the University at
Albany IMC handout *Audacity: Tips & Tricks* (Regina Testa, 2020/2021, CC BY
4.0), which states plainly that commands cannot execute while play or pause is
engaged, and that export raises a metadata dialog with a bypass checkbox. Both
are documented Audacity behaviour that this code had never accounted for.
