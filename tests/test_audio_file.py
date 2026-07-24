import math
import struct
import wave

import pytest

from audacity_mcp_shared.audio_file import (
    UnsupportedAudioFile,
    describe,
    open_pcm,
    sniff_container,
)


def _extended80(value: float) -> bytes:
    """Encode a sample rate the way an AIFF COMM chunk stores it."""
    if value == 0:
        return b"\x00" * 10
    mantissa, exponent = math.frexp(value)
    return struct.pack(">HQ", exponent + 16382, int(mantissa * (1 << 64)))


def write_aiff(path, rate=44100, channels=1, samples=(0, 8000, -8000, 32000)):
    frames = len(samples) // channels
    comm = struct.pack(">HIH", channels, frames, 16) + _extended80(rate)
    ssnd = struct.pack(">II", 0, 0) + b"".join(struct.pack(">h", s) for s in samples)
    body = (
        b"AIFF"
        + b"COMM" + struct.pack(">I", len(comm)) + comm
        + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd
    )
    path.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    return str(path)


def write_wav(path, rate=44100, channels=1, samples=(0, 8000, -8000, 32000)):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return str(path)


class TestSniffContainer:
    def test_real_wav(self, tmp_path):
        assert sniff_container(write_wav(tmp_path / "a.wav")) == "wav"

    def test_aiff_wearing_a_wav_extension(self, tmp_path):
        """The trap this module exists for: Audacity's sticky exporter writes
        AIFF into a path the caller named .wav, and reports a WAV export."""
        assert sniff_container(write_aiff(tmp_path / "a.wav")) == "aiff"

    def test_ogg_flac_and_mp3(self, tmp_path):
        (tmp_path / "a.ogg").write_bytes(b"OggS" + b"\x00" * 20)
        (tmp_path / "a.flac").write_bytes(b"fLaC" + b"\x00" * 20)
        (tmp_path / "a.mp3").write_bytes(b"ID3\x03" + b"\x00" * 20)
        (tmp_path / "b.mp3").write_bytes(b"\xff\xfb" + b"\x00" * 20)
        assert sniff_container(str(tmp_path / "a.ogg")) == "ogg"
        assert sniff_container(str(tmp_path / "a.flac")) == "flac"
        assert sniff_container(str(tmp_path / "a.mp3")) == "mp3"
        assert sniff_container(str(tmp_path / "b.mp3")) == "mp3"

    def test_garbage_and_missing_files(self, tmp_path):
        (tmp_path / "junk").write_bytes(b"not audio at all")
        assert sniff_container(str(tmp_path / "junk")) == "unknown"
        assert sniff_container(str(tmp_path / "nope")) == "unknown"


class TestOpenPcm:
    def test_wav_reads_little_endian_frames(self, tmp_path):
        path = write_wav(tmp_path / "a.wav", samples=(0, 8000, -8000, 32000))
        with open_pcm(path) as reader:
            raw = reader.readframes(reader.getnframes())
        assert struct.unpack("<4h", raw) == (0, 8000, -8000, 32000)

    def test_aiff_frames_come_back_little_endian_too(self, tmp_path):
        """Callers unpack with '<' for both, so the reader does the swap."""
        path = write_aiff(tmp_path / "a.wav", samples=(0, 8000, -8000, 32000))
        with open_pcm(path) as reader:
            raw = reader.readframes(reader.getnframes())
        assert struct.unpack("<4h", raw) == (0, 8000, -8000, 32000)

    def test_aiff_header_fields(self, tmp_path):
        path = write_aiff(tmp_path / "a.aiff", rate=48000, channels=2,
                          samples=(1, 2, 3, 4, 5, 6))
        with open_pcm(path) as reader:
            assert reader.getframerate() == 48000
            assert reader.getnchannels() == 2
            assert reader.getsampwidth() == 2
            assert reader.getnframes() == 3

    def test_unusual_rate_survives_the_extended_float(self, tmp_path):
        path = write_aiff(tmp_path / "a.aiff", rate=384000)
        with open_pcm(path) as reader:
            assert reader.getframerate() == 384000

    def test_non_pcm_container_names_what_it_found(self, tmp_path):
        (tmp_path / "a.wav").write_bytes(b"OggS" + b"\x00" * 20)
        with pytest.raises(UnsupportedAudioFile) as exc:
            open_pcm(str(tmp_path / "a.wav"))
        assert "ogg" in str(exc.value)


class TestDescribe:
    def test_wav(self, tmp_path):
        info = describe(write_wav(tmp_path / "a.wav", rate=44100, samples=tuple(i % 1000 for i in range(44100))))
        assert info["container"] == "wav"
        assert info["sample_rate"] == 44100
        assert info["channels"] == 1
        assert info["duration_seconds"] == 1.0
        assert info["bytes"] > 44100

    def test_aiff_in_a_wav_path(self, tmp_path):
        info = describe(write_aiff(tmp_path / "a.wav", rate=48000))
        assert info["container"] == "aiff"
        assert info["sample_rate"] == 48000

    def test_unreadable_file_still_returns_a_shape(self, tmp_path):
        (tmp_path / "a.wav").write_bytes(b"RIFFxxxxWAVEtruncated")
        info = describe(str(tmp_path / "a.wav"))
        assert info["container"] == "wav"
        assert info["sample_rate"] is None
        assert info["bytes"] == 21

    def test_missing_file(self, tmp_path):
        info = describe(str(tmp_path / "gone.wav"))
        assert info["container"] == "unknown"
        assert info["bytes"] is None
