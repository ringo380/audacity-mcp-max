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


# `wave` refuses IEEE-float WAV (format tag 3) outright, so a 4-byte width that
# reaches us is always signed int32 PCM - decode it as such rather than
# reinterpreting the bytes as float32, which read as garbage but succeeded.
# 32-bit-float exports stay unreadable here until the export path is wired in
# (Task 7) and can decide whether to sniff the fmt tag.
_FORMATS = {2: ("h", 32768.0), 4: ("i", 2147483648.0)}


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

    if sw not in _FORMATS:
        wf.close()
        raise UnreadableAudio(f"unsupported sample width: {sw} bytes")
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
    return info, _iter_blocks(wf, info, sw, size)


def _iter_blocks(wf, info: AudioInfo, sw: int, block_frames: int) -> Iterator[list]:
    fmt_char, max_val = _FORMATS[sw]
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
    raw = raw[: n * struct.calcsize(fmt_char)]
    flat = struct.unpack(f"<{n}{fmt_char}", raw)
    return [[flat[i] / max_val for i in range(c, len(flat), channels)]
            for c in range(channels)]


if HAVE_NUMPY:
    import numpy as _np

    _DTYPES = {"h": "<i2", "i": "<i4"}

    def _decode_numpy(raw, frames, channels, fmt_char, max_val):
        flat = _np.frombuffer(raw, dtype=_DTYPES[fmt_char], count=frames * channels)
        arr = flat.astype(_np.float64) / max_val
        # (frames, channels) -> a list of per-channel 1-D views.
        return [_np.ascontiguousarray(arr[c::channels]) for c in range(channels)]

else:
    _decode_numpy = None


# Both decoders are always named so the two can be compared directly (see the
# backend-agreement tests); _iter_blocks reads this module-level name at call
# time, mirroring metrics._accumulate.
_decode = _decode_numpy if HAVE_NUMPY else _decode_stdlib
