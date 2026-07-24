import asyncio
import time

import pytest

from audacity_mcp.tools import cleanup_tools, transcription_tools
from tests.conftest import register_tools


@pytest.fixture(autouse=True)
def empty_job_stores():
    """Both job stores are module-level dicts — leave them as we found them."""
    cleanup_tools._jobs.clear()
    transcription_tools._jobs.clear()
    yield
    cleanup_tools._jobs.clear()
    transcription_tools._jobs.clear()


@pytest.fixture
def cleanup_registered(mock_client):
    return register_tools("cleanup_tools", mock_client)


def running_job(**extra):
    job = {"status": "running", "current_step": "HPF 80Hz", "started_at": time.time()}
    job.update(extra)
    return job


class TestConcurrentJobRejection:
    def test_running_pipeline_is_named_in_the_error(self, cleanup_registered):
        cleanup_tools._jobs["abc12345"] = running_job()
        result = asyncio.run(cleanup_registered["auto_cleanup_audio"].fn())
        assert result["job_id"] == "abc12345"
        assert result["current_step"] == "HPF 80Hz"
        assert "pipeline is already running" in result["error"]
        assert len(cleanup_tools._jobs) == 1, "a second job was created anyway"

    def test_running_transcription_blocks_without_raising(self, cleanup_registered):
        """The old code scanned only the pipeline store, so a transcription
        conflict fell off the end of a bare next() and raised StopIteration."""
        transcription_tools._jobs["tr999999"] = running_job(current_step="transcribing audio")
        result = asyncio.run(cleanup_registered["auto_cleanup_audio"].fn())
        assert result["job_id"] == "tr999999"
        assert result["current_step"] == "transcribing audio"
        assert "transcription" in result["error"].lower()
        assert cleanup_tools._jobs == {}

    def test_conflicting_job_that_finishes_mid_call_does_not_raise(self, cleanup_registered):
        """The job the caller lost to is identified under the lock, so a store
        that empties out immediately afterwards cannot break the error path."""
        job = running_job()
        cleanup_tools._jobs["dead0001"] = job
        result = asyncio.run(cleanup_registered["auto_cleanup_audio"].fn())
        cleanup_tools._jobs.clear()
        assert result["job_id"] == "dead0001"

    def test_no_running_job_means_the_pipeline_starts(self, cleanup_registered):
        result = asyncio.run(cleanup_registered["auto_cleanup_audio"].fn())
        assert "job_id" in result
        assert "error" not in result
        assert len(cleanup_tools._jobs) == 1
