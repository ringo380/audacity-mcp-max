import pytest

from audacity_mcp.measurement.metrics import compute_metrics
from tests.measurement_signals import (
    constant, db_to_amplitude, silence, sine, write_signal,
)


class TestPeak:
    def test_full_scale_sine_reads_zero_dbfs(self, tmp_path):
        p = write_signal(tmp_path / "a.wav", [sine(1000, 0.5, amplitude=1.0)])
        assert compute_metrics(p)["peak_db"] == pytest.approx(0.0, abs=0.1)

    def test_minus_twenty_dbfs_sine_reads_minus_twenty(self, tmp_path):
        amp = db_to_amplitude(-20.0)
        p = write_signal(tmp_path / "a.wav", [sine(1000, 0.5, amplitude=amp)])
        assert compute_metrics(p)["peak_db"] == pytest.approx(-20.0, abs=0.1)


class TestRms:
    def test_sine_rms_is_three_db_below_its_peak(self, tmp_path):
        """A sine's RMS is peak/sqrt(2), i.e. -3.01 dB. If this reads 0 the
        code is measuring peak and calling it RMS."""
        p = write_signal(tmp_path / "a.wav", [sine(1000, 1.0, amplitude=1.0)])
        assert compute_metrics(p)["rms_db"] == pytest.approx(-3.01, abs=0.1)


class TestNoiseFloor:
    def test_uses_the_quiet_part_wherever_it_is(self, tmp_path):
        """The bug this replaces: the old code took the first 0.5s and called it
        the noise floor, so a file that opens with a loud passage reported a
        noise floor 60 dB too high and the routing followed it."""
        loud = sine(1000, 3.0, amplitude=1.0)
        quiet = sine(1000, 3.0, amplitude=db_to_amplitude(-60.0))
        p = write_signal(tmp_path / "a.wav", [loud + quiet])
        assert compute_metrics(p)["noise_floor_db"] == pytest.approx(-63.0, abs=2.0)

    def test_same_answer_when_the_quiet_part_comes_first(self, tmp_path):
        loud = sine(1000, 3.0, amplitude=1.0)
        quiet = sine(1000, 3.0, amplitude=db_to_amplitude(-60.0))
        p = write_signal(tmp_path / "a.wav", [quiet + loud])
        assert compute_metrics(p)["noise_floor_db"] == pytest.approx(-63.0, abs=2.0)

    def test_a_uniformly_loud_file_has_a_loud_noise_floor(self, tmp_path):
        """No quiet passage means no low noise floor. Reporting one would invent
        headroom that is not there."""
        p = write_signal(tmp_path / "a.wav", [sine(1000, 4.0, amplitude=1.0)])
        assert compute_metrics(p)["noise_floor_db"] > -10.0


class TestDcOffset:
    def test_a_constant_signal_is_all_offset(self, tmp_path):
        p = write_signal(tmp_path / "a.wav", [constant(0.5, 1.0)])
        assert compute_metrics(p)["dc_offset"] == pytest.approx(0.5, abs=0.01)

    def test_a_sine_has_no_offset(self, tmp_path):
        p = write_signal(tmp_path / "a.wav", [sine(1000, 1.0)])
        assert compute_metrics(p)["dc_offset"] == pytest.approx(0.0, abs=0.01)


class TestClipping:
    def test_counts_samples_at_full_scale(self, tmp_path):
        p = write_signal(tmp_path / "a.wav", [constant(1.0, 0.1)])
        assert compute_metrics(p)["clipped_samples"] > 4000

    def test_a_quiet_sine_is_not_clipped(self, tmp_path):
        p = write_signal(tmp_path / "a.wav", [sine(1000, 0.5, amplitude=0.5)])
        assert compute_metrics(p)["clipped_samples"] == 0


class TestSilenceGaps:
    def test_finds_a_gap_and_its_position(self, tmp_path):
        body = sine(1000, 1.0, amplitude=0.5) + silence(2.0) + sine(1000, 1.0, amplitude=0.5)
        p = write_signal(tmp_path / "a.wav", [body])
        r = compute_metrics(p)
        assert r["silence_gap_count"] == 1
        start, dur = r["silence_gaps"][0]
        assert start == pytest.approx(1.0, abs=0.1)
        assert dur == pytest.approx(2.0, abs=0.1)

    def test_ignores_gaps_shorter_than_the_threshold(self, tmp_path):
        body = sine(1000, 1.0, amplitude=0.5) + silence(0.1) + sine(1000, 1.0, amplitude=0.5)
        p = write_signal(tmp_path / "a.wav", [body])
        assert compute_metrics(p)["silence_gap_count"] == 0


class TestClicks:
    """click_count is returned to callers and routed on, so it needs a positive
    case. Without one, a threshold compared the wrong way round, an off-by-one
    in the sample delta, or a backend that never accumulates st.clicks all pass
    the whole suite - the metric would be reported as 0 forever and read as
    'no clicks found' rather than 'never looked'."""

    def test_a_single_discontinuity_is_one_click(self, tmp_path):
        body = constant(0.0, 0.1) + constant(0.9, 0.1)
        p = write_signal(tmp_path / "click.wav", [body])
        assert compute_metrics(p)["click_count"] == 1

    def test_a_smooth_signal_has_no_clicks(self, tmp_path):
        """A 100 Hz sine at 44.1 kHz moves far less than the threshold between
        adjacent samples, so anything above zero here is the detector firing on
        ordinary waveform slope."""
        p = write_signal(tmp_path / "smooth.wav", [sine(100, 0.5, amplitude=0.9)])
        assert compute_metrics(p)["click_count"] == 0

    def test_both_backends_count_the_same_clicks(self, tmp_path, monkeypatch):
        from audacity_mcp.measurement import metrics as mod
        if not mod.HAVE_NUMPY:
            pytest.skip("only one backend available on this install")

        body = constant(0.0, 0.05) + constant(0.9, 0.05) + constant(-0.9, 0.05)
        p = write_signal(tmp_path / "two.wav", [body])

        monkeypatch.setattr(mod, "_accumulate", mod._accumulate_numpy)
        fast = compute_metrics(p)["click_count"]
        monkeypatch.setattr(mod, "_accumulate", mod._accumulate_stdlib)
        slow = compute_metrics(p)["click_count"]
        assert fast == slow == 2


class TestEmptyAudio:
    def test_zero_frames_do_not_crash(self, tmp_path):
        """A 0-frame export must not raise from the measurement layer. The
        deleted _measure_wav returned None for this; the package returns a
        degenerate dict (loudness fields None) and the tool callers guard on
        file size before ever reaching it, so an empty export still surfaces as
        'no measurement' at the tool level rather than a crash."""
        p = write_signal(tmp_path / "empty.wav", [silence(0.0)])
        r = compute_metrics(p)
        assert r["duration"] == 0.0
        assert r["rms_db"] is None


class TestStereo:
    def test_measures_across_both_channels(self, tmp_path):
        """Peak must come from whichever channel is louder, not just the left."""
        p = write_signal(tmp_path / "s.wav", [
            sine(1000, 0.5, amplitude=db_to_amplitude(-40.0)),
            sine(1000, 0.5, amplitude=1.0),
        ])
        assert compute_metrics(p)["peak_db"] == pytest.approx(0.0, abs=0.1)
        assert compute_metrics(p)["channels"] == 2


class TestBackendsAgree:
    """The two accumulators are the same rule written twice. Nothing but a
    direct comparison catches them drifting apart, and a drift here would make
    a measurement depend on whether an optional extra happened to be installed.
    """

    def _signal(self):
        left = (
            sine(1000, 1.0, amplitude=0.9)
            + [0.0] * 44100
            + sine(1000, 1.0, amplitude=db_to_amplitude(-50.0))
        )
        right = (
            sine(1400, 1.0, amplitude=0.4)
            + [0.0] * 44100
            + sine(1400, 1.0, amplitude=db_to_amplitude(-45.0))
        )
        return [left, right]

    def test_every_field_matches_between_backends(self, tmp_path, monkeypatch):
        from audacity_mcp.measurement import metrics as mod
        if not mod.HAVE_NUMPY:
            pytest.skip("only one backend available on this install")

        p = write_signal(tmp_path / "both.wav", self._signal())

        monkeypatch.setattr(mod, "_accumulate", mod._accumulate_numpy)
        fast = compute_metrics(p)
        monkeypatch.setattr(mod, "_accumulate", mod._accumulate_stdlib)
        slow = compute_metrics(p)

        assert set(fast) == set(slow)
        for key in fast:
            if isinstance(fast[key], float):
                assert fast[key] == pytest.approx(slow[key], abs=0.05), key
            else:
                assert fast[key] == slow[key], key

    def test_click_counting_is_per_channel_in_both(self, tmp_path, monkeypatch):
        """Comparing the last sample of L against the first of R would invent a
        click at every frame boundary of a stereo file - a defect that only
        shows up when the two channels differ."""
        from audacity_mcp.measurement import metrics as mod
        if not mod.HAVE_NUMPY:
            pytest.skip("only one backend available on this install")

        p = write_signal(tmp_path / "lr.wav", [
            constant(0.8, 0.2),
            constant(-0.8, 0.2),
        ])
        monkeypatch.setattr(mod, "_accumulate", mod._accumulate_numpy)
        fast = compute_metrics(p)["click_count"]
        monkeypatch.setattr(mod, "_accumulate", mod._accumulate_stdlib)
        slow = compute_metrics(p)["click_count"]
        assert fast == slow == 0

    def test_click_state_does_not_leak_between_channels_across_blocks(
        self, tmp_path, monkeypatch
    ):
        """The carry-over sample has to be per channel, not one shared value.

        The case above cannot see this. It is a fifth of a second, so it is a
        single block, and the first block has no carry-over to get wrong. The
        rule only bites at a block boundary: with one shared carry-over, the
        first sample of L in block 2 is compared against the last sample of R
        in block 1, which on a file whose channels sit at different levels
        invents a click every second.
        """
        from audacity_mcp.measurement import metrics as mod

        p = write_signal(tmp_path / "dc.wav", [
            constant(0.9, 3.0),
            constant(-0.9, 3.0),
        ])
        backends = ["_accumulate_stdlib"]
        if mod.HAVE_NUMPY:
            backends.append("_accumulate_numpy")
        for name in backends:
            monkeypatch.setattr(mod, "_accumulate", getattr(mod, name))
            assert compute_metrics(p)["click_count"] == 0, name
