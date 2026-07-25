import pytest

from audacity_mcp.measurement import LOUDNESS_AVAILABLE
from tests.measurement_signals import db_to_amplitude, silence, sine, write_signal

pytestmark = pytest.mark.skipif(
    not LOUDNESS_AVAILABLE, reason="needs the measurement extra"
)


def _stereo_sine(db, seconds=20.0, rate=48000, freq=1000.0):
    amp = db_to_amplitude(db)
    ch = sine(freq, seconds, rate=rate, amplitude=amp)
    return [ch, list(ch)]


class TestEbuTech3341:
    """EBU Tech 3341 compliance cases. A meter that only agrees with itself
    proves nothing, so these are external ground truth, not self-consistency."""

    def test_case_1_minus_23_lufs(self, tmp_path):
        from audacity_mcp.measurement.loudness import integrated_lufs
        p = write_signal(tmp_path / "c1.wav", _stereo_sine(-23.0), rate=48000)
        assert integrated_lufs(p) == pytest.approx(-23.0, abs=0.1)

    def test_case_2_minus_33_lufs(self, tmp_path):
        from audacity_mcp.measurement.loudness import integrated_lufs
        p = write_signal(tmp_path / "c2.wav", _stereo_sine(-33.0), rate=48000)
        assert integrated_lufs(p) == pytest.approx(-33.0, abs=0.1)

    def test_absolute_gate_discards_near_silence(self, tmp_path):
        """Tech 3341 case 3: quiet passages below -70 LUFS must not drag the
        integrated value down."""
        from audacity_mcp.measurement.loudness import integrated_lufs
        loud = _stereo_sine(-23.0, seconds=10.0)
        quiet = [silence(10.0, rate=48000), silence(10.0, rate=48000)]
        combined = [loud[0] + quiet[0], loud[1] + quiet[1]]
        p = write_signal(tmp_path / "c3.wav", combined, rate=48000)
        assert integrated_lufs(p) == pytest.approx(-23.0, abs=0.2)

    def test_relative_gate_discards_quiet_passages(self, tmp_path):
        """A passage more than 10 LU below the ungated mean is gated out."""
        from audacity_mcp.measurement.loudness import integrated_lufs
        loud = _stereo_sine(-23.0, seconds=10.0)
        low = _stereo_sine(-50.0, seconds=10.0)
        combined = [loud[0] + low[0], loud[1] + low[1]]
        p = write_signal(tmp_path / "c4.wav", combined, rate=48000)
        assert integrated_lufs(p) == pytest.approx(-23.0, abs=0.3)


class TestSampleRateIndependence:
    def test_44100_reads_the_same_as_48000(self, tmp_path):
        """The published K-weighting coefficients are defined at 48 kHz. Using
        them unchanged at 44.1 kHz gives a plausible-looking wrong answer, so
        the filters must be redesigned per rate."""
        from audacity_mcp.measurement.loudness import integrated_lufs
        p48 = write_signal(tmp_path / "a48.wav", _stereo_sine(-23.0, rate=48000), rate=48000)
        p44 = write_signal(tmp_path / "a44.wav", _stereo_sine(-23.0, rate=44100), rate=44100)
        assert integrated_lufs(p44) == pytest.approx(integrated_lufs(p48), abs=0.15)


class TestMonoConvention:
    def test_dual_mono_matches_the_stereo_reading(self, tmp_path):
        """Audacity's LoudnessNormalization defaults to DualMono=True. If the
        meter used the other convention it would disagree by ~3 dB with the
        target Audacity had just normalised to, and report a failure that did
        not exist."""
        from audacity_mcp.measurement.loudness import integrated_lufs
        mono = write_signal(
            tmp_path / "m.wav", [sine(1000, 20.0, rate=48000, amplitude=db_to_amplitude(-23.0))],
            rate=48000,
        )
        stereo = write_signal(tmp_path / "s.wav", _stereo_sine(-23.0), rate=48000)
        assert integrated_lufs(mono, dual_mono=True) == pytest.approx(
            integrated_lufs(stereo), abs=0.1
        )

    def test_single_channel_convention_is_three_db_quieter(self, tmp_path):
        from audacity_mcp.measurement.loudness import integrated_lufs
        mono = write_signal(
            tmp_path / "m.wav", [sine(1000, 20.0, rate=48000, amplitude=db_to_amplitude(-23.0))],
            rate=48000,
        )
        assert integrated_lufs(mono, dual_mono=False) == pytest.approx(
            integrated_lufs(mono, dual_mono=True) - 3.01, abs=0.1
        )


class TestSilence:
    def test_silence_returns_none_rather_than_minus_infinity(self, tmp_path):
        from audacity_mcp.measurement.loudness import integrated_lufs
        p = write_signal(tmp_path / "z.wav", [silence(5.0, rate=48000)], rate=48000)
        assert integrated_lufs(p) is None
