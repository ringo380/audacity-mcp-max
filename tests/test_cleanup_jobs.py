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


class TestMeasureExport:
    """The analyzer reads the header rather than trusting the extension.

    Audacity writes AIFF into a path named .wav often enough that a WAV-only
    reader turns a normal export into "could not parse audio data" (issue #4).
    """

    def test_measures_a_real_wav(self, tmp_path):
        from tests.test_audio_file import write_wav

        path = write_wav(tmp_path / "e.wav", rate=1000,
                         samples=tuple(16384 if i % 2 else -16384 for i in range(2000)))
        m = cleanup_tools._measure_wav(path)
        assert m["sample_rate"] == 1000
        assert m["duration"] == 2.0
        assert m["peak_db"] == pytest.approx(-6.0, abs=0.1)

    def test_measures_aiff_written_into_a_wav_path(self, tmp_path):
        from tests.test_audio_file import write_aiff

        path = write_aiff(tmp_path / "e.wav", rate=1000,
                          samples=tuple(16384 if i % 2 else -16384 for i in range(2000)))
        m = cleanup_tools._measure_wav(path)
        assert m is not None, "an AIFF export must still be measurable"
        assert m["sample_rate"] == 1000
        assert m["peak_db"] == pytest.approx(-6.0, abs=0.1)

    def test_a_non_pcm_export_names_the_container(self, tmp_path):
        from audacity_mcp_shared.audio_file import UnsupportedAudioFile

        (tmp_path / "e.wav").write_bytes(b"OggS" + b"\x00" * 200)
        with pytest.raises(UnsupportedAudioFile) as exc:
            cleanup_tools._measure_wav(str(tmp_path / "e.wav"))
        assert "ogg" in str(exc.value)

    def test_empty_audio_is_none_not_an_error(self, tmp_path):
        from tests.test_audio_file import write_wav

        assert cleanup_tools._measure_wav(write_wav(tmp_path / "e.wav", samples=())) is None


class TestLoudnessSkipSurfacesAsAWarning:
    def test_pipeline_that_could_not_measure_reports_a_warning(
        self, cleanup_registered, mock_client, monkeypatch
    ):
        """A skipped loudness stage used to be recorded as an applied step, so
        a run that never touched loudness reported unqualified success."""

        async def no_sleep(_seconds):
            return None

        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        monkeypatch.setattr(cleanup_tools, "_STEP_DELAY", 0)
        # Export2 answers OK but writes nothing, so measurement cannot happen.

        async def drive():
            await cleanup_registered["auto_cleanup_podcast"].fn(remove_noise=False)
            job = next(iter(cleanup_tools._jobs.values()))
            await job["_task"]
            return job

        job = asyncio.run(drive())
        assert job["status"] == "complete"
        assert any("loudness skipped" in s for s in job["steps_failed"])
        assert not any("loudness skipped" in s for s in job["steps_applied"])
        assert job["result"]["success"] is False
        assert any("loudness skipped" in w for w in job["result"]["warnings"])
