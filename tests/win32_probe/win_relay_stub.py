r"""A faithful stand-in for Audacity's Windows mod-script-pipe relay.

Translated from au3/modules/scripting/mod-script-pipe/PipeServer.cpp (the
WIN32 branch), keeping the parts the client can observe:

  * both pipes are PIPE_ACCESS_DUPLEX, message type/mode, PIPE_WAIT,
    PIPE_REJECT_REMOTE_CLIENTS, PIPE_UNLIMITED_INSTANCES, 1024-byte buffers
  * ToSrvPipe is created first, then FromSrvPipe, then both are connected in
    that same order
  * one connection serves MANY commands - the read/reply loop only ends when
    ReadFile fails or returns zero bytes, i.e. when the client hangs up
  * on hangup both pipes are flushed, disconnected and closed, and the caller
    (ScriptCommandRelay's `while(true)`) starts a whole new PipeServer

Windows only. Runs under CrossOver/Wine, which is how it is exercised on a
Mac: Wine reimplements the named-pipe layer, so a pass here is strong
evidence about our ctypes calls rather than proof about Windows itself.

The `script` argument decides what each command gets back, so a test can make
the relay hang up mid-cycle or answer without a terminator.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INVALID_HANDLE_VALUE = wt.HANDLE(-1).value
PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_MESSAGE = 0x00000004
PIPE_READMODE_MESSAGE = 0x00000002
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
PIPE_UNLIMITED_INSTANCES = 255
ERROR_PIPE_CONNECTED = 535
N_BUFF = 1024

kernel32.CreateNamedPipeW.restype = wt.HANDLE
kernel32.CreateNamedPipeW.argtypes = [
    wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
    ctypes.c_void_p,
]
kernel32.ConnectNamedPipe.restype = wt.BOOL
kernel32.ConnectNamedPipe.argtypes = [wt.HANDLE, ctypes.c_void_p]
kernel32.DisconnectNamedPipe.restype = wt.BOOL
kernel32.DisconnectNamedPipe.argtypes = [wt.HANDLE]
kernel32.FlushFileBuffers.restype = wt.BOOL
kernel32.FlushFileBuffers.argtypes = [wt.HANDLE]
kernel32.ReadFile.restype = wt.BOOL
kernel32.ReadFile.argtypes = [
    wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
]
kernel32.WriteFile.restype = wt.BOOL
kernel32.WriteFile.argtypes = [
    wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]

OK_REPLY = b"BatchCommand finished: OK\n\n"

HANGUP = object()  # "read the command, then drop the connection with no reply"


def _create(name: str) -> int:
    handle = kernel32.CreateNamedPipeW(
        name,
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        PIPE_UNLIMITED_INSTANCES,
        N_BUFF,
        N_BUFF,
        50,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise OSError(f"CreateNamedPipeW({name}) failed: {ctypes.get_last_error()}")
    return handle


def _connect(handle: int) -> bool:
    if kernel32.ConnectNamedPipe(handle, None):
        return True
    return ctypes.get_last_error() == ERROR_PIPE_CONNECTED


class WinRelay:
    """Serves commands the way Audacity does, following `script`.

    `script` is a list of replies, one per command received across the whole
    run (not per connection - the counter keeps going after a hangup, so a
    test can say "drop the first command, answer the second"). Each entry is
    either bytes to write back or HANGUP. Commands past the end of the script
    get OK_REPLY.
    """

    def __init__(self, script=()):
        self.script = list(script)
        self.received = []          # every command line the relay read
        self.connections = 0        # how many PipeServer() cycles ran
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self.ready.wait(10):
            raise RuntimeError("relay did not create its pipes")

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._pipe_server_once()
            except OSError:
                return

    def _pipe_server_once(self):
        to_srv = _create(r"\\.\pipe\ToSrvPipe")
        from_srv = _create(r"\\.\pipe\FromSrvPipe")
        self.ready.set()
        try:
            _connect(to_srv)
            connected = _connect(from_srv)
            if not connected:
                return
            self.connections += 1
            buf = ctypes.create_string_buffer(N_BUFF)
            while True:
                n = wt.DWORD()
                ok = kernel32.ReadFile(to_srv, buf, N_BUFF, ctypes.byref(n), None)
                if not ok or n.value == 0:
                    break  # client hung up: end this PipeServer cycle
                request = buf.raw[: n.value].decode("utf-8", "replace")
                index = len(self.received)
                self.received.append(request)
                reply = self.script[index] if index < len(self.script) else OK_REPLY
                if reply is HANGUP:
                    break
                written = wt.DWORD()
                kernel32.WriteFile(from_srv, reply, len(reply), ctypes.byref(written), None)
            kernel32.FlushFileBuffers(to_srv)
            kernel32.DisconnectNamedPipe(to_srv)
            kernel32.FlushFileBuffers(from_srv)
            kernel32.DisconnectNamedPipe(from_srv)
        finally:
            kernel32.CloseHandle(to_srv)
            kernel32.CloseHandle(from_srv)
