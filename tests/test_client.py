import os
import sys
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from audacity_mcp.audacity_client import AudacityClient, _DeadlineExceeded
from audacity_mcp_shared.constants import PipePaths, Timeouts
from audacity_mcp_shared.error_codes import AudacityMCPError, ErrorCode


@pytest.fixture
def client():
    return AudacityClient()


IS_WIN = sys.platform == "win32"
posix_only = pytest.mark.skipif(IS_WIN, reason="POSIX pipe implementation")
windows_only = pytest.mark.skipif(not IS_WIN, reason="Win32 pipe implementation")

if IS_WIN:
    from audacity_mcp.audacity_client import INVALID_HANDLE_VALUE


def make_fifo(path: str) -> str:
    os.mkfifo(path)
    return path


class TestPosixOpenPipes:
    """_posix_open_pipes works on raw fds via os.open, not buffered open()."""

    @posix_only
    def test_missing_pipe_reports_not_found(self, client):
        # isolated_pipe_paths points both paths at a temp dir; neither exists.
        with pytest.raises(AudacityMCPError) as exc_info:
            client._open_pipes()
        assert exc_info.value.code == ErrorCode.PIPE_NOT_FOUND
        assert "mod-script-pipe" in str(exc_info.value)

    @posix_only
    def test_other_open_error_reports_open_failed(self, client, tmp_path):
        # FROM opens fine; TO is a directory, so os.open(O_WRONLY) gives EISDIR.
        make_fifo(PipePaths.FROM_SRV)
        os.mkdir(PipePaths.TO_SRV)
        with pytest.raises(AudacityMCPError) as exc_info:
            client._open_pipes()
        assert exc_info.value.code == ErrorCode.PIPE_OPEN_FAILED

    @posix_only
    def test_failed_open_leaves_no_fd_behind(self, client):
        with pytest.raises(AudacityMCPError):
            client._open_pipes()
        assert client._to_pipe is None
        assert client._from_pipe is None

    @posix_only
    def test_open_succeeds_with_a_reader_on_the_to_pipe(self, client):
        # Stand in for Audacity's relay: FROM has no writer (fine, opened
        # non-blocking) and TO needs a reader or os.open gives ENXIO.
        make_fifo(PipePaths.FROM_SRV)
        make_fifo(PipePaths.TO_SRV)
        relay_read_fd = os.open(PipePaths.TO_SRV, os.O_RDONLY | os.O_NONBLOCK)
        try:
            client._open_pipes()
            assert isinstance(client._to_pipe, int)
            assert isinstance(client._from_pipe, int)
            # TO must be blocking again after the ENXIO poll.
            import fcntl
            assert not fcntl.fcntl(client._to_pipe, fcntl.F_GETFL) & os.O_NONBLOCK
        finally:
            client._close_pipes()
            os.close(relay_read_fd)


class TestPosixSendRaw:
    @posix_only
    def test_write_failure_reports_write_failed(self, client):
        # A closed fd makes os.write raise EBADF.
        r_fd, w_fd = os.pipe()
        os.close(w_fd)
        os.close(r_fd)
        client._to_pipe = w_fd
        client._from_pipe = r_fd
        with pytest.raises(AudacityMCPError) as exc_info:
            client._posix_send_raw("Play:\n")
        assert exc_info.value.code == ErrorCode.PIPE_WRITE_FAILED

    @posix_only
    def test_send_raw_closes_pipes_after_exhausting_retries(self, client, monkeypatch):
        monkeypatch.setattr(client, "_SEND_ATTEMPTS", 2)

        def open_onto_dead_fds():
            r_fd, w_fd = os.pipe()
            os.close(w_fd)
            os.close(r_fd)
            client._to_pipe = w_fd
            client._from_pipe = r_fd

        with patch.object(client, "_open_pipes", side_effect=open_onto_dead_fds):
            with pytest.raises(AudacityMCPError) as exc_info:
                client._send_raw("Play:\n")
        assert exc_info.value.code == ErrorCode.PIPE_WRITE_FAILED
        assert client._to_pipe is None
        assert client._from_pipe is None

    @posix_only
    def test_reads_reply_until_terminator(self, client):
        make_fifo(PipePaths.FROM_SRV)
        make_fifo(PipePaths.TO_SRV)
        relay_read_fd = os.open(PipePaths.TO_SRV, os.O_RDONLY | os.O_NONBLOCK)
        relay_write_fd = os.open(PipePaths.FROM_SRV, os.O_RDWR | os.O_NONBLOCK)
        try:
            client._open_pipes()
            os.write(relay_write_fd, b"Result\nBatchCommand finished: OK\n")
            raw = client._posix_send_raw("Play:\n")
            assert "BatchCommand finished: OK" in raw
            assert os.read(relay_read_fd, 65536) == b"Play:\n"
        finally:
            client._close_pipes()
            os.close(relay_read_fd)
            os.close(relay_write_fd)


class TestSendRetryLoop:
    """The retry loop is platform-independent; only _send_attempt differs.

    These run everywhere, which is the point — the retry that makes this fork
    reliable used to live inside the POSIX branch, so Windows got one attempt
    and no test could reach the loop from a Mac or a Linux box.
    """

    @pytest.fixture(autouse=True)
    def no_backoff_sleeping(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _s: None)

    def test_retries_until_a_terminated_reply_arrives(self, client):
        replies = ["", "", "BatchCommand finished: OK\n"]
        with patch.object(client, "_send_attempt", side_effect=replies) as attempt:
            with patch.object(client, "_close_pipes_gracefully"):
                raw = client._send_raw("Play:\n")
        assert raw == "BatchCommand finished: OK\n"
        assert attempt.call_count == 3

    def test_stops_at_the_attempt_limit(self, client, monkeypatch):
        monkeypatch.setattr(client, "_SEND_ATTEMPTS", 4)
        with patch.object(client, "_send_attempt", return_value="") as attempt:
            with patch.object(client, "_close_pipes_gracefully"):
                with pytest.raises(AudacityMCPError) as exc_info:
                    client._send_raw("Play:\n")
        assert attempt.call_count == 4
        assert exc_info.value.code == ErrorCode.PIPE_READ_FAILED

    def test_falls_back_to_an_unterminated_reply(self, client, monkeypatch):
        monkeypatch.setattr(client, "_SEND_ATTEMPTS", 2)
        with patch.object(client, "_send_attempt", side_effect=["partial data", ""]):
            with patch.object(client, "_close_pipes_gracefully"):
                assert client._send_raw("Play:\n") == "partial data"

    def test_reraises_the_last_error_when_every_attempt_failed(self, client, monkeypatch):
        monkeypatch.setattr(client, "_SEND_ATTEMPTS", 2)
        err = AudacityMCPError(ErrorCode.PIPE_WRITE_FAILED, "boom")
        with patch.object(client, "_send_attempt", side_effect=err):
            with patch.object(client, "_close_pipes_gracefully"):
                with pytest.raises(AudacityMCPError) as exc_info:
                    client._send_raw("Play:\n")
        assert exc_info.value.code == ErrorCode.PIPE_WRITE_FAILED

    def test_a_failed_attempt_drops_both_ends_before_the_next(self, client):
        """Whatever the platform, a dead connection is not retried in place."""
        events = []
        replies = iter(["", "BatchCommand finished: OK\n"])

        def attempt(_cmd, _deadline=None):
            events.append("attempt")
            return next(replies)

        with patch.object(client, "_send_attempt", side_effect=attempt):
            with patch.object(client, "_close_pipes_gracefully", side_effect=lambda: events.append("close")):
                client._send_raw("Play:\n")
        assert events[:3] == ["attempt", "close", "attempt"]


class TestGracefulCloseDoesNotSigpipeTheRelay:
    """Closing FROM while the relay is mid-reply raises SIGPIPE inside the
    relay, and Audacity does not ignore it, so the app dies (issue #19). The
    relay stub reproduces that: with the hard close it exits -13, with the
    graceful drain-to-EOF close it survives. This is the real subprocess, real
    FIFOs, real client — the only faithful way to test a signal race."""

    @posix_only
    def test_a_gave_up_command_leaves_the_relay_alive(self, client):
        import os
        import subprocess
        import time

        relay_stub = os.path.join(os.path.dirname(__file__), "relay_stub.py")
        make_fifo(PipePaths.TO_SRV)
        make_fifo(PipePaths.FROM_SRV)
        # Reply in 6 pieces, 150ms apart; the client is given 200ms, so it gives
        # up around the second piece with four still unwritten — the window the
        # bug lives in.
        relay = subprocess.Popen(
            [sys.executable, relay_stub, PipePaths.TO_SRV, PipePaths.FROM_SRV, "6", "0.15"]
        )
        try:
            # _send_raw raises the internal _DeadlineExceeded on give-up; it is
            # execute() that maps it to PIPE_TIMEOUT. Either way the command did
            # not complete — what this test cares about is the relay's fate.
            with pytest.raises((AudacityMCPError, _DeadlineExceeded)):
                client._send_raw("Message: Text=hi\n", deadline=time.monotonic() + 0.2)
            rc = relay.wait(timeout=5)
        finally:
            if relay.poll() is None:
                relay.kill()
        assert rc != -13, "the relay was killed by SIGPIPE — the graceful close regressed"
        assert rc == 0, f"relay exited abnormally: {rc}"

    @posix_only
    def test_a_successful_command_leaves_the_relay_alive(self, client):
        """The success path closes too. With the terminator arriving early and
        more pieces still to come, the client returns success while the relay
        is mid-write — a hard close there SIGPIPEs it just the same."""
        import os
        import subprocess

        relay_stub = os.path.join(os.path.dirname(__file__), "relay_stub.py")
        make_fifo(PipePaths.TO_SRV)
        make_fifo(PipePaths.FROM_SRV)
        # 6 pieces, terminator at piece 1: the client succeeds almost at once
        # and closes while pieces 2-5 are still to be written, 150ms apart.
        relay = subprocess.Popen(
            [sys.executable, relay_stub, PipePaths.TO_SRV, PipePaths.FROM_SRV,
             "6", "0.15", "1"]
        )
        try:
            raw = client._send_raw("Message: Text=hi\n", deadline=None)
            assert "BatchCommand finished: OK" in raw
            assert client._to_pipe is None
            assert client._from_pipe is None
            rc = relay.wait(timeout=5)
        finally:
            if relay.poll() is None:
                relay.kill()
        assert rc != -13, "the relay was killed by SIGPIPE on the success path"
        assert rc == 0, f"relay exited abnormally: {rc}"

    @posix_only
    def test_the_drain_is_bounded_when_the_relay_never_closes(self, client, monkeypatch):
        """A relay that keeps its write end open forever must not hang us. The
        drain gives up after PIPE_DRAIN and hard-closes, accepting the residual
        SIGPIPE risk on that path — better than blocking the session."""
        import os
        import time

        monkeypatch.setattr(Timeouts, "PIPE_DRAIN", 0.3)
        make_fifo(PipePaths.TO_SRV)
        make_fifo(PipePaths.FROM_SRV)
        # A reader/writer pair standing in for a relay that answered and then
        # went quiet without ever closing its FROM write end.
        relay_read = os.open(PipePaths.TO_SRV, os.O_RDONLY | os.O_NONBLOCK)
        relay_write = os.open(PipePaths.FROM_SRV, os.O_RDWR)
        try:
            client._open_pipes()
            started = time.monotonic()
            client._close_pipes_gracefully()
            elapsed = time.monotonic() - started
            assert elapsed < 2.0, f"graceful close blocked for {elapsed}s on a silent relay"
            assert elapsed >= 0.3, "it did not actually wait out the drain window"
            assert client._from_pipe is None
        finally:
            os.close(relay_read)
            os.close(relay_write)


class TestWin32Pipes:
    @windows_only
    def test_pipe_not_found(self, client):
        with patch("audacity_mcp.audacity_client.kernel32") as mock_k32:
            mock_k32.CreateFileW.return_value = INVALID_HANDLE_VALUE
            with patch("ctypes.get_last_error", return_value=2):  # ERROR_FILE_NOT_FOUND
                with pytest.raises(AudacityMCPError) as exc_info:
                    client._open_pipes()
                assert exc_info.value.code == ErrorCode.PIPE_NOT_FOUND

    @windows_only
    def test_pipe_open_os_error(self, client):
        with patch("audacity_mcp.audacity_client.kernel32") as mock_k32:
            mock_k32.CreateFileW.return_value = INVALID_HANDLE_VALUE
            with patch("ctypes.get_last_error", return_value=5):  # ERROR_ACCESS_DENIED
                with pytest.raises(AudacityMCPError) as exc_info:
                    client._open_pipes()
                assert exc_info.value.code == ErrorCode.PIPE_OPEN_FAILED

    @windows_only
    def test_send_raw_write_failure(self, client):
        client._to_pipe = 123  # fake handle
        client._from_pipe = 456
        with patch("audacity_mcp.audacity_client.kernel32") as mock_k32:
            mock_k32.WriteFile.return_value = False
            mock_k32.CloseHandle.return_value = True
            with patch("ctypes.get_last_error", return_value=232):
                with pytest.raises(AudacityMCPError) as exc_info:
                    client._send_raw("Play:\n")
                assert exc_info.value.code == ErrorCode.PIPE_WRITE_FAILED
        assert client._to_pipe is None


class TestShutdown:
    def test_close_sync_is_not_a_coroutine_function(self):
        # atexit cannot await; registering an async close() silently discarded
        # the coroutine and left the pipes open (issue #3).
        import inspect
        assert not inspect.iscoroutinefunction(AudacityClient.close_sync)

    def test_atexit_is_given_close_sync_itself(self):
        # Asserting that close_sync is sync proves nothing about what main
        # actually registered. Unregistering it is what pins the wiring: if
        # main registered something else, the callback count does not move.
        import atexit
        import audacity_mcp.main as main

        if not hasattr(atexit, "_ncallbacks"):
            pytest.skip("interpreter does not expose atexit._ncallbacks")
        before = atexit._ncallbacks()
        atexit.unregister(main.client.close_sync)
        after = atexit._ncallbacks()
        atexit.register(main.client.close_sync)
        assert after == before - 1, "atexit was not given client.close_sync"

    @posix_only
    def test_close_sync_closes_open_fds(self, client):
        make_fifo(PipePaths.FROM_SRV)
        make_fifo(PipePaths.TO_SRV)
        relay_read_fd = os.open(PipePaths.TO_SRV, os.O_RDONLY | os.O_NONBLOCK)
        try:
            client._open_pipes()
            to_fd, from_fd = client._to_pipe, client._from_pipe
            client.close_sync()
            assert client._to_pipe is None
            for fd in (to_fd, from_fd):
                with pytest.raises(OSError):
                    os.fstat(fd)
        finally:
            os.close(relay_read_fd)


async def _timeout_budget(client, awaitable) -> float:
    """Seconds of deadline the send loop was handed for one call.

    The worker enforces the timeout itself, so this — not the argument to any
    one asyncio call — is where a command's budget actually lands.
    """
    import time

    seen = []

    def record(_cmd, deadline=None):
        seen.append(deadline - time.monotonic())
        return "BatchCommand finished: OK\n"

    with patch.object(client, "_send_raw", side_effect=record):
        await awaitable
    assert len(seen) == 1
    return seen[0]


@pytest.mark.asyncio
class TestClientExecute:
    async def test_execute_formats_and_sends(self, client):
        with patch.object(client, "_send_raw", return_value="BatchCommand finished: OK\n") as send:
            result = await client.execute("Play")
        assert result["success"] is True
        assert send.call_args[0][0] == "Play:\n"

    async def test_execute_surfaces_client_errors(self, client):
        err = AudacityMCPError(ErrorCode.PIPE_TIMEOUT, "boom")
        with patch.object(client, "_send_raw", side_effect=err):
            with pytest.raises(AudacityMCPError) as exc_info:
                await client.execute("Play")
        assert exc_info.value.code == ErrorCode.PIPE_TIMEOUT

    async def test_execute_uses_the_short_timeout(self, client):
        budget = await _timeout_budget(client, client.execute("Play"))
        assert abs(budget - Timeouts.COMMAND) < 0.5

    async def test_execute_long_uses_the_long_timeout(self, client):
        """execute_long is a thin wrapper — this is what keeps it from
        collapsing into a plain execute() with the short timeout."""
        budget = await _timeout_budget(client, client.execute_long("Amplify", Ratio=1.5))
        assert abs(budget - Timeouts.LONG_COMMAND) < 0.5

    async def test_execute_long_still_formats_its_params(self, client):
        with patch.object(client, "_send_raw", return_value="BatchCommand finished: OK\n") as send:
            await client.execute_long("Amplify", Ratio=1.5)
        assert send.call_args[0][0] == 'Amplify: Ratio=1.5\n'


class TestSendDeadline:
    """A thread cannot be cancelled, so the send loop has to stop itself.

    Without this the worker kept retrying long after its caller gave up: a
    5s health check returned in 5s and the process took 31s to exit (#18).
    """

    @pytest.fixture(autouse=True)
    def no_backoff_sleeping(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _s: None)

    def test_a_passed_deadline_makes_no_attempt_at_all(self, client):
        import time

        with patch.object(client, "_send_attempt", return_value="") as attempt:
            with patch.object(client, "_close_pipes"):
                with pytest.raises(_DeadlineExceeded):
                    client._send_raw("Play:\n", deadline=time.monotonic() - 1)
        assert attempt.call_count == 0

    def test_the_loop_stops_when_the_deadline_passes_mid_retry(self, client):
        import time

        def attempt(_cmd, _deadline=None):
            _burn(0.06)  # each attempt costs real time, so the deadline arrives
            return ""

        with patch.object(client, "_send_attempt", side_effect=attempt) as spy:
            with patch.object(client, "_close_pipes"):
                with pytest.raises(_DeadlineExceeded):
                    client._send_raw("Play:\n", deadline=time.monotonic() + 0.15)
        assert 0 < spy.call_count < client._SEND_ATTEMPTS

    def test_the_backoff_never_sleeps_past_the_deadline(self, client):
        """The retry backoff grows to 0.3s. Sleeping it out whole is another
        way for the thread to outlive the caller, so it is clipped."""
        import time

        wakes = []
        deadline = time.monotonic() + 0.12

        def attempt(_cmd, _deadline=None):
            _burn(0.04)
            return ""

        def record(seconds):
            wakes.append(time.monotonic() + seconds)

        with patch.object(client, "_send_attempt", side_effect=attempt):
            with patch.object(client, "_close_pipes"):
                with patch("time.sleep", record):
                    with pytest.raises(_DeadlineExceeded):
                        client._send_raw("Play:\n", deadline=deadline)
        assert wakes, "the loop never backed off, so this proves nothing"
        # The tolerance covers the clock moving between the clamp and this
        # recorder (microseconds). An unclamped backoff overshoots by ~60ms.
        assert max(wakes) <= deadline + 0.001, "a backoff was set to end past the deadline"

    def test_no_deadline_keeps_the_old_behaviour(self, client, monkeypatch):
        monkeypatch.setattr(client, "_SEND_ATTEMPTS", 3)
        with patch.object(client, "_send_attempt", return_value="") as attempt:
            with patch.object(client, "_close_pipes"):
                with pytest.raises(AudacityMCPError) as exc_info:
                    client._send_raw("Play:\n")
        assert attempt.call_count == 3
        assert exc_info.value.code == ErrorCode.PIPE_READ_FAILED

    @posix_only
    def test_the_read_gate_never_waits_past_the_deadline(self, client):
        import select
        import time

        make_fifo(PipePaths.FROM_SRV)
        make_fifo(PipePaths.TO_SRV)
        relay_read_fd = os.open(PipePaths.TO_SRV, os.O_RDONLY | os.O_NONBLOCK)
        waits = []

        def spy_select(rlist, wlist, xlist, timeout):
            waits.append(timeout)
            return ([], [], [])

        try:
            client._open_pipes()
            with patch.object(select, "select", spy_select):
                with pytest.raises(AudacityMCPError):
                    client._posix_send_raw("Play:\n", deadline=time.monotonic() + 0.2)
        finally:
            client._close_pipes()
            os.close(relay_read_fd)
        assert waits and waits[0] <= 0.2, f"read gate waited {waits[0]}s, past the deadline"
        assert Timeouts.PIPE_READ > 0.2, "the cap has to be shorter than the default to prove anything"


def _burn(seconds: float) -> None:
    """Spend real time. time.sleep is patched out in some of these tests, and
    the point is to move the monotonic clock, not to yield."""
    import time as _t

    end = _t.monotonic() + seconds
    while _t.monotonic() < end:
        pass


@pytest.mark.asyncio
class TestAbandonedWorker:
    """The lock is released the moment wait_for gives up, so the next command
    would otherwise start writing while the abandoned one is still on the
    pipe, with the event loop closing fds out from under its thread."""

    @pytest.fixture(autouse=True)
    def quick_grace(self, monkeypatch):
        monkeypatch.setattr(Timeouts, "WORKER_EXIT", 0.15)

    async def test_a_timed_out_command_leaves_the_fds_to_its_worker(self, client):
        import threading

        release = threading.Event()

        def slow(_cmd, _deadline=None):
            release.wait(5)
            return "BatchCommand finished: OK\n"

        try:
            with patch.object(client, "_send_raw", side_effect=slow):
                with patch.object(client, "_close_pipes") as close:
                    with pytest.raises(AudacityMCPError) as exc_info:
                        await client.execute("Play", _timeout=0.05)
            assert exc_info.value.code == ErrorCode.PIPE_TIMEOUT
            assert close.call_count == 0, "the event loop closed the worker's fds"
        finally:
            release.set()

    async def test_the_caller_waits_for_the_worker_to_unwind(self, client):
        import threading

        started = threading.Event()

        def slow(_cmd, _deadline=None):
            started.set()
            _burn(0.15)
            return "BatchCommand finished: OK\n"

        with patch.object(client, "_send_raw", side_effect=slow):
            with pytest.raises(AudacityMCPError):
                await client.execute("Play", _timeout=0.02)
        assert started.is_set()
        assert client._abandoned is None, "returned while the worker was still on the pipe"

    async def test_a_worker_that_will_not_stop_blocks_the_next_command(self, client):
        import threading

        release = threading.Event()
        calls = []

        def stuck(_cmd, _deadline=None):
            calls.append(_cmd)
            release.wait(5)
            return "BatchCommand finished: OK\n"

        try:
            with patch.object(client, "_send_raw", side_effect=stuck):
                with pytest.raises(AudacityMCPError):
                    await client.execute("Play", _timeout=0.02)
                assert client._abandoned is not None

                with pytest.raises(AudacityMCPError) as exc_info:
                    await client.execute("Stop", _timeout=0.02)
            assert exc_info.value.code == ErrorCode.PIPE_TIMEOUT
            assert "still running" in exc_info.value.message
            assert calls == ["Play:\n"], "a second command was sent onto the busy pipe"
        finally:
            release.set()

    async def test_a_worker_that_finishes_late_clears_the_way(self, client):
        import threading

        release = threading.Event()
        calls = []

        def maybe_stuck(_cmd, _deadline=None):
            calls.append(_cmd)
            release.wait(5)
            return "BatchCommand finished: OK\n"

        with patch.object(client, "_send_raw", side_effect=maybe_stuck):
            with pytest.raises(AudacityMCPError):
                await client.execute("Play", _timeout=0.02)
            assert client._abandoned is not None
            release.set()
            result = await client.execute("Stop", _timeout=1.0)
        assert result["success"] is True
        assert calls == ["Play:\n", "Stop:\n"]
        assert client._abandoned is None
