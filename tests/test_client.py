import os
import sys
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from audacity_mcp.audacity_client import AudacityClient
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
        monkeypatch.setattr(client, "_POSIX_SEND_ATTEMPTS", 2)

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
        with patch.object(client, "_send_raw", return_value="BatchCommand finished: OK\n"):
            with patch("audacity_mcp.audacity_client.asyncio.wait_for", new_callable=AsyncMock) as wait_for:
                wait_for.return_value = "BatchCommand finished: OK\n"
                await client.execute("Play")
        assert wait_for.call_args[1]["timeout"] == Timeouts.COMMAND

    async def test_execute_long_uses_the_long_timeout(self, client):
        """execute_long is a thin wrapper — this is what keeps it from
        collapsing into a plain execute() with the short timeout."""
        with patch.object(client, "_send_raw", return_value="BatchCommand finished: OK\n"):
            with patch("audacity_mcp.audacity_client.asyncio.wait_for", new_callable=AsyncMock) as wait_for:
                wait_for.return_value = "BatchCommand finished: OK\n"
                await client.execute_long("Amplify", Ratio=1.5)
        assert wait_for.call_args[1]["timeout"] == Timeouts.LONG_COMMAND

    async def test_execute_long_still_formats_its_params(self, client):
        with patch.object(client, "_send_raw", return_value="BatchCommand finished: OK\n") as send:
            await client.execute_long("Amplify", Ratio=1.5)
        assert send.call_args[0][0] == 'Amplify: Ratio=1.5\n'
