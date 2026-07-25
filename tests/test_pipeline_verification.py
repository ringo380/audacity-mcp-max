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
        """Forcing NumChannels=1 measures a mono downmix of a stereo project.

        On uncorrelated stereo that reads about 3 dB from the truth, which is
        the exact error the dual-mono convention exists to avoid, reintroduced
        above the meter. True peak is worse: L at +0.99 against R at -0.99
        downmixes to about zero, so a ceiling check passes on a file that clips.
        """
        run_to_completion(tools, "auto_cleanup_audio")
        exports = [
            c for c in exporting_client.execute_long.call_args_list
            if c.args and c.args[0] == "Export2"
        ]
        assert exports, "no export was made at all"
        for call in exports:
            assert "NumChannels" not in call.kwargs, (
                "the measurement export must carry the project's own channel "
                f"count, got NumChannels={call.kwargs.get('NumChannels')}"
            )

    def test_no_measurable_change_is_flagged(self, tools):
        """Both exports produce the identical signal, so nothing moved. A
        pipeline that changed nothing must not report unqualified success."""
        status = run_to_completion(tools, "auto_cleanup_audio")
        assert status["measurement"]["delta"].get("_no_measurable_change") is True


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
