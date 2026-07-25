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
