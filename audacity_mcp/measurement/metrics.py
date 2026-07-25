"""Level and defect metrics. Works with or without numpy.

The noise floor here is deliberately not what the old _measure_wav computed.
That took the RMS of the opening 0.5 seconds, which is an assumption that the
recording starts with room tone; on a file that opens with speech it reported a
number far too high and several pipelines routed on it.

Two backends, and the numpy one is not an optimisation for its own sake:
reader.py hands this module numpy arrays whenever the extra is installed, and
iterating a numpy array element by element in Python is SLOWER than iterating a
list. Without a vectorised path the extra would make this half of the
measurement worse than not having it. Measured on this machine at 44.1 kHz
stereo, the Python loop costs about 160 seconds per hour of audio per pass
against about one second vectorised, and a verified pipeline runs two passes.

Both backends fill the same accumulator fields, so the tests assert the two give
identical answers rather than each being checked in isolation.
"""
import math

from . import HAVE_NUMPY
from .reader import read_audio

_CLIP_THRESHOLD = 0.999
_CLICK_JUMP = 0.3           # a sample-to-sample jump over 30% of full scale
_SILENCE_LEVEL = 0.001      # -60 dBFS
_MIN_GAP_SECONDS = 0.5
_NOISE_PERCENTILE = 10      # the 10th percentile of per-block RMS
_MAX_REPORTED_GAPS = 10


def _db(linear: float) -> float:
    return round(20.0 * math.log10(max(linear, 1e-10)), 1)


def _percentile(sorted_values: list, pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * pct / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


class _State:
    """Accumulators carried across blocks.

    Both backends fill exactly these fields. Keeping them in one object is what
    lets the two paths be compared assert-for-assert instead of each being
    trusted on its own.
    """

    def __init__(self, channels: int, rate: int):
        self.rate = rate
        self.peak = 0.0
        self.sum_sq = 0.0
        self.total_sum = 0.0
        self.count = 0
        self.clipped = 0
        self.clicks = 0
        self.prev = [0.0] * channels
        self.first_block = True
        self.block_rms = []      # linear RMS per block, for the percentiles
        self.silence_run = 0     # in frames
        self.gaps = []
        self.frame_pos = 0


def _scan_runs(is_silent, st: _State, n: int, boundaries):
    """Fold this block's silence runs into the carried run state.

    boundaries is the list of segment edges: [0, ...change points..., n]. Both
    backends produce it differently and share this fold, so a gap spanning a
    block boundary is accumulated the same way in each.
    """
    for i in range(len(boundaries) - 1):
        start = int(boundaries[i])
        end = int(boundaries[i + 1])
        if is_silent(start):
            st.silence_run += end - start
        else:
            _close_gap(st.gaps, st.silence_run, st.frame_pos + start, st.rate)
            st.silence_run = 0
    st.frame_pos += n


def _accumulate_numpy(block, st: _State):
    import numpy as np

    stacked = np.vstack([np.asarray(c, dtype=np.float64) for c in block])
    absv = np.abs(stacked)
    squares = np.square(stacked)
    n = stacked.shape[1]

    st.peak = max(st.peak, float(absv.max()))
    st.clipped += int((absv >= _CLIP_THRESHOLD).sum())
    block_sum_sq = float(squares.sum())
    st.sum_sq += block_sum_sq
    st.total_sum += float(stacked.sum())
    st.count += int(stacked.size)
    if n:
        st.block_rms.append(math.sqrt(block_sum_sq / stacked.size))

    # Clicks are per channel. Comparing the last sample of L against the first
    # of R would invent a click at every frame boundary of a stereo file.
    for c in range(stacked.shape[0]):
        chan = stacked[c]
        if st.first_block:
            diffs = np.abs(np.diff(chan))
        else:
            diffs = np.abs(np.diff(np.concatenate(([st.prev[c]], chan))))
        st.clicks += int((diffs > _CLICK_JUMP).sum())
        st.prev[c] = float(chan[-1])
    st.first_block = False

    # Silence is per frame, using the loudest channel in that frame, so a gap in
    # one channel of a stereo pair is not reported as silence.
    silent = absv.max(axis=0) < _SILENCE_LEVEL
    changes = np.flatnonzero(np.diff(silent.astype(np.int8))) + 1
    boundaries = np.concatenate(([0], changes, [n]))
    _scan_runs(lambda i: bool(silent[i]), st, n, boundaries)


def _accumulate_stdlib(block, st: _State):
    n = len(block[0])
    block_sum_sq = 0.0

    for c, chan in enumerate(block):
        prev = st.prev[c]
        first = st.first_block
        for s in chan:
            a = abs(s)
            if a > st.peak:
                st.peak = a
            if a >= _CLIP_THRESHOLD:
                st.clipped += 1
            sq = s * s
            st.sum_sq += sq
            block_sum_sq += sq
            st.total_sum += s
            st.count += 1
            if not first and abs(s - prev) > _CLICK_JUMP:
                st.clicks += 1
            prev = s
            first = False
        st.prev[c] = prev
    st.first_block = False

    if n:
        st.block_rms.append(math.sqrt(block_sum_sq / (n * len(block))))

    silent = [max(abs(chan[i]) for chan in block) < _SILENCE_LEVEL for i in range(n)]
    boundaries = [0]
    for i in range(1, n):
        if silent[i] != silent[i - 1]:
            boundaries.append(i)
    boundaries.append(n)
    _scan_runs(lambda i: silent[i], st, n, boundaries)


_accumulate = _accumulate_numpy if HAVE_NUMPY else _accumulate_stdlib


def compute_metrics(path: str) -> dict:
    """Measure a PCM file. Raises UnreadableAudio if it is not readable PCM."""
    info, blocks = read_audio(path)
    st = _State(info.channels, info.sample_rate)

    for block in blocks:
        if not block or len(block[0]) == 0:
            continue
        # Looked up on the module at call time, not bound into a local, so the
        # backend-agreement tests can monkeypatch this name and have it take
        # effect on the next call.
        _accumulate(block, st)

    _close_gap(st.gaps, st.silence_run, st.frame_pos, info.sample_rate)

    count = st.count
    rms_db = _db(math.sqrt(st.sum_sq / count)) if count else None
    dc_offset = round(st.total_sum / count, 6) if count else 0.0
    gaps = st.gaps

    noise_floor_db = None
    dynamic_range_db = None
    if st.block_rms:
        ordered = sorted(st.block_rms)
        noise_floor_db = _db(_percentile(ordered, _NOISE_PERCENTILE))
        if len(ordered) >= 2:
            # 95th vs 10th rather than max vs min: one clipped block should not
            # define the top of the range.
            dynamic_range_db = round(
                _db(_percentile(ordered, 95)) - noise_floor_db, 1
            )

    return {
        "duration": info.duration,
        "sample_rate": info.sample_rate,
        "channels": info.channels,
        "peak_db": _db(st.peak),
        "rms_db": rms_db,
        "noise_floor_db": noise_floor_db,
        "dc_offset": dc_offset,
        "clipped_samples": st.clipped,
        "click_count": st.clicks,
        "silence_gaps": gaps[:_MAX_REPORTED_GAPS],
        "silence_gap_count": len(gaps),
        "dynamic_range_db": dynamic_range_db,
    }


def _close_gap(gaps: list, run_frames: int, end_frame: int, rate: int):
    """Record a silence run if it was long enough to matter."""
    if run_frames >= int(rate * _MIN_GAP_SECONDS):
        start = (end_frame - run_frames) / rate
        gaps.append((round(max(start, 0.0), 2), round(run_frames / rate, 2)))
