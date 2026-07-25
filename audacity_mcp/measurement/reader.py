"""Read a PCM file as blocks of per-channel float samples.

One interface, two backends. Everything above this module is written once
against blocks, so adding numpy did not fork the metrics.
"""
import struct
from dataclasses import dataclass
from typing import Iterator

from audacity_mcp_shared.audio_file import UnsupportedAudioFile, open_pcm

from . import HAVE_NUMPY


class UnreadableAudio(Exception):
    """The file is not readable PCM. Names the container where it can."""


@dataclass(frozen=True)
class AudioInfo:
    sample_rate: int
    channels: int
    frames: int
    duration: float


# Keyed on (sample width in bytes, is_float). A 4-byte width is ambiguous -
# int32 and float32 are both 4 bytes - so the container reader tells us which
# via its `sample_format`. Decoding int32 bytes as float (or the reverse) reads
# as garbage but succeeds, which is why the two are kept apart here.
# struct has no three-byte integer, so 24-bit PCM carries this sentinel instead
# of a struct format character and both decoders special-case it. Audacity's
# export dialog offers "Signed 24-bit PCM" and its exporter is sticky, so a user
# who chose it once keeps producing it - the same way the float32 case arises.
_INT24 = "i24"

_FORMATS = {
    (2, False): ("h", 32768.0),
    (3, False): (_INT24, 8388608.0),
    (4, False): ("i", 2147483648.0),
    (4, True): ("f", 1.0),
}


def read_audio(
    path: str, block_frames: int = 0
) -> tuple[AudioInfo, Iterator[list]]:
    """Return (AudioInfo, block iterator).

    block_frames defaults to one second, which is also the granularity the
    dynamic-range and noise-floor percentiles want.
    """
    try:
        wf = open_pcm(path)
    except UnsupportedAudioFile as e:
        raise UnreadableAudio(str(e)) from e
    except Exception as e:
        raise UnreadableAudio(f"{type(e).__name__}: {e}") from e

    try:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        frames = wf.getnframes()
        sw = wf.getsampwidth()
    except Exception as e:
        wf.close()
        raise UnreadableAudio(f"{type(e).__name__}: {e}") from e

    # A 4-byte width can be int32 or float32; the container reader disambiguates.
    is_float = getattr(wf, "sample_format", "int") == "float"
    fmt = _FORMATS.get((sw, is_float))
    if fmt is None:
        wf.close()
        kind = "float" if is_float else "int"
        raise UnreadableAudio(f"unsupported sample format: {sw} bytes, {kind}")
    if rate <= 0 or channels <= 0:
        wf.close()
        raise UnreadableAudio(f"nonsense header: rate={rate} channels={channels}")

    info = AudioInfo(
        sample_rate=rate,
        channels=channels,
        frames=frames,
        duration=round(frames / rate, 3) if rate else 0.0,
    )
    size = block_frames if block_frames > 0 else rate
    return info, _iter_blocks(wf, info, sw, fmt, size)


def _iter_blocks(wf, info: AudioInfo, sw: int, fmt, block_frames: int) -> Iterator[list]:
    fmt_char, max_val = fmt
    ch = info.channels
    try:
        read = 0
        while read < info.frames:
            n = min(block_frames, info.frames - read)
            raw = wf.readframes(n)
            got = len(raw) // (sw * ch)
            if got == 0:
                break
            yield _decode(raw, got, ch, fmt_char, max_val)
            read += got
    finally:
        wf.close()


def _decode_stdlib(raw, frames, channels, fmt_char, max_val):
    # struct.unpack requires the buffer length to match the format exactly, so
    # trim any trailing partial-frame bytes first. The numpy backend's
    # frombuffer(count=...) already ignores them; without this slice a
    # misaligned read (a corrupt AIFF SSND can produce one) raises struct.error
    # from the stdlib backend only, so the measurement would succeed or fail
    # depending on which optional extra is installed.
    n = frames * channels
    if fmt_char == _INT24:
        raw = raw[: n * 3]
        flat = [
            int.from_bytes(raw[i:i + 3], "little", signed=True)
            for i in range(0, len(raw), 3)
        ]
    else:
        raw = raw[: n * struct.calcsize(fmt_char)]
        flat = struct.unpack(f"<{n}{fmt_char}", raw)
    return [[flat[i] / max_val for i in range(c, len(flat), channels)]
            for c in range(channels)]


if HAVE_NUMPY:
    import numpy as _np

    _DTYPES = {"h": "<i2", "i": "<i4", "f": "<f4"}

    def _decode_numpy(raw, frames, channels, fmt_char, max_val):
        n = frames * channels
        if fmt_char == _INT24:
            # Widen each little-endian 3-byte sample into the high three bytes
            # of an int32 and shift back down, so the arithmetic shift carries
            # the sign for us rather than testing the top bit per sample.
            triples = _np.frombuffer(raw, dtype=_np.uint8, count=n * 3).reshape(-1, 3)
            wide = _np.zeros((triples.shape[0], 4), dtype=_np.uint8)
            wide[:, 1:] = triples
            flat = wide.view("<i4").reshape(-1) >> 8
        else:
            flat = _np.frombuffer(raw, dtype=_DTYPES[fmt_char], count=n)
        arr = flat.astype(_np.float64) / max_val
        # (frames, channels) -> a list of per-channel 1-D views.
        return [_np.ascontiguousarray(arr[c::channels]) for c in range(channels)]

else:
    _decode_numpy = None


# Both decoders are always named so the two can be compared directly (see the
# backend-agreement tests); _iter_blocks reads this module-level name at call
# time, mirroring metrics._accumulate.
_decode = _decode_numpy if HAVE_NUMPY else _decode_stdlib
