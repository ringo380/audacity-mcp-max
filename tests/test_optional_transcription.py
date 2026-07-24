"""faster-whisper is an extra, not a base dependency.

The 7 transcription tools still register and stay self-describing; they fail at
call time with an error that names the fix. The point of the test is that the
base install stays small: a user cleaning up a recording should not pay for
ctranslate2 and onnxruntime to get there.
"""
import pathlib
import re

import pytest

from audacity_mcp_shared.error_codes import AudacityMCPError

PYPROJECT = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"


def read_pyproject():
    return PYPROJECT.read_text()


def test_faster_whisper_is_not_a_base_dependency():
    text = read_pyproject()
    base = re.search(r"^dependencies = \[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    assert base, "pyproject has no dependencies array"
    assert "faster-whisper" not in base.group(1)


def test_faster_whisper_is_declared_as_the_transcription_extra():
    text = read_pyproject()
    extra = re.search(r"^transcription = \[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    assert extra, "pyproject has no transcription extra"
    assert "faster-whisper" in extra.group(1)


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
