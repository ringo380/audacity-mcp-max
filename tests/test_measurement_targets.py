import pytest

from audacity_mcp.measurement import LOUDNESS_AVAILABLE, targets
from audacity_mcp.measurement.report import check_targets, delta, measure_file
from tests.measurement_signals import sine, write_signal


def measurement(**over):
    base = {
        "duration": 60.0, "sample_rate": 44100, "channels": 1,
        "peak_db": -3.0, "rms_db": -20.0, "noise_floor_db": -65.0,
        "dc_offset": 0.0, "clipped_samples": 0, "click_count": 0,
        "silence_gaps": [], "silence_gap_count": 0, "dynamic_range_db": 20.0,
        "lufs": -16.0, "true_peak_dbtp": -1.5, "unavailable": {},
    }
    base.update(over)
    return base


class TestSpecs:
    def test_every_auto_pipeline_is_covered_or_explicitly_none(self):
        """A pipeline with no entry at all is an oversight; one mapped to None
        is a decision. The dict must distinguish them.

        Compared with `==`, not `<=`. A subset check only looks in one
        direction, so it cannot see a key no pipeline can produce - and that is
        what happened: SPECS carried "lofi_effect" while auto_lofi_effect names
        its job f"lofi_{intensity}", so the entry was unreachable and this test
        passed anyway. An unreachable key is the same defect as a missing one,
        because a lookup miss and an explicit None are both None downstream.
        """
        expected = {
            "podcast_cleanup", "audiobook_mastering", "interview_cleanup",
            "vocal_cleanup", "live_cleanup", "cleanup_audio",
            "mastering_edm", "mastering_hiphop", "mastering_rock",
            "mastering_pop", "mastering_classical", "mastering_acoustic",
            "lofi_light", "lofi_medium", "lofi_heavy",
        }
        assert set(targets.SPECS) == expected

    def test_cleanup_and_lofi_have_no_target(self):
        assert targets.SPECS["cleanup_audio"] is None
        for intensity in ("light", "medium", "heavy"):
            assert targets.SPECS[f"lofi_{intensity}"] is None

    def test_acx_spec_matches_the_published_requirement(self):
        spec = targets.SPECS["audiobook_mastering"]
        assert spec.rms_range == (-23.0, -18.0)
        assert spec.peak_max == -3.0
        assert spec.noise_floor_max == -60.0

    def test_unknown_pipeline_name_returns_none(self):
        assert targets.for_pipeline("no_such_pipeline") is None


class TestCheckTargets:
    def test_met_when_inside_the_range(self):
        spec = targets.SPECS["podcast_cleanup"]
        result = check_targets(measurement(lufs=-16.2, true_peak_dbtp=-1.5), spec)
        assert result["lufs"]["status"] == "met"
        assert result["true_peak"]["status"] == "met"

    def test_missed_when_outside_and_says_how_far(self):
        spec = targets.SPECS["podcast_cleanup"]
        result = check_targets(measurement(lufs=-21.3), spec)
        assert result["lufs"]["status"] == "missed"
        assert result["lufs"]["actual"] == -21.3
        assert "4.3" in result["lufs"]["gap"]

    def test_missed_names_the_tool_that_closes_the_gap(self):
        """The report's job is to say what to do next, not just that something
        is off. This sentence is the product of the milestone."""
        spec = targets.SPECS["podcast_cleanup"]
        result = check_targets(measurement(lufs=-21.3), spec)
        assert "loudness_normalize" in result["lufs"]["advice"]

    def test_unmeasurable_is_unknown_not_missed(self):
        """Without the extra there is no LUFS. Calling that a missed target
        would report a failure that was never established."""
        spec = targets.SPECS["podcast_cleanup"]
        m = measurement(lufs=None, true_peak_dbtp=None,
                        unavailable={"lufs": "needs numpy and scipy"})
        result = check_targets(m, spec)
        assert result["lufs"]["status"] == "unknown"
        assert result["true_peak"]["status"] == "unknown"
        assert "numpy" in result["lufs"]["reason"]

    def test_no_spec_yields_an_empty_result(self):
        assert check_targets(measurement(), None) == {}

    def test_acx_checks_all_three_of_its_bounds(self):
        spec = targets.SPECS["audiobook_mastering"]
        result = check_targets(
            measurement(rms_db=-20.0, peak_db=-3.5, noise_floor_db=-65.0), spec
        )
        assert result["rms"]["status"] == "met"
        assert result["peak"]["status"] == "met"
        assert result["noise_floor"]["status"] == "met"

    def test_acx_flags_a_hot_peak(self):
        spec = targets.SPECS["audiobook_mastering"]
        result = check_targets(measurement(peak_db=-1.0), spec)
        assert result["peak"]["status"] == "missed"


class TestMeasureFile:
    @pytest.mark.skipif(not LOUDNESS_AVAILABLE, reason="needs the measurement extra")
    def test_one_loudness_failure_does_not_condemn_the_other(self, tmp_path, monkeypatch):
        """The two loudness figures are measured separately, so they have to
        fail separately. Sharing one except clause meant a true-peak failure
        marked lufs unavailable while the correct lufs value sat in the same
        dict - telling the reader to disbelieve a number that is right.
        """
        from audacity_mcp.measurement import loudness

        def boom(*a, **k):
            raise RuntimeError("resampler said no")

        monkeypatch.setattr(loudness, "true_peak_dbtp", boom)
        p = write_signal(tmp_path / "m.wav", [sine(1000, 1.0, amplitude=0.5)])
        result = measure_file(p)

        assert result["lufs"] is not None
        assert "lufs" not in result["unavailable"]
        assert result["true_peak_dbtp"] is None
        assert "resampler said no" in result["unavailable"]["true_peak"]


class TestDelta:
    def test_reports_before_after_and_change(self):
        d = delta(measurement(lufs=-25.0), measurement(lufs=-16.0))
        assert d["lufs"]["before"] == -25.0
        assert d["lufs"]["after"] == -16.0
        assert d["lufs"]["change"] == pytest.approx(9.0)

    def test_a_field_missing_on_one_side_is_skipped_not_zeroed(self):
        """Reporting 'change: 0' for something that was never measured is the
        same lie as reporting a missed target for it."""
        d = delta(measurement(lufs=None), measurement(lufs=-16.0))
        assert "lufs" not in d

    def test_flags_when_nothing_moved(self):
        d = delta(measurement(), measurement())
        assert d["_no_measurable_change"] is True

    def test_does_not_flag_when_something_moved(self):
        d = delta(measurement(peak_db=-9.0), measurement(peak_db=-3.0))
        assert d.get("_no_measurable_change") is not True

    def test_a_removed_dc_offset_counts_as_a_change(self):
        """dc_offset is a fraction of full scale, not dB. Under a single 0.1
        threshold for every field, a DC offset of 0.06 - which
        auto_analyze_audio calls a defect above 0.005, and which the DC step
        removes completely - was reported as no measurable change: the no-op
        detector calling a working pipeline a no-op.
        """
        d = delta(measurement(dc_offset=0.06), measurement(dc_offset=0.0))
        assert d.get("_no_measurable_change") is not True

    def test_a_count_moves_in_whole_numbers(self):
        """The other end of the same rule: counts are exact, so the threshold
        for them is half a unit rather than a tenth of a dB."""
        d = delta(measurement(click_count=0), measurement(click_count=1))
        assert d.get("_no_measurable_change") is not True
