"""Product limits for voice capture and long-form transcription.

Keep these values in one small dependency-free module so the controller,
meeting pipeline, web bridge, UI, and tests cannot quietly drift apart.

The normal dictation limit is intentionally one 10-minute 16 kHz mono PCM WAV
(about 19.2 MB).  That remains below the 25 MB direct-upload ceiling shared by
the supported Groq free tier and OpenAI transcription endpoints, while keeping
the in-memory dictation buffer bounded.

Meetings are disk-backed and may run for four hours.  Long-form processing is
split into 10-minute chunks so each cloud request stays below the same provider
ceiling and local processing never loads the whole recording into RAM.
"""

SAMPLE_RATE = 16_000

DICTATION_MAX_SECONDS = 10 * 60
MEETING_MAX_SECONDS = 4 * 60 * 60
LONG_FORM_CHUNK_SECONDS = 10 * 60

DICTATION_MAX_SAMPLES = DICTATION_MAX_SECONDS * SAMPLE_RATE
MEETING_MAX_SAMPLES = MEETING_MAX_SECONDS * SAMPLE_RATE


def format_duration(seconds):
    """Return a compact, stable human-readable duration."""
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


DICTATION_MAX_DISPLAY = format_duration(DICTATION_MAX_SECONDS)
MEETING_MAX_DISPLAY = format_duration(MEETING_MAX_SECONDS)
