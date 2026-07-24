"""Audio measurement, isolated from the MCP layer.

Everything here is a pure function of a file path - no client, no async, no
FastMCP. That is what lets the BS.1770 loudness math be tested against the EBU
compliance signals with Audacity nowhere in the picture.

numpy and scipy are optional. Without them the stdlib metrics still work and
loudness reports as unavailable: an unmeasurable target is not a missed one,
and reporting it as missed would be exactly the dishonesty this package exists
to remove.
"""

try:  # pragma: no cover - exercised by the extra being present or absent
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

try:  # pragma: no cover
    import scipy.signal  # noqa: F401
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False

# K-weighting is an IIR filter, so the recursion cannot be vectorised in numpy
# alone - a pure-numpy biquad is still a per-sample Python loop, which is tens
# of minutes on an hour of audio. scipy.signal.sosfilt runs it at C speed, so
# loudness needs both.
LOUDNESS_AVAILABLE = HAVE_NUMPY and HAVE_SCIPY

_INSTALL_HINT = (
    "install the measurement extra: "
    "uv sync --extra measurement (or pip install 'audacity-mcp-max[measurement]')"
)


def describe_capability() -> dict:
    """What this install can and cannot measure, and how to fix it."""
    if LOUDNESS_AVAILABLE:
        return {"numpy": True, "scipy": True, "loudness": True, "reason": ""}
    missing = [n for n, ok in (("numpy", HAVE_NUMPY), ("scipy", HAVE_SCIPY)) if not ok]
    return {
        "numpy": HAVE_NUMPY,
        "scipy": HAVE_SCIPY,
        "loudness": False,
        "reason": f"LUFS and true peak need {' and '.join(missing)} - {_INSTALL_HINT}",
    }
