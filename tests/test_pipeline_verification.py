import asyncio

import pytest

from audacity_mcp.tools import cleanup_tools, transcription_tools
from tests.conftest import register_tools
from tests.measurement_signals import sine, write_signal


@pytest.fixture(autouse=True)
def empty_job_stores():
    cleanup_tools._jobs.clear()
    transcription_tools._jobs.clear()
    yield
    cleanup_tools._jobs.clear()
    transcription_tools._jobs.clear()


def _tracks_reply(channels: int):
    """A GetInfo Type=Tracks side effect describing one track of `channels`."""
    import json

    async def _execute(command, **params):
        if command == "GetInfo" and params.get("Type") == "Tracks":
            return {
                "success": True,
                "raw": "",
                "message": json.dumps(
                    [{"name": "Audio", "start": 0, "end": 1.0, "rate": 44100,
                      "channels": channels}]
                ),
                "data": {},
            }
        return {"success": True, "raw": "", "message": "", "data": {}}

    return _execute


@pytest.fixture
def exporting_client(mock_client, tmp_path):
    """Make Export2 write a real, measurable WAV, the way Audacity would."""
    state = {"amplitude": 1.0, "exports": 0}

    async def _long(command, extra_params=None, **params):
        if command == "Export2":
            state["exports"] += 1
            write_signal(params["Filename"], [sine(1000, 1.0, amplitude=state["amplitude"])])
        return {"success": True, "raw": "", "message": "", "data": {}}

    mock_client.execute_long.side_effect = _long
    mock_client.state = state
    return mock_client


@pytest.fixture
def tools(exporting_client):
    return register_tools("cleanup_tools", exporting_client)


def run_to_completion(tools, name, **kwargs):
    async def _go():
        started = await tools[name].fn(**kwargs)
        job = cleanup_tools._jobs[started["job_id"]]
        await job["_task"]
        return await tools["check_pipeline_status"].fn(job_id=started["job_id"])
    return asyncio.run(_go())


class TestTransportPrecondition:
    def test_stop_is_issued_before_any_step(self, tools, exporting_client):
        """Audacity refuses scripted commands while transport is playing or
        paused, and the refusal looks exactly like a dead pipe."""
        run_to_completion(tools, "auto_cleanup_audio")
        commands = [c.args[0] for c in exporting_client.execute.call_args_list]
        assert "Stop" in commands
        assert commands.index("Stop") == 0


class TestMeasurementBlock:
    def test_terminal_status_carries_before_after_and_delta(self, tools):
        status = run_to_completion(tools, "auto_cleanup_audio")
        m = status["measurement"]
        assert m["verified"] is True
        assert m["before"]["peak_db"] is not None
        assert m["after"]["peak_db"] is not None
        assert "peak_db" in m["delta"]

    def test_two_exports_for_a_verified_run(self, tools, exporting_client):
        run_to_completion(tools, "auto_cleanup_audio")
        assert exporting_client.state["exports"] >= 2

    def test_verify_false_skips_both_measurements(self, tools, exporting_client):
        status = run_to_completion(tools, "auto_cleanup_audio", verify=False)
        assert status["measurement"]["verified"] is False
        assert status["measurement"]["before"] is None
        assert status["measurement"]["after"] is None
        # The point of verify=False is not measuring, not discarding the
        # measurement. Asserting only that before and after are None would hold
        # just as well if both exports ran and their results were thrown away,
        # which is the whole cost the flag exists to avoid.
        assert exporting_client.state["exports"] == 0

    def test_the_measurement_export_keeps_the_project_channels(
        self, tools, exporting_client
    ):
        """A mono export measures a downmix of a stereo project.

        On uncorrelated stereo that reads about 3 dB from the truth, which is
        the exact error the dual-mono convention exists to avoid, reintroduced
        above the meter. True peak is worse: L at +0.99 against R at -0.99
        downmixes to about zero, so a ceiling check passes on a file that clips.

        Omitting NumChannels does not avoid this. Audacity's ExportCommand
        declares `S.Define(mnChannels, wxT("NumChannels"), 1)`, so the absent
        parameter takes the default and the export is mono anyway - the count
        has to be sent.
        """
        exporting_client.execute.side_effect = _tracks_reply(channels=2)
        run_to_completion(tools, "auto_cleanup_audio")
        exports = [
            c for c in exporting_client.execute_long.call_args_list
            if c.args and c.args[0] == "Export2"
        ]
        assert exports, "no export was made at all"
        for call in exports:
            assert call.kwargs.get("NumChannels") == 2, (
                "the measurement export must carry the project's own channel "
                f"count, got NumChannels={call.kwargs.get('NumChannels')}"
            )

    def test_a_mono_project_is_exported_mono(self, tools, exporting_client):
        exporting_client.execute.side_effect = _tracks_reply(channels=1)
        run_to_completion(tools, "auto_cleanup_audio")
        exports = [
            c for c in exporting_client.execute_long.call_args_list
            if c.args and c.args[0] == "Export2"
        ]
        assert exports
        for call in exports:
            assert call.kwargs.get("NumChannels") == 1

    def test_an_unanswerable_track_query_exports_stereo(
        self, tools, exporting_client
    ):
        """Guessing mono would silently downmix; guessing stereo cannot lose
        anything, because a mono project exported as two channels measures
        identically to the dual-mono convention applied to one."""
        async def _fail(command, **params):
            if command == "GetInfo":
                raise RuntimeError("pipe closed")
            return {"success": True, "raw": "", "message": "", "data": {}}

        exporting_client.execute.side_effect = _fail
        run_to_completion(tools, "auto_cleanup_audio")
        exports = [
            c for c in exporting_client.execute_long.call_args_list
            if c.args and c.args[0] == "Export2"
        ]
        assert exports
        for call in exports:
            assert call.kwargs.get("NumChannels") == 2

    def test_the_analysis_export_keeps_the_project_channels(
        self, tools, exporting_client
    ):
        """auto_analyze_audio exports through a second call site, which the
        pipeline tests above cannot reach. It has the same defect available to
        it and had no test of its own."""
        exporting_client.execute.side_effect = _tracks_reply(channels=2)
        asyncio.run(tools["auto_analyze_audio"].fn())
        exports = [
            c for c in exporting_client.execute_long.call_args_list
            if c.args and c.args[0] == "Export2"
        ]
        assert exports, "no export was made at all"
        assert exports[0].kwargs.get("NumChannels") == 2

    def test_the_analysis_export_of_a_mono_project_is_mono(
        self, tools, exporting_client
    ):
        exporting_client.execute.side_effect = _tracks_reply(channels=1)
        asyncio.run(tools["auto_analyze_audio"].fn())
        exports = [
            c for c in exporting_client.execute_long.call_args_list
            if c.args and c.args[0] == "Export2"
        ]
        assert exports
        assert exports[0].kwargs.get("NumChannels") == 1

    def test_no_measurable_change_is_flagged(self, tools):
        """Both exports produce the identical signal, so nothing moved. A
        pipeline that changed nothing must not report unqualified success."""
        status = run_to_completion(tools, "auto_cleanup_audio")
        assert status["measurement"]["delta"].get("_no_measurable_change") is True


class TestMeasurementDoesNotStallTheLoop:
    def test_measurement_runs_off_the_event_loop(
        self, tools, exporting_client, monkeypatch
    ):
        """measure_file is CPU-bound - a K-weighting pass and a 4x-oversampled
        resample over the whole export - and a verified run calls it three
        times. Inline, it blocks the loop for the duration, so
        check_pipeline_status cannot answer while a long project is measured."""
        import threading

        from audacity_mcp.tools import cleanup_tools as ct

        seen = []
        real = ct.measure_file

        def _record(path):
            seen.append(threading.current_thread().name)
            return real(path)

        monkeypatch.setattr(ct, "measure_file", _record)
        loop_thread = threading.current_thread().name
        run_to_completion(tools, "auto_cleanup_audio")

        assert seen, "measure_file was never called"
        assert all(name != loop_thread for name in seen), (
            f"measurement ran on the event loop thread ({loop_thread})"
        )


class TestTargets:
    def test_podcast_run_evaluates_its_declared_target(self, tools):
        status = run_to_completion(tools, "auto_cleanup_podcast")
        assert "lufs" in status["measurement"]["targets"]

    def test_cleanup_audio_declares_no_target(self, tools):
        status = run_to_completion(tools, "auto_cleanup_audio")
        assert status["measurement"]["targets"] == {}

    def test_a_style_in_the_wrong_case_still_gets_its_target_checked(self, tools):
        """The job name is what for_pipeline looks up. Building it before the
        style is normalised produced "mastering_EDM", which matches no spec and
        returns an empty targets block - indistinguishable from a pipeline that
        declares no target, so a silently dropped check reads as a decision.
        """
        status = run_to_completion(tools, "auto_master_music", style="EDM")
        assert status["measurement"]["targets"], (
            "an uppercase style dropped the whole target check"
        )

    def test_a_lofi_intensity_in_the_wrong_case_names_the_job_normally(
        self, tools
    ):
        """The same defect as the style case above, in the pipeline the fix for
        it did not touch. Harmless only while every lofi spec is None - the day
        one is declared, an uppercase intensity drops the check silently."""
        status = run_to_completion(tools, "auto_lofi_effect", intensity="Medium")
        job = cleanup_tools._jobs[status["job_id"]]
        assert job["pipeline"] == "lofi_medium"


class TestPresetValidation:
    def test_an_unknown_style_leaves_no_job_behind(self, tools):
        """_create_job inserts the job as "running" before the preset was
        checked, and nothing marked it errored - so a typo blocked every later
        pipeline with "a pipeline is already running" until the ten-minute
        stale timeout expired."""
        with pytest.raises(Exception):
            asyncio.run(tools["auto_master_music"].fn(style="nonsense"))
        assert cleanup_tools._jobs == {}

    def test_an_unknown_lofi_intensity_leaves_no_job_behind(self, tools):
        with pytest.raises(Exception):
            asyncio.run(tools["auto_lofi_effect"].fn(intensity="nonsense"))
        assert cleanup_tools._jobs == {}

    def test_a_rejected_preset_does_not_block_the_next_pipeline(self, tools):
        with pytest.raises(Exception):
            asyncio.run(tools["auto_master_music"].fn(style="nonsense"))
        status = run_to_completion(tools, "auto_cleanup_audio")
        assert status["status"] == "complete"


class TestStepRecords:
    def test_every_step_is_recorded_with_an_outcome(self, tools):
        status = run_to_completion(tools, "auto_cleanup_audio")
        assert status["steps"], "no step records at all"
        for step in status["steps"]:
            assert set(step) >= {"name", "ok", "noop_reason"}

    def test_a_failing_step_is_recorded_as_not_ok(self, tools, exporting_client):
        async def _fail(command, extra_params=None, **params):
            if command == "High-passFilter":
                raise RuntimeError("boom")
            if command == "Export2":
                write_signal(params["Filename"], [sine(1000, 1.0)])
            return {"success": True, "raw": "", "message": "", "data": {}}
        exporting_client.execute_long.side_effect = _fail

        status = run_to_completion(tools, "auto_cleanup_audio")
        failed = [s for s in status["steps"] if not s["ok"]]
        assert failed, "a raising step was recorded as ok"
        assert any("boom" in (s["noop_reason"] or "") for s in failed)

    def test_a_step_audacity_refused_is_not_reported_as_completed(
        self, tools, exporting_client
    ):
        """steps_completed and the result message are both built from
        steps_applied, so a step Audacity refused used to be named in two
        places as having run. "A step that failed must never look like one that
        ran" is the same rule as the one about measurements.
        """
        async def _refuse(command, extra_params=None, **params):
            if command == "High-passFilter":
                return {"success": False, "raw": "", "message": "no", "data": {}}
            if command == "Export2":
                write_signal(params["Filename"], [sine(1000, 1.0)])
            return {"success": True, "raw": "", "message": "", "data": {}}
        exporting_client.execute_long.side_effect = _refuse

        status = run_to_completion(tools, "auto_cleanup_audio")
        hpf = [s for s in status["steps"] if s["name"].startswith("HPF")]
        assert hpf and not hpf[0]["ok"], "the refused step was recorded as ok"
        assert not any(s.startswith("HPF") for s in status["steps_completed"]), (
            f"a refused step is named in steps_completed: {status['steps_completed']}"
        )
        assert "HPF" not in status["result"]["message"]


class TestMeasurementFailureIsHonest:
    def test_a_failed_export_does_not_report_a_clean_measurement(self, tools, exporting_client):
        async def _no_file(command, extra_params=None, **params):
            return {"success": True, "raw": "", "message": "", "data": {}}
        exporting_client.execute_long.side_effect = _no_file

        status = run_to_completion(tools, "auto_cleanup_audio")
        assert status["measurement"]["before"] is None
        assert status["warnings"], "a failed measurement produced no warning"
        assert any("dialog" in w for w in status["warnings"]), (
            "the warning must name the modal-dialog cause, which is the "
            "indistinguishable-from-a-dead-pipe case"
        )

    def test_a_failed_measurement_does_not_fail_the_pipeline(
        self, tools, exporting_client
    ):
        """The audio was processed; what failed was checking it. A verified run
        measures three times, so a blocking export dialog used to report
        success: False for a pipeline whose every step applied."""
        async def _no_file(command, extra_params=None, **params):
            return {"success": True, "raw": "", "message": "", "data": {}}
        exporting_client.execute_long.side_effect = _no_file

        status = run_to_completion(tools, "auto_cleanup_audio")
        assert status["result"]["success"] is True
        assert status["warnings"], "the failure must still be visible"

    def test_a_failed_measurement_is_not_reported_as_verified(
        self, tools, exporting_client
    ):
        """verified used to be copied from the request at job creation, so a
        report could carry verified: true beside before: null and after: null -
        the exact unverifiable claim this package exists to remove."""
        async def _no_file(command, extra_params=None, **params):
            return {"success": True, "raw": "", "message": "", "data": {}}
        exporting_client.execute_long.side_effect = _no_file

        status = run_to_completion(tools, "auto_cleanup_audio")
        assert status["measurement"]["verified"] is False
