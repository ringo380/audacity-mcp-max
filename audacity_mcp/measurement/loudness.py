"""ITU-R BS.1770-4 integrated loudness and true peak.

Needs the measurement extra. K-weighting is an IIR filter and the recursion
cannot be vectorised in numpy alone, so scipy.signal.sosfilt is what makes this
viable on an hour of audio.
"""
import math

from . import LOUDNESS_AVAILABLE
from .reader import read_audio

# BS.1770-4 block gating
_BLOCK_SECONDS = 0.400
_OVERLAP = 0.75
_ABSOLUTE_GATE_LUFS = -70.0
_RELATIVE_GATE_LU = -10.0
_OFFSET = -0.691  # the BS.1770 loudness offset

# Channel weights: L, R, C are unity; surrounds are 1.41. This code only sees
# mono and stereo (Export2 is asked for 1 or 2 channels), so unity throughout.
_CHANNEL_WEIGHT = 1.0


class LoudnessUnavailable(Exception):
    """numpy or scipy is missing, so loudness cannot be measured."""


def _require():
    if not LOUDNESS_AVAILABLE:
        from . import describe_capability
        raise LoudnessUnavailable(describe_capability()["reason"])


def k_weighting_sos(sample_rate: float):
    """The two K-weighting stages as a (2, 6) SOS array, designed for this rate.

    The coefficient tables in BS.1770 are given at 48 kHz only. Applying them
    unchanged to 44.1 kHz material shifts the answer by a few tenths of a dB -
    wrong in a way that still looks plausible, which is the worst kind. Both
    stages are therefore derived from the analog prototype for the actual rate.
    """
    _require()
    import numpy as np

    # Stage 1: high-frequency shelf.
    f0 = 1681.974450955533
    gain_db = 3.999843853973347
    q = 0.7071752369554196

    k = math.tan(math.pi * f0 / sample_rate)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh ** 0.4996667741545416
    denom = 1.0 + k / q + k * k
    shelf_b = [
        (vh + vb * k / q + k * k) / denom,
        2.0 * (k * k - vh) / denom,
        (vh - vb * k / q + k * k) / denom,
    ]
    shelf_a = [
        1.0,
        2.0 * (k * k - 1.0) / denom,
        (1.0 - k / q + k * k) / denom,
    ]

    # Stage 2: RLB high-pass.
    f0 = 38.13547087602444
    q = 0.5003270373238773
    k = math.tan(math.pi * f0 / sample_rate)
    denom = 1.0 + k / q + k * k
    hp_b = [1.0, -2.0, 1.0]
    hp_a = [
        1.0,
        2.0 * (k * k - 1.0) / denom,
        (1.0 - k / q + k * k) / denom,
    ]

    return np.array([shelf_b + shelf_a, hp_b + hp_a], dtype=np.float64)


def _load_channels(path: str):
    """Whole-file per-channel float arrays, plus the AudioInfo."""
    import numpy as np

    info, blocks = read_audio(path)
    collected = [[] for _ in range(info.channels)]
    for block in blocks:
        for c, chan in enumerate(block):
            collected[c].append(np.asarray(chan, dtype=np.float64))
    channels = [
        np.concatenate(parts) if parts else np.zeros(0, dtype=np.float64)
        for parts in collected
    ]
    return info, channels


def integrated_lufs(path: str, dual_mono: bool = True) -> float | None:
    """Gated integrated loudness in LUFS, or None if nothing survives the gate.

    dual_mono duplicates a mono file to two channels, which is what Audacity's
    LoudnessNormalization does by default. Using the other convention would put
    this meter ~3 dB away from the target Audacity had just normalised to.
    """
    _require()
    import numpy as np
    from scipy.signal import sosfilt

    info, channels = _load_channels(path)
    if not channels or channels[0].size == 0:
        return None

    if dual_mono and len(channels) == 1:
        channels = [channels[0], channels[0]]

    sos = k_weighting_sos(info.sample_rate)
    filtered = [sosfilt(sos, ch) for ch in channels]

    block_frames = int(round(_BLOCK_SECONDS * info.sample_rate))
    step = int(round(block_frames * (1.0 - _OVERLAP)))
    n = filtered[0].size
    if n < block_frames or step <= 0:
        return None

    starts = np.arange(0, n - block_frames + 1, step)
    if starts.size == 0:
        return None

    # z[j] = the weighted mean square of block j, summed over channels.
    z = np.zeros(starts.size, dtype=np.float64)
    for ch in filtered:
        squares = ch * ch
        cumulative = np.concatenate(([0.0], np.cumsum(squares)))
        block_sums = cumulative[starts + block_frames] - cumulative[starts]
        z += _CHANNEL_WEIGHT * (block_sums / block_frames)

    with np.errstate(divide="ignore"):
        loudness = _OFFSET + 10.0 * np.log10(np.maximum(z, 1e-20))

    above_absolute = loudness > _ABSOLUTE_GATE_LUFS
    if not above_absolute.any():
        return None

    ungated_mean = z[above_absolute].mean()
    relative_gate = (
        _OFFSET + 10.0 * math.log10(max(ungated_mean, 1e-20)) + _RELATIVE_GATE_LU
    )
    kept = above_absolute & (loudness > relative_gate)
    if not kept.any():
        return None

    return round(_OFFSET + 10.0 * math.log10(max(z[kept].mean(), 1e-20)), 2)


def true_peak_dbtp(path: str, oversample: int = 4) -> float | None:
    """Peak in dBTP, per BS.1770-4 Annex 2.

    A sample-peak meter misses the crest that falls between two samples, which
    is exactly what a -1 dBTP ceiling exists to catch: a file that reads -1.0
    dBFS can reconstruct above 0 in a converter and clip on playback. Annex 2
    calls for at least 4x oversampling for rates up to 48 kHz.
    """
    _require()
    import numpy as np
    from scipy.signal import resample_poly

    _, channels = _load_channels(path)
    if not channels or channels[0].size == 0:
        return None

    peak = 0.0
    for ch in channels:
        if ch.size == 0:
            continue
        upsampled = resample_poly(ch, oversample, 1)
        channel_peak = float(np.max(np.abs(upsampled)))
        if channel_peak > peak:
            peak = channel_peak

    if peak <= 1e-10:
        return None
    return round(20.0 * math.log10(peak), 2)
