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

# Input samples of context carried across chunk boundaries for true peak.
_TP_PAD = 64


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


def _chunks(path: str):
    """(AudioInfo, iterator of per-channel float64 arrays).

    read_audio hands back a second of audio at a time and this keeps it that
    way. An earlier version concatenated the lot into one array per channel,
    which cost about 10 MB of resident memory per second of audio once the 4x
    oversampling for true peak was included - roughly 18 GB on a 45-minute
    podcast, which is exactly the length this plugin's pipelines are for. It did
    not read as a memory bug either: MemoryError is caught upstream, so the
    result was loudness quietly reporting "unavailable" on every real file while
    passing every test, because the test files are seconds long.
    """
    import numpy as np

    info, blocks = read_audio(path)

    def gen():
        for block in blocks:
            yield [np.asarray(chan, dtype=np.float64) for chan in block]

    return info, gen()


def integrated_lufs(path: str, dual_mono: bool = True) -> float | None:
    """Gated integrated loudness in LUFS, or None if nothing survives the gate.

    dual_mono duplicates a mono file to two channels, which is what Audacity's
    LoudnessNormalization does by default. Using the other convention would put
    this meter ~3 dB away from the target Audacity had just normalised to.
    """
    _require()
    import numpy as np
    from scipy.signal import sosfilt

    info, chunks = _chunks(path)

    block_frames = int(round(_BLOCK_SECONDS * info.sample_rate))
    step = int(round(block_frames * (1.0 - _OVERLAP)))
    if block_frames <= 0 or step <= 0:
        return None

    sos = k_weighting_sos(info.sample_rate)

    # The K-weighting recursion has to run over the whole signal, so each chunk
    # picks up where the last one left off (zi) rather than restarting the
    # filter - restarting would put a transient at every chunk boundary.
    state = None
    # Held filtered samples that no complete gating block has consumed yet.
    held: list = []
    held_origin = 0      # index in the filtered stream of held[c][0]
    next_start = 0       # start of the next gating block, on the global grid
    weighted: list = []  # per block: the weighted mean square, summed over channels

    for chunk in chunks:
        if dual_mono and len(chunk) == 1:
            # Duplicating the array would double the memory for an identical
            # result; the channel sum is what dual mono actually means here.
            weight = 2.0 * _CHANNEL_WEIGHT
        else:
            weight = _CHANNEL_WEIGHT
        if not chunk or chunk[0].size == 0:
            continue

        if state is None:
            # Zeros, not the step-response state sosfilt_zi gives: the filter
            # starts cold at the head of the file, which is what the reference
            # implementations and the EBU fixtures assume.
            state = [np.zeros((sos.shape[0], 2)) for _ in chunk]
            held = [np.zeros(0, dtype=np.float64) for _ in chunk]

        buffers = []
        for c, ch in enumerate(chunk):
            y, state[c] = sosfilt(sos, ch, zi=state[c])
            buffers.append(np.concatenate((held[c], y)))

        available = held_origin + buffers[0].size
        while next_start + block_frames <= available:
            local = next_start - held_origin
            total = 0.0
            for buf in buffers:
                seg = buf[local:local + block_frames]
                total += weight * (float(np.dot(seg, seg)) / block_frames)
            weighted.append(total)
            next_start += step

        drop = next_start - held_origin
        held = [buf[drop:] for buf in buffers]
        held_origin = next_start

    if not weighted:
        return None
    z = np.asarray(weighted, dtype=np.float64)

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

    _, chunks = _chunks(path)

    peak = 0.0
    # Carried context, so the resampler never sees a fabricated edge in a region
    # whose peak is then counted. resample_poly's anti-imaging filter is about
    # 10*oversample taps at the upsampled rate, so _TP_PAD input samples is
    # several times the history it needs.
    pad = _TP_PAD
    carry: list = []
    first = True

    def take(chunk, last):
        nonlocal peak, carry, first
        if first:
            carry = [np.zeros(0, dtype=np.float64) for _ in chunk]
        for c, ch in enumerate(chunk):
            seg = np.concatenate((carry[c], ch))
            out = np.abs(resample_poly(seg, oversample, 1))
            # Skip the head unless this is genuinely the start of the file, and
            # skip the tail unless this is the end of it. Those samples have no
            # future context yet, and are covered by the next chunk instead -
            # its carry holds 2*pad samples, so the two valid ranges meet rather
            # than leaving a gap. Reading a peak out of a region the resampler
            # filtered against fabricated zeros is how an edge becomes an
            # overshoot, and a true-peak ceiling exists to catch overshoots.
            lo = 0 if first else pad * oversample
            hi = out.size if last else max(lo, out.size - pad * oversample)
            if hi > lo:
                chunk_peak = float(out[lo:hi].max())
                if chunk_peak > peak:
                    peak = chunk_peak
            carry[c] = seg[-2 * pad:] if seg.size >= 2 * pad else seg
        first = False

    # One chunk of lookahead, purely so the final chunk can be told it is final.
    pending = None
    for chunk in chunks:
        if not chunk or chunk[0].size == 0:
            continue
        if pending is not None:
            take(pending, last=False)
        pending = chunk
    if pending is not None:
        take(pending, last=True)

    if peak <= 1e-10:
        return None
    return round(20.0 * math.log10(peak), 2)
