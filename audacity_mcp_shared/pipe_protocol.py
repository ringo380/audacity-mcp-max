import re
from audacity_mcp_shared.error_codes import AudacityMCPError, ErrorCode


_DANGEROUS_CHARS = re.compile(r"[\n\r\x00]")


def _validate_value(value: str) -> str:
    if _DANGEROUS_CHARS.search(value):
        raise AudacityMCPError(
            ErrorCode.INJECTION_DETECTED,
            f"Value contains illegal characters: {value!r}",
        )
    return value


def _quote_value(value: str) -> str:
    if " " in value or '"' in value or "=" in value or "\\" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


# A backslash followed by a lowercase 'n' in a path (e.g. C:\new) cannot survive
# Audacity's parameter parser. It runs Unescape, whose "\n" -> newline rule fires
# before "\\" -> "\", so our correctly-doubled C:\\new is read as backslash +
# newline. There is no escaping that avoids it - it is an Audacity-side bug
# (issue #5) - so the only thing to do is warn the caller before the path
# silently arrives corrupted and the command fails with a confusing not-found.
_CORRUPTING_BACKSLASH = re.compile(r"\\n")


def path_corruption_warning(path: str) -> str | None:
    """Return a warning if `path` will be mangled by Audacity, else None."""
    if _CORRUPTING_BACKSLASH.search(path):
        return (
            f"The path {path!r} contains a backslash followed by 'n', which "
            "Audacity's command parser turns into a newline (it unescapes \\n "
            "before \\\\). The path will arrive corrupted no matter how it is "
            "escaped; rename the folder or use a forward-slash path if Audacity "
            "accepts one on your system."
        )
    return None


def format_command(command: str, extra_params: dict | None = None, **params: str | int | float | bool) -> str:
    _validate_value(command)
    parts = [command + ":"]
    all_params = dict(params)
    if extra_params:
        all_params.update(extra_params)
    for key, val in all_params.items():
        _validate_value(key)
        if isinstance(val, bool):
            str_val = "1" if val else "0"
        else:
            str_val = str(val)
        _validate_value(str_val)
        parts.append(f"{key}={_quote_value(str_val)}")
    return " ".join(parts) + "\n"


def parse_response(raw: str) -> dict:
    lines = raw.strip().split("\n")
    result: dict = {"raw": raw.strip(), "success": False, "message": "", "data": {}}

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("BatchCommand finished:"):
            if "OK" in line:
                result["success"] = True
            else:
                if result["message"]:
                    result["message"] += "\n" + line
                else:
                    result["message"] = line
            continue

        if "=" in line:
            key, _, value = line.partition("=")
            result["data"][key.strip()] = value.strip()
        else:
            if result["message"]:
                result["message"] += "\n" + line
            else:
                result["message"] = line

    return result
