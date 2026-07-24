"""Synthesized signals with known ground truth.

A real recording proves nothing about a meter, because you do not know what its
true value is. A sine at a known amplitude does.
"""
import math
import struct
import wave


def write_signal(path, samples_per_channel, rate=44100, sampwidth=2):
    """Write float channels in -1.0..1.0 as interleaved 16-bit PCM.

    samples_per_channel: list of per-channel lists, all the same length.
    """
    channels = len(samples_per_channel)
    n = len(samples_per_channel[0])
    for ch in samples_per_channel:
        assert len(ch) == n, "channels must be the same length"
    interleaved = []
    for i in range(n):
        for ch in samples_per_channel:
            v = max(-1.0, min(1.0, ch[i]))
            interleaved.append(int(round(v * 32767.0)))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        wf.writeframes(b"".join(struct.pack("<h", s) for s in interleaved))
    return str(path)


def sine(freq, seconds, rate=44100, amplitude=1.0, phase=0.0):
    """A sine of a known peak amplitude in -1.0..1.0."""
    n = int(seconds * rate)
    w = 2.0 * math.pi * freq / rate
    return [amplitude * math.sin(w * i + phase) for i in range(n)]


def db_to_amplitude(db):
    """-23 dBFS -> the peak amplitude a sine needs to sit at that level."""
    return 10.0 ** (db / 20.0)


def silence(seconds, rate=44100):
    return [0.0] * int(seconds * rate)


def constant(value, seconds, rate=44100):
    return [value] * int(seconds * rate)
