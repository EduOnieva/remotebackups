"""rsync exit codes, from rsync(1) EXIT VALUES, so a failure says what it was."""

import signal

RSYNC_EXIT_MEANINGS: dict[int, str] = {
    0: "success",
    1: "syntax or usage error",
    2: "protocol incompatibility",
    3: "errors selecting input/output files or dirs",
    4: "requested action not supported",
    5: "error starting client-server protocol",
    6: "daemon unable to append to log-file",
    10: "error in socket I/O",
    11: "error in file I/O",
    12: "error in rsync protocol data stream",
    13: "errors with program diagnostics",
    14: "error in IPC code",
    20: "received SIGUSR1 or SIGINT",
    21: "some error returned by waitpid()",
    22: "error allocating core memory buffers",
    23: "partial transfer due to error",
    24: "partial transfer due to vanished source files",
    25: "the --max-delete limit stopped deletions",
    30: "timeout in data send/receive",
    35: "timeout waiting for daemon connection",
    255: "ssh could not connect or authenticate",
}

# A transfer that ended with these moved data; the remaining problems are listed in the log
PARTIAL_TRANSFER_CODES = frozenset({23, 24})


def describe_exit(code: int) -> str:
    if code < 0:
        try:
            return f"killed by {signal.Signals(-code).name}"
        except ValueError:
            return f"killed by signal {-code}"
    return RSYNC_EXIT_MEANINGS.get(code, f"unknown rsync exit code {code}")
