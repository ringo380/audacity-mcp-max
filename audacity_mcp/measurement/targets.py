"""Declared numeric targets, one per pipeline.

The pipelines deliberately do not chase these. The safe loudness step is peak
only and never boosts, because a blind LUFS boost on quiet material can add
20-30 dB and destroy the file. So a podcast run routinely lands short of -16
LUFS, and the honest report is "here is how far off, here is what closes it" -
not a pass/fail verdict on a pipeline that never promised to get there.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TargetSpec:
    label: str
    advice: str
    lufs_range: tuple | None = None
    true_peak_max: float | None = None
    rms_range: tuple | None = None
    peak_max: float | None = None
    noise_floor_max: float | None = None


_SPEECH_ADVICE = (
    "run loudness_normalize(-16) to close the gap, then re-check - it boosts as "
    "well as reduces, so only use it on audio with healthy levels"
)

_SPEECH = TargetSpec(
    label="speech / broadcast",
    advice=_SPEECH_ADVICE,
    lufs_range=(-17.0, -15.0),
    true_peak_max=-1.0,
)


def _music(label: str, low: float, high: float) -> TargetSpec:
    return TargetSpec(
        label=label,
        advice=(
            f"run loudness_normalize({round((low + high) / 2)}) to reach the "
            f"{label} range, then check true peak stays at or below -1 dBTP"
        ),
        lufs_range=(low, high),
        true_peak_max=-1.0,
    )


SPECS: dict[str, TargetSpec | None] = {
    "podcast_cleanup": _SPEECH,
    "interview_cleanup": _SPEECH,
    "vocal_cleanup": _SPEECH,
    "live_cleanup": _SPEECH,

    # ACX/Audible: RMS -23 to -18 dBFS, peaks below -3 dBFS, noise floor below
    # -60 dBFS. This is an external spec, not house style.
    "audiobook_mastering": TargetSpec(
        label="ACX / Audible",
        advice=(
            "ACX rejects outside these bounds. If RMS is low run normalize then "
            "re-run this pipeline; if the noise floor is high run noise_reduction "
            "with a clean room-tone sample at the head of the file"
        ),
        rms_range=(-23.0, -18.0),
        peak_max=-3.0,
        noise_floor_max=-60.0,
    ),

    "mastering_edm": _music("EDM / electronic", -11.0, -9.0),
    "mastering_hiphop": _music("hip-hop / rap", -11.0, -9.0),
    "mastering_rock": _music("rock", -14.0, -11.0),
    "mastering_pop": _music("pop", -14.0, -11.0),
    "mastering_acoustic": _music("acoustic / chill", -18.0, -16.0),
    "mastering_classical": _music("classical / orchestral", -18.0, -16.0),

    # No target by design. auto_cleanup_audio is explicitly "just clean" - no
    # compression, no normalize - and a creative effect has no correctness
    # target to check against. Both report delta only.
    "cleanup_audio": None,
    "lofi_effect": None,
}


def for_pipeline(name: str) -> TargetSpec | None:
    """The spec for a pipeline name, or None for both 'no target declared' and
    'not a known pipeline'. Callers treat the two the same: report delta only.
    """
    return SPECS.get(name)
