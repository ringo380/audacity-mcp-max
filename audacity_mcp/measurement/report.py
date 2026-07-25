"""Assemble a measurement, compare two of them, and check against a target."""
from . import LOUDNESS_AVAILABLE, describe_capability
from .metrics import compute_metrics
from .targets import TargetSpec

# Fields compared by delta(). Counts and levels only - silence_gaps is a list
# of positions, which does not subtract meaningfully.
_DELTA_FIELDS = (
    "peak_db", "rms_db", "noise_floor_db", "lufs", "true_peak_dbtp",
    "dc_offset", "clipped_samples", "click_count", "silence_gap_count",
    "dynamic_range_db",
)

# Below this, a change is measurement noise rather than an effect doing
# something. Two exports of an untouched project do not agree bit for bit.
_NOISE_FLOOR_DB = 0.1


def measure_file(path: str, dual_mono: bool = True) -> dict:
    """Full measurement of one file. Loudness fields are None without the extra,
    and `unavailable` says why - never silently absent."""
    result = compute_metrics(path)
    result["lufs"] = None
    result["true_peak_dbtp"] = None
    result["unavailable"] = {}

    if not LOUDNESS_AVAILABLE:
        reason = describe_capability()["reason"]
        result["unavailable"] = {"lufs": reason, "true_peak": reason}
        return result

    from .loudness import integrated_lufs, true_peak_dbtp

    try:
        result["lufs"] = integrated_lufs(path, dual_mono=dual_mono)
        result["true_peak_dbtp"] = true_peak_dbtp(path)
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        result["unavailable"] = {"lufs": reason, "true_peak": reason}
    return result


def delta(before: dict, after: dict) -> dict:
    """What moved between two measurements.

    A field missing on either side is skipped rather than reported as a zero
    change - claiming 'no change' for something never measured is the same lie
    as reporting a missed target for it.
    """
    out: dict = {}
    moved = False
    for field in _DELTA_FIELDS:
        b, a = before.get(field), after.get(field)
        if b is None or a is None:
            continue
        change = round(a - b, 3)
        out[field] = {"before": b, "after": a, "change": change}
        if abs(change) > _NOISE_FLOOR_DB:
            moved = True
    if out and not moved:
        out["_no_measurable_change"] = True
    return out


def _bounded(name, actual, low, high, unavailable, advice, unit="dB"):
    if actual is None:
        return {
            "status": "unknown",
            "target": f"{low} to {high} {unit}",
            "reason": unavailable.get(name, "not measured"),
        }
    if low <= actual <= high:
        return {"status": "met", "actual": actual, "target": f"{low} to {high} {unit}"}
    gap = round(low - actual if actual < low else actual - high, 1)
    direction = "below" if actual < low else "above"
    return {
        "status": "missed",
        "actual": actual,
        "target": f"{low} to {high} {unit}",
        "gap": f"{abs(gap)} {unit} {direction} the target",
        "advice": advice,
    }


def _ceiling(name, actual, maximum, unavailable, advice, unit="dB"):
    if actual is None:
        return {
            "status": "unknown",
            "target": f"at or below {maximum} {unit}",
            "reason": unavailable.get(name, "not measured"),
        }
    if actual <= maximum:
        return {"status": "met", "actual": actual, "target": f"at or below {maximum} {unit}"}
    return {
        "status": "missed",
        "actual": actual,
        "target": f"at or below {maximum} {unit}",
        "gap": f"{round(actual - maximum, 1)} {unit} above the ceiling",
        "advice": advice,
    }


def check_targets(measurement: dict, spec: TargetSpec | None) -> dict:
    """Evaluate a measurement against a spec.

    An unmeasurable bound reports `unknown`, never `missed`. A target that could
    not be checked is not a target that failed.
    """
    if spec is None:
        return {}
    un = measurement.get("unavailable", {})
    out: dict = {}

    if spec.lufs_range:
        out["lufs"] = _bounded(
            "lufs", measurement.get("lufs"), *spec.lufs_range, un, spec.advice, "LUFS"
        )
    if spec.true_peak_max is not None:
        out["true_peak"] = _ceiling(
            "true_peak", measurement.get("true_peak_dbtp"), spec.true_peak_max,
            un, "reduce peaks, or apply a limiter, before publishing", "dBTP",
        )
    if spec.rms_range:
        out["rms"] = _bounded(
            "rms", measurement.get("rms_db"), *spec.rms_range, un, spec.advice
        )
    if spec.peak_max is not None:
        out["peak"] = _ceiling(
            "peak", measurement.get("peak_db"), spec.peak_max, un, spec.advice
        )
    if spec.noise_floor_max is not None:
        out["noise_floor"] = _ceiling(
            "noise_floor", measurement.get("noise_floor_db"), spec.noise_floor_max,
            un, spec.advice,
        )
    return out
