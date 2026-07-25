"""Ground truth about an exported audio file, read from its own bytes.

Audacity uses the sticky last-used exporter, so a file named `.wav` can hold
AIFF data and Audacity will still report a successful WAV export. Every check
here reads the header rather than trusting the extension.

`aifc` was removed from the standard library in Python 3.13, so the AIFF side
is parsed directly.
"""

from __future__ import annotations

import os
import struct
import wave


class UnsupportedAudioFile(Exception):
    """The file is not PCM audio this module can read frames from."""


_MAGIC = [
    (b"RIFF", 8, b"WAVE", "wav"),
    (b"FORM", 8, b"AIFF", "aiff"),
    (b"FORM", 8, b"AIFC", "aiff"),
    (b"OggS", None, None, "ogg"),
    (b"fLaC", None, None, "flac"),
    (b"ID3", None, None, "mp3"),
]


def sniff_container(path: str) -> str:
    """Container actually present in the file: wav, aiff, ogg, flac, mp3, unknown."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return "unknown"
    for magic, form_off, form, name in _MAGIC:
        if not head.startswith(magic):
            continue
        if form is None:
            return name
        if head[form_off:form_off + len(form)] == form:
            return name
    # A bare MPEG frame sync, i.e. an mp3 with no ID3 tag.
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        return "mp3"
    return "unknown"


def _read_extended80(raw: bytes) -> float:
    """IEEE 754 80-bit extended float — how AIFF stores its sample rate."""
    exponent = struct.unpack(">H", raw[:2])[0]
    mantissa = struct.unpack(">Q", raw[2:10])[0]
    sign = -1 if exponent & 0x8000 else 1
    exponent &= 0x7FFF
    if exponent == 0 and mantissa == 0:
        return 0.0
    return sign * mantissa * 2.0 ** (exponent - 16383 - 63)


def _aiff_chunks(fh):
    """Yield (chunk_id, size, data_offset) for each chunk in an AIFF FORM.

    Positions are absolute and tracked here, so the caller is free to seek
    around inside a chunk before asking for the next one.
    """
    pos = 12
    while True:
        fh.seek(pos)
        header = fh.read(8)
        if len(header) < 8:
            return
        chunk_id, size = struct.unpack(">4sI", header)
        yield chunk_id, size, pos + 8
        pos += 8 + size + (size & 1)  # chunks are word-aligned


class AiffReader:
    """The slice of the `wave` reader interface the analysis code uses.

    readframes() returns little-endian bytes so callers can keep unpacking
    with the same '<' format they use for WAV.
    """

    def __init__(self, path: str):
        self._fh = open(path, "rb")
        try:
            self._parse()
        except Exception:
            self._fh.close()
            raise

    def _parse(self):
        comm = ssnd = None
        for chunk_id, size, offset in _aiff_chunks(self._fh):
            if chunk_id == b"COMM":
                self._fh.seek(offset)
                comm = self._fh.read(size)
            elif chunk_id == b"SSND":
                ssnd = (offset, size)
        if comm is None or ssnd is None:
            raise UnsupportedAudioFile("AIFF file has no COMM or SSND chunk")

        self._channels, frames, bits = struct.unpack(">HIH", comm[:8])
        self._rate = int(round(_read_extended80(comm[8:18])))
        self._sampwidth = bits // 8
        if self._sampwidth not in (1, 2):
            raise UnsupportedAudioFile(
                f"AIFF is {bits}-bit; only 8- and 16-bit AIFF can be measured directly"
            )
        if len(comm) >= 22 and comm[18:22] not in (b"", b"NONE", b"sowt"):
            raise UnsupportedAudioFile(
                f"AIFF-C compression '{comm[18:22].decode('ascii', 'replace')}' is not PCM"
            )
        # 'sowt' is little-endian PCM: already in the byte order callers want.
        self._byteswap = self._sampwidth == 2 and comm[18:22] != b"sowt"

        offset, size = ssnd
        self._fh.seek(offset)
        data_offset, _block_size = struct.unpack(">II", self._fh.read(8))
        self._data_start = offset + 8 + data_offset
        frame_bytes = self._sampwidth * self._channels
        available = (size - 8 - data_offset) // frame_bytes if frame_bytes else 0
        self._nframes = min(frames, max(available, 0))
        self._fh.seek(self._data_start)

    def getframerate(self) -> int:
        return self._rate

    def getnframes(self) -> int:
        return self._nframes

    def getsampwidth(self) -> int:
        return self._sampwidth

    def getnchannels(self) -> int:
        return self._channels

    def readframes(self, n: int) -> bytes:
        raw = self._fh.read(n * self._sampwidth * self._channels)
        if self._byteswap:
            arr = bytearray(raw)
            arr[0::2], arr[1::2] = arr[1::2], arr[0::2]
            return bytes(arr)
        return raw

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# WAV fmt-chunk format tags. `wave` only accepts PCM (1); the others it rejects
# with wave.Error, which is why float has to be parsed here.
_WAVE_FORMAT_PCM = 0x0001
_WAVE_FORMAT_IEEE_FLOAT = 0x0003
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE


def _riff_chunks(fh):
    """Yield (chunk_id, size, data_offset) for each chunk in a RIFF WAVE."""
    pos = 12
    while True:
        fh.seek(pos)
        header = fh.read(8)
        if len(header) < 8:
            return
        chunk_id, size = struct.unpack("<4sI", header)
        yield chunk_id, size, pos + 8
        pos += 8 + size + (size & 1)  # chunks are word-aligned


class FloatWavReader:
    """A `wave`-shaped reader for IEEE-float WAV, which `wave` refuses.

    Audacity's internal format is 32-bit float and its exporter is sticky, so a
    project exported once as float keeps producing float WAVs. `wave` rejects
    those outright (format tag 3), and a 4-byte width alone cannot tell float
    from int32 - so this reader exposes `sample_format = "float"` for the decode
    layer to branch on. Samples are already little-endian, as callers expect.
    """

    sample_format = "float"

    def __init__(self, path: str):
        self._fh = open(path, "rb")
        try:
            self._parse()
        except UnsupportedAudioFile:
            self._fh.close()
            raise
        except Exception as e:
            self._fh.close()
            raise UnsupportedAudioFile(f"unreadable float WAV: {e}") from e

    def _parse(self):
        fmt = None
        data = None
        for chunk_id, size, offset in _riff_chunks(self._fh):
            if chunk_id == b"fmt ":
                self._fh.seek(offset)
                fmt = self._fh.read(size)
            elif chunk_id == b"data":
                data = (offset, size)
        if fmt is None or data is None:
            raise UnsupportedAudioFile("WAV has no fmt or data chunk")
        if len(fmt) < 16:
            raise UnsupportedAudioFile("WAV fmt chunk is truncated")

        tag, channels, rate, _byte_rate, _block_align, bits = struct.unpack(
            "<HHIIHH", fmt[:16]
        )
        # WAVE_FORMAT_EXTENSIBLE hides the real format in the SubFormat GUID,
        # whose first two bytes are the format code.
        if tag == _WAVE_FORMAT_EXTENSIBLE:
            if len(fmt) < 26:
                raise UnsupportedAudioFile("EXTENSIBLE WAV fmt chunk is truncated")
            tag = struct.unpack("<H", fmt[24:26])[0]
        if tag != _WAVE_FORMAT_IEEE_FLOAT:
            raise UnsupportedAudioFile(
                f"WAV format tag {tag} is not IEEE float; only float is parsed here"
            )
        if bits != 32:
            raise UnsupportedAudioFile(
                f"{bits}-bit float WAV is not supported; only 32-bit float is"
            )

        self._channels = channels
        self._rate = rate
        self._sampwidth = 4
        offset, size = data
        frame_bytes = self._sampwidth * channels
        self._nframes = size // frame_bytes if frame_bytes else 0
        self._fh.seek(offset)

    def getframerate(self) -> int:
        return self._rate

    def getnframes(self) -> int:
        return self._nframes

    def getsampwidth(self) -> int:
        return self._sampwidth

    def getnchannels(self) -> int:
        return self._channels

    def readframes(self, n: int) -> bytes:
        return self._fh.read(n * self._sampwidth * self._channels)

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def open_pcm(path: str):
    """Open a PCM export for reading, whatever container it turned out to be.

    Raises UnsupportedAudioFile naming the real container, so a caller can say
    "the export produced AIFF" instead of "could not parse audio data".
    """
    container = sniff_container(path)
    if container == "wav":
        try:
            return wave.open(path, "rb")
        except (wave.Error, EOFError):
            # wave rejects any non-PCM tag (float is tag 3) with wave.Error, and
            # trips over a WAVE_FORMAT_EXTENSIBLE fmt chunk with EOFError.
            # Audacity's sticky exporter can leave a project emitting 32-bit
            # float WAV, so parse that ourselves rather than failing the
            # measurement.
            return FloatWavReader(path)
    if container == "aiff":
        return AiffReader(path)
    raise UnsupportedAudioFile(
        f"file is {container} data, not PCM WAV or AIFF: {path}"
    )


def describe(path: str) -> dict:
    """What is actually in the file: container, rate, channels, duration, size.

    Never raises — an unreadable file comes back with `container` set and the
    rest as None, because this is used to annotate a result, not to gate one.
    """
    info = {
        "container": sniff_container(path),
        "bytes": None,
        "sample_rate": None,
        "channels": None,
        "duration_seconds": None,
    }
    try:
        info["bytes"] = os.path.getsize(path)
    except OSError:
        return info
    try:
        with open_pcm(path) as reader:
            info["sample_rate"] = reader.getframerate()
            info["channels"] = reader.getnchannels()
            frames = reader.getnframes()
            if info["sample_rate"]:
                info["duration_seconds"] = round(frames / info["sample_rate"], 2)
    except (UnsupportedAudioFile, wave.Error, OSError, struct.error):
        pass
    return info
