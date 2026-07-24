"""faster-whisper is an extra, not a base dependency.

The 7 transcription tools still register and stay self-describing; they fail at
call time with an error that names the fix. The point of the test is that the
base install stays small: a user cleaning up a recording should not pay for
ctranslate2 and onnxruntime to get there.
"""
import pathlib

import pytest

from audacity_mcp_shared.error_codes import AudacityMCPError

PYPROJECT = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"


def dependency_array(name):
    """The entries of a `name = [...]` array in pyproject.toml, or None.

    Scanned line by line rather than matched with `\\[(.*?)\\]`: that pattern
    stops at the `]` inside "mcp[cli]>=1.0.0", so it returns a truncated prefix
    that cannot contain the requirement under test, and the assertion below
    passes no matter what pyproject actually declares.
    """
    lines = PYPROJECT.read_text().splitlines()
    for index, line in enumerate(lines):
        if line.strip() != name + " = [":
            continue
        entries = []
        for entry in lines[index + 1 :]:
            if entry.strip() == "]":
                return entries
            entries.append(entry.strip())
        raise AssertionError(name + " array is never closed")
    return None


def test_faster_whisper_is_not_a_base_dependency():
    base = dependency_array("dependencies")
    assert base is not None, "pyproject has no dependencies array"
    # Anchors the scan: without it, a parse that found nothing would satisfy
    # the real assertion for the wrong reason.
    assert any("mcp[cli]" in entry for entry in base), base
    assert not any("faster-whisper" in entry for entry in base), base


def test_faster_whisper_is_declared_as_the_transcription_extra():
    extra = dependency_array("transcription")
    assert extra is not None, "pyproject has no transcription extra"
    assert any("faster-whisper" in entry for entry in extra), extra


def test_the_missing_dependency_error_names_the_setup_command(monkeypatch):
    from audacity_mcp.tools import transcription_tools

    # Force the uninstalled path rather than skipping when faster-whisper
    # happens to be present. A test that skips on the developer's machine is a
    # test that never runs.
    monkeypatch.setattr(transcription_tools, "_whisper_available", lambda: False)

    with pytest.raises(AudacityMCPError) as excinfo:
        transcription_tools._check_whisper_installed()
    message = str(excinfo.value)
    # Both audiences: plugin users get a command, pip users get an install line.
    assert "/audacity:setup --transcription" in message
    assert 'audacity-mcp-max[transcription]' in message


def test_the_check_passes_when_the_extra_is_available(monkeypatch):
    from audacity_mcp.tools import transcription_tools

    monkeypatch.setattr(transcription_tools, "_whisper_available", lambda: True)
    transcription_tools._check_whisper_installed()  # must not raise
