from enum import IntEnum


class ErrorCode(IntEnum):
    """Every member here is raised somewhere — tests/test_tools.py enforces it.

    Values are explicit and stable, so a code that becomes useful later can be
    added back at its old number. Retired: 1005 PIPE_DISCONNECTED,
    2001 COMMAND_NOT_FOUND, 2002 COMMAND_TIMEOUT, 2003 COMMAND_REJECTED,
    3002 MISSING_PARAMETER — none were ever raised.
    """

    # Pipe errors (1000s)
    PIPE_NOT_FOUND = 1000
    PIPE_OPEN_FAILED = 1001
    PIPE_WRITE_FAILED = 1002
    PIPE_READ_FAILED = 1003
    PIPE_TIMEOUT = 1004

    # Command errors (2000s)
    COMMAND_FAILED = 2000

    # Validation errors (3000s)
    VALIDATION_FAILED = 3000
    INVALID_PARAMETER = 3001
    INVALID_PATH = 3003
    INVALID_FORMAT = 3004
    INJECTION_DETECTED = 3005
    VALUE_OUT_OF_RANGE = 3006


class AudacityMCPError(Exception):
    def __init__(self, code: ErrorCode, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code.name} ({code.value})] {message}")
