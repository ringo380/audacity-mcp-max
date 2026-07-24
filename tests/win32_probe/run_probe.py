r"""Exercise the real Win32 send path against a faithful relay stub.

Issue #11 asked for the retry loop to cover Windows, not just POSIX. The loop
is now shared, but every Win32 primitive under it - CreateFileW, WriteFile,
ReadFile on `\\.\pipe\...` - had never actually run. This drives them.

Run it with a Windows Python. On a Mac that means CrossOver/Wine; see
run_all.sh, which runs ONE scenario per process. That isolation is not
cosmetic: a relay thread blocked in ConnectNamedPipe cannot be stopped from
outside, so two relays in one process leave the client free to connect to the
older one and every count afterwards is attributed to the wrong server.

Wine reimplements named pipes, so a pass is evidence about our calls, not
proof about Windows. Exits non-zero if the scenario fails.
"""

from __future__ import annotations

import asyncio
import ctypes
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

if sys.platform != "win32":
    sys.exit("this probe only means anything on Windows (or Wine)")

from audacity_mcp import audacity_client as client_module  # noqa: E402
from audacity_mcp.audacity_client import AudacityClient  # noqa: E402
from audacity_mcp_shared.constants import PipePaths  # noqa: E402
from audacity_mcp_shared.error_codes import AudacityMCPError  # noqa: E402
from tests.win32_probe.win_relay_stub import HANGUP, WinRelay  # noqa: E402

ERROR_FILE_NOT_FOUND = 2
OPEN_EXISTING = 3
GENERIC_READ = 0x80000000
# The client's own handle, already given the right argtypes/restype. A second
# WinDLL object would default CreateFileW to a 32-bit return and truncate the
# handle, so the check below would misread every result.
kernel32 = client_module.kernel32


def assert_no_relay_is_listening():
    """Precondition. A leftover pipe from another process would answer our
    commands and quietly invalidate every connection count below."""
    handle = kernel32.CreateFileW(
        PipePaths.FROM_SRV, GENERIC_READ, 0, None, OPEN_EXISTING, 0, None
    )
    if handle != client_module.INVALID_HANDLE_VALUE:
        kernel32.CloseHandle(handle)
        sys.exit("PRECONDITION FAILED: something is already serving FromSrvPipe")
    err = ctypes.get_last_error()
    if err != ERROR_FILE_NOT_FOUND:
        sys.exit(f"PRECONDITION FAILED: unexpected error probing FromSrvPipe: {err}")


def report(name, condition, detail):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}: {detail}", flush=True)
    return 0 if condition else 1


def happy_path():
    relay = WinRelay()
    relay.start()
    client = AudacityClient()
    result = asyncio.run(client.execute("GetInfo", Type="Tracks"))
    client.close_sync()
    return report(
        "happy_path",
        result["success"] and relay.received and "GetInfo" in relay.received[0],
        f"success={result['success']} relay_saw={relay.received!r}",
    )


def handles_are_reused():
    """Windows named pipes are connection-oriented and the relay's read loop
    serves many commands per connection, so a second command must NOT force a
    reconnect. This is why the Win32 branch caches handles and POSIX does not."""
    relay = WinRelay()
    relay.start()
    client = AudacityClient()

    async def two():
        a = await client.execute("GetInfo", Type="Tracks")
        b = await client.execute("GetInfo", Type="Labels")
        return a, b

    a, b = asyncio.run(two())
    client.close_sync()
    return report(
        "handles_are_reused",
        a["success"] and b["success"] and relay.connections == 1 and len(relay.received) == 2,
        f"connections={relay.connections} commands={len(relay.received)}",
    )


def retry_after_hangup():
    """The failure #11 is about: the relay takes a command and drops the
    connection without answering. One attempt cannot survive that."""
    relay = WinRelay([HANGUP])
    relay.start()
    client = AudacityClient()
    try:
        result = asyncio.run(client.execute("GetInfo", Type="Tracks"))
        ok, detail = result["success"], f"success={result['success']}"
    except AudacityMCPError as e:
        ok, detail = False, f"raised {e.code.name}"
    client.close_sync()
    return report(
        "retry_after_hangup",
        ok and relay.connections >= 2 and len(relay.received) >= 2,
        f"{detail} connections={relay.connections} commands={len(relay.received)}",
    )


def retry_after_truncated_reply():
    """A reply that arrives but carries no terminator is the other half-open
    case: it must be retried, not returned as if it were the answer."""
    relay = WinRelay([b"Partial output with no status line\n\n"])
    relay.start()
    client = AudacityClient()
    try:
        result = asyncio.run(client.execute("GetInfo", Type="Tracks"))
        ok, detail = result["success"], f"success={result['success']}"
    except AudacityMCPError as e:
        ok, detail = False, f"raised {e.code.name}"
    client.close_sync()
    return report(
        "retry_after_truncated_reply",
        ok and len(relay.received) >= 2,
        f"{detail} commands={len(relay.received)}",
    )


def single_attempt_cannot_recover():
    """Mutation check, permanently wired in: with the retry loop cut to one
    attempt - the shape the Win32 branch had before #11 - the hangup scenario
    must fail. If this ever passes, the retry is not what saves the others."""
    relay = WinRelay([HANGUP])
    relay.start()
    client = AudacityClient()
    client._SEND_ATTEMPTS = 1
    try:
        result = asyncio.run(client.execute("GetInfo", Type="Tracks"))
        outcome, failed = f"unexpectedly succeeded: {result}", False
    except AudacityMCPError as e:
        outcome, failed = e.code.name, True
    client.close_sync()
    return report("single_attempt_cannot_recover", failed, outcome)


def no_pipes_is_a_clear_error():
    """Nothing listening at all must say so rather than hanging."""
    client = AudacityClient()
    try:
        asyncio.run(client.execute("GetInfo", Type="Tracks"))
        outcome, ok = "unexpectedly succeeded", False
    except AudacityMCPError as e:
        outcome, ok = e.code.name, e.code.name in ("PIPE_NOT_FOUND", "PIPE_OPEN_FAILED")
    client.close_sync()
    return report("no_pipes_is_a_clear_error", ok, outcome)


SCENARIOS = {
    f.__name__: f
    for f in (
        no_pipes_is_a_clear_error,
        happy_path,
        handles_are_reused,
        retry_after_hangup,
        retry_after_truncated_reply,
        single_attempt_cannot_recover,
    )
}


def main(argv):
    if len(argv) != 2 or argv[1] not in SCENARIOS:
        print("\n".join(SCENARIOS))
        return 0 if len(argv) == 1 else 2
    assert_no_relay_is_listening()
    try:
        return SCENARIOS[argv[1]]()
    except Exception as e:  # a crash is a failed scenario, not a lost run
        return report(argv[1], False, f"crashed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
