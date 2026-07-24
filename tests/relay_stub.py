"""A stand-in for Audacity's mod-script-pipe relay, for the SIGPIPE test.

Mirrors the POSIX branch of modules/scripting/mod-script-pipe/PipeServer.cpp:
open the incoming pipe, then the outgoing one, read one command, and write the
reply in pieces. Like the C++ relay - and unlike default Python - it leaves
SIGPIPE at its terminating default, so dropping the reader mid-reply kills this
process with signal 13, exactly as it killed Audacity (issue #19).

Run as a subprocess:
    relay_stub.py <to_fifo> <from_fifo> <chunks> <gap_seconds> [terminator_index]

`terminator_index` places the "BatchCommand finished" line; it defaults to the
last piece. Putting it earlier makes the client return successfully while the
relay is still writing the trailing pieces, which is when a hard close on the
success path would SIGPIPE.

Exit 0 means it wrote the whole reply unharmed; a negative return code from the
parent's point of view means a signal, and -13 is the SIGPIPE we are guarding
against.
"""
import signal
import sys
import time


def main() -> int:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    to_name, from_name, chunks, gap = (
        sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
    )
    terminator_at = int(sys.argv[5]) if len(sys.argv) > 5 else chunks - 1

    # to (incoming) first, from (outgoing) second, matching PipeServer.
    to_fifo = open(to_name, "r")
    from_fifo = open(from_name, "w")
    to_fifo.readline()  # the command; blocks until the client writes it

    pieces = ["Response line"] * chunks
    pieces[terminator_at] = "BatchCommand finished: OK"
    for i, piece in enumerate(pieces):
        from_fifo.write(piece + "\n")
        from_fifo.flush()  # a readerless FROM here raises SIGPIPE
        if i < len(pieces) - 1:
            time.sleep(gap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
