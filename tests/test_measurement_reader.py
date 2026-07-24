import pytest

from audacity_mcp.measurement.reader import read_audio, UnreadableAudio
from tests.measurement_signals import sine, write_signal


class TestAudioInfo:
    def test_reports_rate_channels_and_duration(self, tmp_path):
        p = write_signal(tmp_path / "a.wav", [sine(1000, 2.0)], rate=44100)
        info, _ = read_audio(p)
        assert info.sample_rate == 44100
        assert info.channels == 1
        assert info.frames == 88200
        assert info.duration == pytest.approx(2.0, abs=0.01)

    def test_stereo_channel_count(self, tmp_path):
        p = write_signal(tmp_path / "s.wav", [sine(1000, 0.5), sine(1000, 0.5)])
        info, _ = read_audio(p)
        assert info.channels == 2


class TestBlocks:
    def test_blocks_cover_every_frame_exactly_once(self, tmp_path):
        p = write_signal(tmp_path / "a.wav", [sine(1000, 1.0, rate=8000)], rate=8000)
        info, blocks = read_audio(p, block_frames=1000)
        total = sum(len(b[0]) for b in blocks)
        assert total == info.frames == 8000

    def test_channels_are_deinterleaved(self, tmp_path):
        """A block is per-channel, not interleaved. Getting this wrong makes a
        stereo file measure as a mono file at twice the rate."""
        left = [0.5] * 100
        right = [-0.5] * 100
        p = write_signal(tmp_path / "s.wav", [left, right], rate=8000)
        _, blocks = read_audio(p, block_frames=50)
        for block in blocks:
            assert all(v > 0 for v in block[0])
            assert all(v < 0 for v in block[1])

    def test_samples_are_normalised_to_unit_range(self, tmp_path):
        p = write_signal(
            tmp_path / "a.wav", [sine(1000, 0.2, rate=8000, amplitude=1.0)], rate=8000
        )
        _, blocks = read_audio(p, block_frames=8000)
        peak = max(abs(v) for b in blocks for v in b[0])
        assert peak == pytest.approx(1.0, abs=0.001)

    def test_final_partial_block_is_yielded(self, tmp_path):
        p = write_signal(tmp_path / "a.wav", [sine(1000, 1.0, rate=8000)], rate=8000)
        _, blocks = read_audio(p, block_frames=3000)
        sizes = [len(b[0]) for b in blocks]
        assert sizes == [3000, 3000, 2000]


class TestFailures:
    def test_an_aiff_in_a_wav_path_names_the_container(self, tmp_path):
        """Audacity reuses its last exporter, so a .wav path can hold AIFF.
        'could not parse audio data' is the wrong thing to tell a caller."""
        from tests.test_audio_file import write_aiff
        p = write_aiff(tmp_path / "a.wav")
        info, blocks = read_audio(p)
        assert info.channels == 1

    def test_a_non_audio_file_raises_unreadable(self, tmp_path):
        p = tmp_path / "a.wav"
        p.write_bytes(b"not audio at all")
        with pytest.raises(UnreadableAudio):
            read_audio(str(p))
