# Mutation matrix: the measurement and verification core

A passing test proves nothing until it has been seen to fail. This is the
recorded result of breaking each rule the measurement work added, one at a
time, and checking that a test - and the right test - noticed.

This is a record, not a script. Reproduce a row by applying its mutation by
hand and running `python3 -m pytest -q`.

## Method

Every row: apply the mutation, run the **full** suite to a file, classify, then
revert with `git checkout --`. Classification follows the exit code as well as
the failure count, because a mutation that makes the harness crash reports zero
`FAILED` lines while actually being covered:

| exit code | `FAILED` lines | verdict |
|---|---|---|
| non-zero | one or more | **guarded** |
| non-zero | none | harness crashed - read the log; covered |
| zero | none | **unguarded**, or the rule is dead code |

Three preconditions, each of which has produced a false verdict elsewhere:

- **The baseline must be seen green first.** A harness that has not been shown
  reading a known-green run cannot classify anything. Baseline for this run:
  `344 passed, 4 skipped` before the three new tests below, `347 passed, 4
  skipped` after.
- **Each anchor must match exactly one site.** A mutation string that also
  matches a byte-identical line in a sibling function silently tests nothing.
  The runner asserts `count == 1` and refuses the row otherwise.
- **The mutation must be a real semantic change.** A mutation that happens to
  be a no-op reports a healthy rule as unguarded.

## Results

15 rows, all guarded. Three of them only after this exercise found they were
not - see "What the zero rows turned out to be" below.

| # | Rule | Mutation | Verdict | Test that caught it |
|---|------|----------|---------|---------------------|
| 1 | Per-rate K-weighting | Hardcode `sample_rate = 48000.0` in `k_weighting_sos` | guarded | `test_measurement_loudness.py::TestSampleRateIndependence::test_44100_reads_the_same_as_48000` |
| 2 | Dual-mono default | `integrated_lufs(dual_mono=True)` default to `False` | guarded (new test) | `TestMonoConvention::test_the_default_convention_is_dual_mono` |
| 3 | Absolute gate | `_ABSOLUTE_GATE_LUFS` to `-200.0` | guarded (new test) | `TestEbuTech3341::test_absolute_gate_is_the_rule_that_discards_a_sub_70_passage` |
| 4 | Relative gate | `_RELATIVE_GATE_LU` to `-200.0` | guarded | `TestEbuTech3341::test_relative_gate_discards_quiet_passages` |
| 5 | Percentile noise floor | Noise floor = first block's RMS (the old rule) | guarded | `test_measurement_metrics.py::TestNoiseFloor::test_uses_the_quiet_part_wherever_it_is` |
| 6 | Silence gap minimum | `_MIN_GAP_SECONDS` to `0.0` | guarded (2 tests) | `TestSilenceGaps::test_finds_a_gap_and_its_position`, `::test_ignores_gaps_shorter_than_the_threshold` |
| 7 | unknown, not missed | `_bounded` returns `"missed"` when `actual is None` | guarded | `test_measurement_targets.py::TestCheckTargets::test_unmeasurable_is_unknown_not_missed` |
| 8 | delta skips missing fields | `delta` treats `None` as `0.0` instead of skipping | guarded | `TestDelta::test_a_field_missing_on_one_side_is_skipped_not_zeroed` |
| 9 | no-measurable-change flag | Delete the `_no_measurable_change` assignment | guarded (2 tests) | `TestDelta::test_flags_when_nothing_moved`, `test_pipeline_verification.py::TestMeasurementBlock::test_no_measurable_change_is_flagged` |
| 10 | Transport precondition | Delete the `Stop` in `_begin_pipeline` | guarded | `TestTransportPrecondition::test_stop_is_issued_before_any_step` |
| 11 | `verify=False` skips exports | `_begin_pipeline` ignores the `verify` flag | guarded | `TestMeasurementBlock::test_verify_false_skips_both_measurements` |
| 12 | Step failure recording | The `except` branch records the step as succeeded | guarded | `TestStepRecords::test_a_failing_step_is_recorded_as_not_ok` |
| 13 | Modal-dialog wording | Drop the dialog sentence from the missing-file branch | guarded | `TestMeasurementFailureIsHonest::test_a_failed_export_does_not_report_a_clean_measurement` |
| 15 | Backends agree (numpy) | Count clicks over the flattened array, not per channel | guarded | `TestBackendsAgree::test_click_counting_is_per_channel_in_both` |
| 16 | Per-channel click state (stdlib) | Share one carry-over sample across channels | guarded (new test) | `TestBackendsAgree::test_click_state_does_not_leak_between_channels_across_blocks` |

### Second pass: the whole-branch review's fixes

A review of the finished branch found defects that no per-task review could see,
because each was a seam between two tasks. Their fixes carry guards of their
own, mutation-checked the same way against the same green baseline (355 passed,
4 skipped). Every row fails its own test and nothing else - except the last,
which correctly takes both tests that name the key.

| Rule | Mutation | Verdict | Test that caught it |
|------|----------|---------|---------------------|
| The measurement export carries the project's channels | Put `NumChannels=1` back | guarded | `TestMeasurementBlock::test_the_measurement_export_keeps_the_project_channels` |
| A refused step is not reported as applied | Append to `steps_applied` before the failure branch | guarded | `TestStepRecords::test_a_step_audacity_refused_is_not_reported_as_completed` |
| The style is normalised before the job name is built | Move `style.lower()` back below `_create_job` | guarded | `TestTargets::test_a_style_in_the_wrong_case_still_gets_its_target_checked` |
| Change thresholds are per field | Give `dc_offset` the 0.1 dB threshold again | guarded | `TestDelta::test_a_removed_dc_offset_counts_as_a_change` |
| The two loudness figures fail independently | Share one `except` across both | guarded | `TestMeasureFile::test_one_loudness_failure_does_not_condemn_the_other` |
| Every target key is reachable | Restore the unreachable `lofi_effect` key | guarded (2 tests) | `TestSpecs::test_every_auto_pipeline_is_covered_or_explicitly_none`, `::test_cleanup_and_lofi_have_no_target` |

The memory fix in the same wave is guarded separately, by two tests that
synthesise twenty minutes of audio straight into the block iterator and assert
the measurement does not grow with it. Restoring the whole-file loader fails
both and nothing else.

Three of these are worth naming as a pattern, because each was a test that
looked like it covered the rule:

- The `verify=False` test asserted only that `before` and `after` were `None`,
  which holds just as well if both exports ran and the results were discarded -
  and the entire point of the flag is not paying for the exports. It now asserts
  the export count is zero, which the fixture could always have told it.
- The target-coverage test compared with `<=`. A subset check looks in one
  direction only, so it could see a missing key but never a wrong one, and a key
  no pipeline can produce is the same defect as a missing one: both are `None`
  at the lookup. It now compares exactly.
- The per-channel click test used a fifth of a second of audio, so it never
  reached a second block, and the rule it names only bites at a block boundary.

### Third pass: the second whole-branch review

A second review of the finished branch found ten more defects, one of which
undid the stereo fix recorded in the pass above: Audacity's `ExportCommand`
declares `S.Define(mnChannels, wxT("NumChannels"), 1)`, so *omitting* the
parameter takes the default and exports mono exactly as asking for one did.
The row above was guarded by a test that asserted the kwarg was absent, which
is a fact about our code and not about the export - the mutation restored a
line the code noticed and Audacity did not.

Ten rows, all guarded, against a baseline of 84 passed.

| Rule | Mutation | Verdict | Test that caught it |
|------|----------|---------|---------------------|
| The channel count is sent, not omitted | Drop `NumChannels` from the pipeline export | guarded (3) | `TestMeasurementBlock::test_the_measurement_export_keeps_the_project_channels` and the mono/unanswerable cases |
| The analysis path sends it too | Drop `NumChannels` from the `auto_analyze_audio` export | guarded (2) | `::test_the_analysis_export_keeps_the_project_channels` |
| An unanswerable track query means stereo | Fall back to 1 | guarded | `::test_an_unanswerable_track_query_exports_stereo` |
| A stereo track forces two channels | Always return 1 | guarded | `::test_the_measurement_export_keeps_the_project_channels` |
| Short tails do not count in the percentile | Count every block | guarded | `TestNoiseFloor::test_a_short_tail_does_not_define_the_floor` |
| The first block sets the nominal length | Reset it on every block | guarded | `::test_a_short_tail_does_not_define_the_floor` |
| A failed measurement is not a failed pipeline | Fold `measurement_failed` back into `success` | guarded | `TestMeasurementFailureIsHonest::test_a_failed_measurement_does_not_fail_the_pipeline` |
| A failed measurement still warns | Report only `steps_failed` as warnings | guarded (2) | `::test_a_failed_export_does_not_report_a_clean_measurement` |
| `verified` reflects what landed | Set it from the request again | guarded | `::test_a_failed_measurement_is_not_reported_as_verified` |
| Measurement runs off the event loop | Call `measure_file` inline | guarded | `TestMeasurementDoesNotStallTheLoop::test_measurement_runs_off_the_event_loop` |
| lofi normalises before naming the job | Move `intensity.lower()` below the job name | guarded | `TestTargets::test_a_lofi_intensity_in_the_wrong_case_names_the_job_normally` |
| The preset is validated before the slot is claimed | Restore create-then-validate | guarded (2) | `TestPresetValidation::test_an_unknown_style_leaves_no_job_behind`, `::test_a_rejected_preset_does_not_block_the_next_pipeline` |
| EXTENSIBLE PCM is read, not rejected | Disable the PCM branch | guarded (3) | `TestOpenPcm::test_extensible_pcm_reads_where_wave_cannot` and the 16-bit and 24-bit cases |
| The fallback keeps the real sample width | Hardcode 4 | guarded (2) | `::test_extensible_pcm_reads_where_wave_cannot` |

Two rows first read UNGUARDED and were false zeros - the fourth cause on the
list, a mutation that is not a real semantic change:

- `st.block_frames = max(st.block_frames, n)` equals the first block's length
  on any file whose blocks are equal except a short tail, so the mutation
  changed nothing. `st.block_frames = n` is the real inversion, and it fails
  the test.
- `presets.get(style)` left the validation exactly where it was. The rule is an
  *ordering*, so the only faithful mutation is to restore the whole
  create-then-validate arrangement, which fails two tests.

A third row read ANCHOR-AMBIGUOUS: `self._sampwidth = bits // 8` occurs in both
readers in that file. Anchoring on the surrounding two lines made it unique.

### Fourth pass: the two minors that shipped

Both were triaged as ship-as-is at merge and looked at afterwards. Both turned
out to be hiding a live gap rather than being cosmetic.

| Rule | Mutation | Verdict | Test that caught it |
|------|----------|---------|---------------------|
| Loudness needs numpy **and** scipy | `and` to `or` in `describe_capability` | guarded (5) | `TestCapabilityProbe::test_loudness_requires_both`, all four combinations |
| `LOUDNESS_AVAILABLE` needs both | `and` to `or` on the constant | guarded | `::test_the_constant_is_false_when_only_one_half_is_present` |
| The reason names only what is missing | Always name both | guarded (2) | `::test_the_reason_names_exactly_what_is_missing` |
| The reason carries the install hint | Drop `_INSTALL_HINT` | guarded (3) | same, all three cases |
| The available reason is empty | Return a non-empty reason | guarded | `::test_reason_is_empty_when_available` |
| The percentile interpolates | `pos = 0.0` (always the minimum) | guarded (3) | `TestPercentile::test_it_interpolates_between_the_two_neighbours`, `TestDynamicRange::test_a_loud_and_quiet_file_has_the_range_between_them` |
| The percentile clamps its upper neighbour | `hi = lo + 1` | guarded (15) | `TestPercentile::test_a_single_value_is_every_percentile` and the metrics suite |

Two rows read UNGUARDED on the first run of this pass, and neither was dead
code:

- **`LOUDNESS_AVAILABLE = HAVE_NUMPY and HAVE_SCIPY`** is computed once at
  import, so no monkeypatching re-runs it. With both extras it reads True
  whether the operator is `and` or `or`; with neither it reads False either
  way. Those are the only two configurations the gate runs, so the operator
  was untestable by construction. The new test reloads the module with scipy
  unimportable, which is the one configuration that tells them apart.
- **`pos = 0.0`** makes every percentile return the minimum, and the whole
  metrics suite passed. Every existing assertion read a percentile near an end
  of the distribution, where interpolating and taking the nearest value agree,
  and `dynamic_range_db` - a shipped field - had no test asserting a non-zero
  range at all. A direct percentile test and a loud-plus-quiet range test close
  it.

The `_percentile` empty-list branch that prompted this pass was indeed dead,
and so was the single-value branch beside it that nobody had flagged: the
interpolation already returns the only value at every percentile, because `pos`
is 0 and both neighbours are index 0. Both are deleted rather than tested, and
the rule that actually holds is mutated instead.

### Row 14 is not in this table

Row 14 was to be "the export metadata dialog preference reads `unknown` when
the key is absent". There is no such code and there will not be: the
preference does not exist in current Audacity. Nothing in the 3.7.8 bundle
contains `ShowId3` or the checkbox label in any language, the whole
`/FileFormats/*` and `/AudioFiles/*` preference family is gone from the binary,
and the only metadata entry points left are user-initiated (the export dialog's
"Edit Metadata" button and the Edit menu command). Audacity 3.2 replaced the
auto-raised editor with that button and removed the preference that gated it.

Reading a key that no version writes would have printed a permanently
`unknown` line, which tells a user a cause was checked when nothing was looked
at. The symptom side is covered instead, by row 13.

## What the zero rows turned out to be

Rows 2, 3 and 16 came back with a clean exit and no failures on the first pass.
None was dead code; all three were live rules with no test, and each was
invisible for a different reason. The tests below were written to close them,
and each was then re-mutated to confirm it fails on its own mutation and only
on its own.

**Row 2 - the default nobody asserted.** Both existing mono tests pass the
convention explicitly (`dual_mono=True`, `dual_mono=False`), so neither can see
what the default is. The default is the only one that ships: `measure_file`
calls `integrated_lufs(path)` with no convention argument. Flipping it would
report every mono file 3 dB below the target Audacity had just normalised it
to. Closed by asserting the no-argument call matches the stereo reading.

**Row 3 - a test passing for the wrong reason.** `test_absolute_gate_discards_
near_silence` uses digital silence, which reads about -200 LUFS. The relative
gate discards that by itself, so the test passes whatever the absolute gate is
set to - it was never testing the absolute gate. The new case holds the
relative gate off the rule: the whole file is quiet, so the ungated mean is
about -66 LUFS and the relative gate sits near -76, which would keep the -73
LUFS passage. Only the -70 absolute gate throws it out.

**Row 16 - a fixture that could not express the bug.** The existing
per-channel click test uses a fifth of a second of audio, which is a single
block, and the first block has no carry-over sample to get wrong. Sharing one
carry-over across channels only bites at a block boundary. The new case is
three seconds of stereo DC at opposite polarity: correct code sees no jump
anywhere, while a shared carry-over invents a click every second.

The lesson each of these three encodes is the same one: the mutation is what
tells you whether a test tests what its name says.
