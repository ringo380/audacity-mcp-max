# Win32 pipe probe

The send retry loop is shared by both platforms, but the Win32 primitives under
it - `CreateFileW`, `WriteFile`, `ReadFile` on `\\.\pipe\ToSrvPipe` and
`\\.\pipe\FromSrvPipe` - only ever ran on paper. This drives them against a
stand-in for Audacity's own relay.

`win_relay_stub.py` is translated from the WIN32 branch of
`au3/modules/scripting/mod-script-pipe/PipeServer.cpp`, keeping everything the
client can observe: duplex message-mode pipes with 1024-byte buffers, ToSrv
created and connected before FromSrv, one connection serving many commands, and
a full teardown-and-recreate cycle when the client hangs up. It can be told to
hang up mid-cycle or to answer without a terminator.

## Running it

It needs a Windows Python. On macOS that means CrossOver or plain Wine:

```sh
# one-time: a 64-bit bottle with an embeddable Python at C:\py
CX=/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin
$CX/cxbottle --bottle audmcp64 --create --template win10_64
curl -sSLO https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip
unzip -q python-3.12.8-embed-amd64.zip \
    -d "$HOME/Library/Application Support/CrossOver/Bottles/audmcp64/drive_c/py"

CX_BOTTLE=audmcp64 ./tests/win32_probe/run_all.sh
```

The client and the shared package are stdlib-only, so the embeddable Python
needs nothing installed. `run_all.sh` starts one process per scenario, which is
required rather than tidy: a relay thread blocked in `ConnectNamedPipe` cannot
be stopped from outside, so a second relay in the same process leaves a stale
server listening and every connection count after it is credited to the wrong
one. Each run asserts up front that nothing is already serving the pipes.

Not wired into `scripts/verify.sh`, which must stay runnable with no bottle.

## What it establishes, and what it does not

Scenarios, and the mutation each one is known to catch:

| Scenario | Fails when |
| --- | --- |
| `no_pipes_is_a_clear_error` | the Win32 branch stops opening pipes |
| `happy_path` | the same |
| `handles_are_reused` | the post-success close stops being Windows-guarded |
| `retry_after_hangup` | attempts are cut to one, or the loop stops dropping handles between attempts |
| `retry_after_truncated_reply` | attempts are cut to one |
| `single_attempt_cannot_recover` | retrying stops being what rescues a hangup |

The last one is an inverted control: it asserts that a one-attempt client
*fails* the hangup scenario, so the four passes above cannot be coming from
somewhere other than the retry.

**Wine is not Windows.** It reimplements the named-pipe layer, so a pass here is
evidence that our `ctypes` declarations, handle types, call sequence and error
handling are right - not proof about a real Windows kernel. Anyone with a
Windows machine can get the stronger result by running the same scripts there
with no bottle: `python tests\win32_probe\run_probe.py <scenario>`.
