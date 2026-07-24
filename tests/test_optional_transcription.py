"""faster-whisper is an extra, not a base dependency.

The 7 transcription tools still register and stay self-describing; they fail at
call time with an error that names the fix. The point of the test is that the
base install stays small: a user cleaning up a recording should not pay for
ctranslate2 and onnxruntime to get there.
"""
import pathlib

import pytest

from audacity_mcp_shared.error_codes import AudacityMCPError

from tests.conftest import register_tools

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


@pytest.fixture
def registered_tools(mock_client):
    return register_tools("transcription_tools", mock_client)


class TestRuntimePathsSurfaceTheCleanMessage:
    """_check_whisper_installed has real callers now - these exercise them.

    Both tests below drive the actual tool through its background job, not
    the helper directly: the bug this guards against was two other call
    sites each doing their own dependency check, so a test that only calls
    _check_whisper_installed would keep passing while either site silently
    reverted to its own stale message.
    """

    @pytest.mark.asyncio(loop_scope="function")
    async def test_transcribe_audio_background_job_reports_the_setup_message(
        self, registered_tools, mock_client, monkeypatch
    ):
        from audacity_mcp.tools import transcription_tools

        monkeypatch.setattr(transcription_tools, "_whisper_available", lambda: False)

        tool = registered_tools["transcribe_audio"]
        result = await tool.fn(model_size="tiny")
        job_id = result["job_id"]

        # Await the real background task rather than sleeping a guessed
        # duration - the job start delays a second before doing anything,
        # and a fixed sleep would be either flaky or needlessly slow.
        await transcription_tools._jobs[job_id]["_task"]

        job = transcription_tools._jobs[job_id]
        assert job["status"] == "error"
        assert "/audacity:setup --transcription" in job["error"]
        assert 'audacity-mcp-max[transcription]' in job["error"]
        # The message must read as clean prose, not the exception's
        # "[VALIDATION_FAILED (3000)] ..." decoration.
        assert "VALIDATION_FAILED" not in job["error"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_transcription_set_model_reports_the_setup_message(
        self, registered_tools, mock_client, monkeypatch
    ):
        from audacity_mcp.tools import transcription_tools

        monkeypatch.setattr(transcription_tools, "_whisper_available", lambda: False)

        tool = registered_tools["transcription_set_model"]
        result = await tool.fn(model_size="tiny")
        job_id = result["job_id"]

        await transcription_tools._jobs[job_id]["_task"]

        job = transcription_tools._jobs[job_id]
        assert job["status"] == "error"
        assert "/audacity:setup --transcription" in job["error"]
        assert 'audacity-mcp-max[transcription]' in job["error"]
        assert "VALIDATION_FAILED" not in job["error"]
        # Not the raw ModuleNotFoundError _get_model's own import would raise.
        assert "ModuleNotFoundError" not in job["error"]
