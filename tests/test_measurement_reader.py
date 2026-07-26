import struct
import wave

import pytest

from audacity_mcp.measurement import reader as reader_mod
from audacity_mcp.measurement.reader import read_audio, UnreadableAudio
from tests.measurement_signals import sine, write_signal


def write_int24(path, values, rate=8000):
    """A mono 24-bit PCM WAV, little-endian, three bytes per sample."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(3)
        wf.setframerate(rate)
        wf.writeframes(b"".join(
            int(v).to_bytes(3, "little", signed=True) for v in values
        ))
    return str(path)


def write_int32(path, values, rate=8000):
    """A mono 32-bit *integer* PCM WAV. wave refuses IEEE-float WAV, so this is
    the only 4-byte file the reader ever actually sees."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(4)
        wf.setframerate(rate)
        wf.writeframes(b"".join(struct.pack("<i", v) for v in values))
    return str(path)


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


class TestSampleWidths:
    def test_int32_pcm_normalises_to_unit_range(self, tmp_path):
        """A 4-byte int32 width must read as +/-1.0, not as float bytes divided
        by 1.0 - which would report peaks in the billions and clip everything."""
        p = write_int32(tmp_path / "i32.wav",
                        [2147483647, -2147483648, 0, 1073741824])
        _, blocks = read_audio(p, block_frames=8000)
        got = [round(float(v), 4) for b in blocks for v in b[0]]
        assert got == [1.0, -1.0, 0.0, 0.5]

    def test_int24_pcm_normalises_to_unit_range(self, tmp_path):
        """Audacity's export dialog offers "Signed 24-bit PCM" and its exporter
        is sticky, so a user who picked it once keeps producing it. struct has
        no three-byte integer, which is why this width used to fail outright and
        take the whole measurement with it.
        """
        p = write_int24(tmp_path / "i24.wav",
                        [8388607, -8388608, 0, 4194304])
        _, blocks = read_audio(p, block_frames=8000)
        got = [round(float(v), 4) for b in blocks for v in b[0]]
        assert got == [1.0, -1.0, 0.0, 0.5]

    def test_int24_still_reads_when_wave_cannot_open_extensible(
        self, tmp_path, monkeypatch
    ):
        """24-bit is the width libsndfile most often writes as
        WAVE_FORMAT_EXTENSIBLE, and `wave` only learned that header in Python
        3.12 while this project supports 3.10. Without the fallback reading PCM
        as well as float, 24-bit support is unreachable on the two oldest
        supported interpreters - the case it was added for."""
        from tests.test_audio_file import write_extensible_pcm_wav

        p = write_extensible_pcm_wav(
            tmp_path / "x24.wav", rate=8000, bits=24,
            samples=[8388607, -8388608, 0, 4194304],
        )
        monkeypatch.setattr(
            wave, "open",
            lambda *a, **k: (_ for _ in ()).throw(wave.Error("unknown format: 65534")),
        )
        _, blocks = read_audio(p, block_frames=8000)
        got = [round(float(v), 4) for b in blocks for v in b[0]]
        assert got == [1.0, -1.0, 0.0, 0.5]

    def test_both_int24_decoders_agree(self, tmp_path, monkeypatch):
        """The sign extension is written twice, once per backend, so nothing but
        a direct comparison catches them drifting apart. The negative values are
        the point: a byte-order or sign slip reads them as large positives."""
        if not reader_mod.HAVE_NUMPY:
            pytest.skip("only one backend available on this install")
        values = [8388607, -8388608, -1, 0, 1, -4194304, 4194304]
        p = write_int24(tmp_path / "i24b.wav", values)

        monkeypatch.setattr(reader_mod, "_decode", reader_mod._decode_numpy)
        fast = [float(v) for b in read_audio(p, block_frames=8000)[1] for v in b[0]]
        monkeypatch.setattr(reader_mod, "_decode", reader_mod._decode_stdlib)
        slow = [float(v) for b in read_audio(p, block_frames=8000)[1] for v in b[0]]
        assert fast == slow
        assert slow == [pytest.approx(v / 8388608.0) for v in values]

    def test_float32_wav_is_read_as_float_not_int32(self, tmp_path):
        """int32 and float32 are both 4 bytes; the container reader says which.
        Float samples are already in -1.0..1.0, so decoding them as int32 (or
        the reverse) reads as garbage while still succeeding - which is why the
        two must not collapse to the same code path."""
        from tests.test_audio_file import write_float_wav
        p = write_float_wav(tmp_path / "f.wav", rate=48000,
                            samples=(1.0, -1.0, 0.0, 0.5, -0.25))
        info, blocks = read_audio(p, block_frames=48000)
        got = [round(float(v), 4) for b in blocks for v in b[0]]
        assert got == [1.0, -1.0, 0.0, 0.5, -0.25]
        assert info.sample_rate == 48000


class TestPartialFrame:
    def test_stdlib_decode_trims_a_trailing_partial_frame(self):
        """A misaligned read (a corrupt AIFF SSND can return one) must decode
        the whole frames it has and drop the stray bytes. struct.unpack rejects
        a buffer whose length does not match the format exactly, so without the
        slice this raises struct.error - and only from the stdlib backend, so
        the two backends would disagree on the same input."""
        raw = struct.pack("<3h", 100, 200, 300) + b"\x01"  # 3 frames + 1 stray byte
        out = reader_mod._decode_stdlib(raw, 3, 1, "h", 32768.0)
        assert [round(v, 4) for v in out[0]] == [round(x / 32768.0, 4)
                                                 for x in (100, 200, 300)]

    def test_both_decoders_agree_on_a_partial_frame(self):
        if reader_mod._decode_numpy is None:
            pytest.skip("numpy backend not installed")
        raw = struct.pack("<3h", 100, 200, 300) + b"\x01"
        slow = reader_mod._decode_stdlib(raw, 3, 1, "h", 32768.0)
        fast = reader_mod._decode_numpy(raw, 3, 1, "h", 32768.0)
        assert [round(v, 6) for v in slow[0]] == [round(float(v), 6) for v in fast[0]]


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
