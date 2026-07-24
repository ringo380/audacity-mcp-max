from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from audacity_mcp_shared.constants import PipePaths, Timeouts
from audacity_mcp_shared.error_codes import AudacityMCPError, ErrorCode
from audacity_mcp_shared.pipe_protocol import format_command, parse_response

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value  # 0xFFFFFFFFFFFFFFFF on 64-bit

    kernel32.CreateFileW.restype = ctypes.wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        ctypes.wintypes.LPCWSTR,  # lpFileName
        ctypes.wintypes.DWORD,    # dwDesiredAccess
        ctypes.wintypes.DWORD,    # dwShareMode
        ctypes.c_void_p,          # lpSecurityAttributes
        ctypes.wintypes.DWORD,    # dwCreationDisposition
        ctypes.wintypes.DWORD,    # dwFlagsAndAttributes
        ctypes.wintypes.HANDLE,   # hTemplateFile
    ]

    kernel32.WriteFile.restype = ctypes.wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        ctypes.wintypes.HANDLE,            # hFile
        ctypes.c_void_p,                   # lpBuffer
        ctypes.wintypes.DWORD,             # nNumberOfBytesToWrite
        ctypes.POINTER(ctypes.wintypes.DWORD),  # lpNumberOfBytesWritten
        ctypes.c_void_p,                   # lpOverlapped
    ]

    kernel32.ReadFile.restype = ctypes.wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        ctypes.wintypes.HANDLE,            # hFile
        ctypes.c_void_p,                   # lpBuffer
        ctypes.wintypes.DWORD,             # nNumberOfBytesToRead
        ctypes.POINTER(ctypes.wintypes.DWORD),  # lpNumberOfBytesRead
        ctypes.c_void_p,                   # lpOverlapped
    ]

    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]

    kernel32.WaitNamedPipeW.restype = ctypes.wintypes.BOOL
    kernel32.WaitNamedPipeW.argtypes = [
        ctypes.wintypes.LPCWSTR,  # lpNamedPipeName
        ctypes.wintypes.DWORD,    # nTimeOut (ms)
    ]

    ERROR_PIPE_BUSY = 231


class AudacityClient:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._to_pipe = None
        self._from_pipe = None

    def _open_pipes(self):
        try:
            if sys.platform == "win32":
                # Audacity requires FromSrvPipe opened first, and both need read+write access
                self._from_pipe = self._win32_open_pipe(PipePaths.FROM_SRV, GENERIC_READ | GENERIC_WRITE)
                self._to_pipe = self._win32_open_pipe(PipePaths.TO_SRV, GENERIC_READ | GENERIC_WRITE)
            else:
                self._posix_open_pipes()
        except AudacityMCPError:
            self._close_pipes()
            raise
        except FileNotFoundError:
            # Same diagnostic the Win32 branch gives for ERROR_FILE_NOT_FOUND.
            # Without this, a missing FIFO on POSIX surfaced as a bare
            # PIPE_OPEN_FAILED with an errno string and no hint about the module.
            self._close_pipes()
            raise AudacityMCPError(
                ErrorCode.PIPE_NOT_FOUND,
                "Audacity pipe not found. Is Audacity running with mod-script-pipe enabled? "
                "(Edit > Preferences > Modules > mod-script-pipe = Enabled, then restart Audacity)",
            )
        except OSError as e:
            self._close_pipes()
            raise AudacityMCPError(ErrorCode.PIPE_OPEN_FAILED, str(e))

    def _posix_open_pipes(self):
        # Audacity's mod-script-pipe relay opens its WRITE end (FROM) first and
        # blocks for a reader, so the client must open FROM before TO. The reverse
        # order (the historical bug) races the relay and yields immediate empty
        # reads. Open FROM read-only + O_NONBLOCK so it never hangs and reads are
        # driven by select()/os.read in _posix_send_raw. Open TO with O_NONBLOCK
        # too (it raises ENXIO until the relay's read end is up — poll briefly),
        # then clear O_NONBLOCK so os.write to it behaves normally. We deliberately
        # keep RAW integer fds here (no buffered file object): the relay closes
        # both ends right after each reply, and a buffered reader's readahead/EOF
        # handling drops the reply, whereas os.read() returns the bytes reliably.
        import errno
        import fcntl
        import os
        import time

        from_fd = os.open(PipePaths.FROM_SRV, os.O_RDONLY | os.O_NONBLOCK)
        try:
            deadline = time.monotonic() + Timeouts.PIPE_OPEN
            while True:
                try:
                    to_fd = os.open(PipePaths.TO_SRV, os.O_WRONLY | os.O_NONBLOCK)
                    break
                except OSError as e:
                    if e.errno == errno.ENXIO and time.monotonic() < deadline:
                        time.sleep(0.01)
                        continue
                    raise
        except BaseException:
            os.close(from_fd)
            raise

        flags = fcntl.fcntl(to_fd, fcntl.F_GETFL)
        fcntl.fcntl(to_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

        self._from_pipe = from_fd  # raw int fd (read, non-blocking)
        self._to_pipe = to_fd      # raw int fd (write, blocking)

    def _win32_open_pipe(self, pipe_path: str, access: int) -> ctypes.wintypes.HANDLE:
        for _ in range(3):
            handle = kernel32.CreateFileW(
                pipe_path,
                access,
                0,     # no sharing
                None,  # default security
                OPEN_EXISTING,
                0,     # default attributes
                None,  # no template
            )
            if handle != INVALID_HANDLE_VALUE:
                return handle
            err = ctypes.get_last_error()
            if err == 2:  # ERROR_FILE_NOT_FOUND
                raise AudacityMCPError(
                    ErrorCode.PIPE_NOT_FOUND,
                    "Audacity pipe not found. Is Audacity running with mod-script-pipe enabled? "
                    "(Edit > Preferences > Modules > mod-script-pipe = Enabled, then restart Audacity)",
                )
            if err == ERROR_PIPE_BUSY:
                # Wait up to 5 seconds for pipe to become available
                kernel32.WaitNamedPipeW(pipe_path, 5000)
                continue
            raise AudacityMCPError(
                ErrorCode.PIPE_OPEN_FAILED,
                f"Failed to open pipe {pipe_path}: Win32 error {err}",
            )
        raise AudacityMCPError(
            ErrorCode.PIPE_OPEN_FAILED,
            f"Pipe {pipe_path} remained busy after retries",
        )

    def _close_pipes(self):
        if sys.platform == "win32":
            for handle in (self._to_pipe, self._from_pipe):
                if handle is not None:
                    try:
                        kernel32.CloseHandle(handle)
                    except OSError:
                        pass
        else:
            import os

            for fd in (self._to_pipe, self._from_pipe):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        self._to_pipe = None
        self._from_pipe = None

    # Audacity's mod-script-pipe relay (Linux) tears down and reopens BOTH FIFO
    # ends after every command cycle. Even a fresh per-command open races that
    # reopen, so any single attempt succeeds only ~50% of the time (empty read /
    # broken pipe), with successes and failures alternating cycle-to-cycle. A
    # bounded retry — closing our ends between attempts so the relay finishes its
    # cycle and we reopen clean — makes it reliable. A bad cycle fails fast
    # (immediate empty read), so the retries are cheap in the common case.
    _SEND_ATTEMPTS = 6

    def _send_attempt(self, command_str: str) -> str:
        """One write-and-read cycle, opening whatever this platform needs first."""
        if sys.platform == "win32":
            # Named pipes are connection-oriented, so the handles are kept across
            # commands. _win32_send_raw closes them on failure, which is what makes
            # the next attempt reopen instead of retrying a dead handle.
            if self._to_pipe is None or self._from_pipe is None:
                self._open_pipes()
            return self._win32_send_raw(command_str)
        self._open_pipes()  # always fresh; never cache a fd across commands
        return self._posix_send_raw(command_str)

    def _send_raw(self, command_str: str) -> str:
        import time

        last_raw = ""
        last_err = None
        for attempt in range(self._SEND_ATTEMPTS):
            try:
                raw = self._send_attempt(command_str)
            except AudacityMCPError as e:
                last_err = e
            else:
                if "BatchCommand finished" in raw:
                    # Complete, well-formed response. POSIX still drops its ends;
                    # Windows keeps the connection for the next command.
                    if sys.platform != "win32":
                        self._close_pipes()
                    return raw
                if raw.strip():
                    last_raw = raw  # non-empty but no terminator: keep as fallback
            # The attempt did not produce a usable reply. Drop both ends on either
            # platform so the next attempt reopens clean rather than retrying a
            # connection the other side has already given up on.
            self._close_pipes()
            time.sleep(0.05 * (attempt + 1))

        if last_raw:
            return last_raw
        raise last_err or AudacityMCPError(
            ErrorCode.PIPE_READ_FAILED,
            f"Empty response from Audacity pipe after {self._SEND_ATTEMPTS} attempts",
        )

    def _win32_send_raw(self, command_str: str) -> str:
        data = command_str.encode("utf-8")
        bytes_written = ctypes.wintypes.DWORD()
        try:
            ok = kernel32.WriteFile(
                self._to_pipe,
                data,
                len(data),
                ctypes.byref(bytes_written),
                None,
            )
            if not ok:
                raise OSError(f"WriteFile failed: Win32 error {ctypes.get_last_error()}")
        except OSError as e:
            self._close_pipes()
            raise AudacityMCPError(ErrorCode.PIPE_WRITE_FAILED, str(e))

        try:
            response_parts = []
            buf = ctypes.create_string_buffer(65536)
            while True:
                bytes_read = ctypes.wintypes.DWORD()
                ok = kernel32.ReadFile(
                    self._from_pipe,
                    buf,
                    len(buf),
                    ctypes.byref(bytes_read),
                    None,
                )
                if not ok:
                    err = ctypes.get_last_error()
                    raise OSError(f"ReadFile failed: Win32 error {err}")
                if bytes_read.value == 0:
                    break
                chunk = buf.raw[:bytes_read.value].decode("utf-8")
                response_parts.append(chunk)
                accumulated = "".join(response_parts)
                if "\n\n" in accumulated:
                    break
            return "".join(response_parts)
        except OSError as e:
            self._close_pipes()
            raise AudacityMCPError(ErrorCode.PIPE_READ_FAILED, str(e))

    def _posix_send_raw(self, command_str: str) -> str:
        # Operates on the raw int fds from _posix_open_pipes. Closing is left to
        # the caller (_send_raw's retry loop), which tears the pipes down between
        # attempts so the relay can complete its cycle.
        import os
        import select

        try:
            os.write(self._to_pipe, command_str.encode("utf-8"))
        except OSError as e:
            raise AudacityMCPError(ErrorCode.PIPE_WRITE_FAILED, str(e))

        try:
            chunks = []
            while True:
                # Gate each read so a silent relay can't hang us forever.
                ready, _, _ = select.select([self._from_pipe], [], [], Timeouts.PIPE_READ)
                if not ready:
                    raise AudacityMCPError(
                        ErrorCode.PIPE_TIMEOUT,
                        f"Pipe read timed out after {Timeouts.PIPE_READ}s — Audacity may have stopped responding",
                    )
                chunk = os.read(self._from_pipe, 65536)
                if not chunk:  # EOF: relay closed its write end
                    break
                chunks.append(chunk)
                # Audacity terminates every reply with this status line.
                if b"BatchCommand finished" in b"".join(chunks):
                    break
            return b"".join(chunks).decode("utf-8", errors="replace")
        except AudacityMCPError:
            raise
        except OSError as e:
            raise AudacityMCPError(ErrorCode.PIPE_READ_FAILED, str(e))

    async def execute(
        self,
        command: str,
        extra_params: dict | None = None,
        *,
        _timeout: float = Timeouts.COMMAND,
        **params,
    ) -> dict:
        """Send one command and parse the reply.

        `_timeout` is underscored so it cannot collide with an Audacity
        parameter arriving through **params.
        """
        cmd_str = format_command(command, extra_params=extra_params, **params)
        async with self._lock:
            loop = asyncio.get_event_loop()
            try:
                raw = await asyncio.wait_for(
                    loop.run_in_executor(None, self._send_raw, cmd_str),
                    timeout=_timeout,
                )
            except TimeoutError:
                self._close_pipes()
                raise AudacityMCPError(
                    ErrorCode.PIPE_TIMEOUT,
                    f"Command timed out after {_timeout}s: {command}",
                )
            except AudacityMCPError:
                raise
            except Exception as e:
                self._close_pipes()
                raise AudacityMCPError(ErrorCode.COMMAND_FAILED, str(e))
        return parse_response(raw)

    async def execute_long(self, command: str, extra_params: dict | None = None, **params) -> dict:
        """Same as execute() with the long timeout, for effects that render audio."""
        return await self.execute(
            command, extra_params, _timeout=Timeouts.LONG_COMMAND, **params
        )

    def close_sync(self):
        """Synchronous shutdown, for callers that cannot await — notably atexit."""
        self._close_pipes()

    async def close(self):
        self.close_sync()
