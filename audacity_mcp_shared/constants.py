import os
import sys
from pathlib import Path


class PipePaths:
    if sys.platform == "win32":
        TO_SRV = r"\\.\pipe\ToSrvPipe"
        FROM_SRV = r"\\.\pipe\FromSrvPipe"
    else:
        _uid = os.getuid()
        TO_SRV = f"/tmp/audacity_script_pipe.to.{_uid}"
        FROM_SRV = f"/tmp/audacity_script_pipe.from.{_uid}"


class Timeouts:
    PIPE_OPEN = 5.0
    PIPE_READ = 10.0
    COMMAND = 30.0
    LONG_COMMAND = 600.0  # 10 minutes — large files (2-3hr podcasts) need this


ALLOWED_EXPORT_FORMATS = {"wav", "mp3", "ogg", "flac", "aiff", "mp4"}

# Rates any sound device can be expected to play back. Resampling to something
# outside this set is allowed — Audacity accepts it — but it is worth flagging,
# because an exotic project rate is what makes Audacity fail to open the output
# device at playback time, far away from the call that caused it.
COMMON_SAMPLE_RATES = {8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000}

MAX_TRACKS = 500
MAX_LABEL_LENGTH = 1000

WHISPER_MODEL_SIZES = {"tiny", "base", "small", "medium", "large-v3"}
TRANSCRIPTION_TASKS = {"transcribe", "translate"}
SUBTITLE_FORMATS = {"srt", "vtt", "txt"}
