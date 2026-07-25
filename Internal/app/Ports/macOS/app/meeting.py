"""Meeting processing orchestration.

Ties together:
  • Audio recording  → same mic pipeline as main dictation (mumble.py)
  • Transcription    → existing faster-whisper / cloud STT (mumble._transcribe)
  • Diarisation      → meeting_diarise.diarise()
  • Storage          → meeting_store.save_meeting()
  • AI summary       → via existing LLM providers (ai.py)

This module is DESIGNED to be called BY the Mumble controller — it does not
import mumble (to avoid circular deps) but expects the controller to pass in
its transcription callable and settings.

Owner 2026-06-29 — meeting-mode milestone.
"""

import math
import os
import queue
import re
import threading
import time
import uuid
import wave

import ai
import meeting_diarise
import meeting_store
import branding
import processing_route
from recording_limits import (
    LONG_FORM_CHUNK_SECONDS,
    MEETING_MAX_SAMPLES,
    MEETING_MAX_SECONDS,
    SAMPLE_RATE,
)


NORMALIZE_BLOCK_SECONDS = 60
ANALYSIS_CHUNK_CHARS = 55_000
LONG_FORM_OVERLAP_SECONDS = 2.0
# One process-wide lease covers live stops, imports, retry, and startup recovery.
# Instance-local sets cannot prevent a fresh MeetingRecorder from racing an
# import already persisted as ``processing``.
_PROCESSING_LOCK = threading.Lock()
_PROCESSING_IDS = set()


class MeetingRecorder:
    """Encapsulates a single meeting recording lifecycle.

    Usage:
        recorder = MeetingRecorder(transcribe_fn, settings)
        recorder.start()
        # … meeting happens …
        meeting_id = recorder.stop(title="Sprint Planning")
    """

    def __init__(self, transcribe_fn, settings, island_callback=None):
        """
        Args:
            transcribe_fn: callable(audio_bytes, want_words=False) -> raw_text
                           (typically mumble._transcribe)
            settings: Settings-like object
            island_callback: optional fn(state, timer, speaker_count)
                             to update the island overlay during recording
        """
        self._transcribe = transcribe_fn
        self._settings = settings
        self._island_cb = island_callback
        self._stream = None
        self._recording = False
        self._start_time = 0.0
        self._sample_count = 0
        self._audio_filename = None
        self._audio_full_path = None
        self._writer_queue = None
        self._writer_thread = None
        self._writer_error = None
        self._timer_thread = None
        self._lock = threading.Lock()
        # ``finish_capture`` may be reached from the UI stop command and the
        # shutdown path at nearly the same time.  Serialise it and remember the
        # result so a second call returns the original meeting instead of
        # registering the same WAV twice.
        self._finish_lock = threading.Lock()
        self._capture_finalized = False
        self._last_meeting_id = None
        self._limit_reached = False

    def start(self):
        """Open the mic and stream PCM to a durable, private WAV file."""
        import numpy as np
        import sounddevice as sd

        with self._lock:
            if self._stream is not None or self._recording or (
                    self._writer_thread is not None
                    and self._writer_thread.is_alive()):
                raise RuntimeError("A meeting is already recording.")

        self._sample_count = 0
        self._writer_error = None
        self._capture_finalized = False
        self._last_meeting_id = None
        self._limit_reached = False
        self._audio_filename, self._audio_full_path = _new_meeting_audio_path()
        self._writer_queue = queue.Queue(maxsize=1024)
        audio_path = self._audio_full_path
        writer_queue = self._writer_queue

        def _writer():
            try:
                with wave.open(audio_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(SAMPLE_RATE)
                    while True:
                        block = writer_queue.get()
                        if block is None:
                            break
                        wf.writeframesraw(block)
            except Exception as e:
                self._mark_capture_failed(f"The meeting audio writer failed: {e}")
                print(f"[meeting] audio writer failed: {e}")

        def _cb(indata, frames, time_info, status):
            if self._recording:
                if status and getattr(status, "input_overflow", False):
                    self._mark_capture_failed(
                        "Meeting recording stopped because the microphone input "
                        "overflowed. The audio captured before that point was saved.")
                    return
                remaining = MEETING_MAX_SAMPLES - self._sample_count
                if remaining <= 0:
                    self._mark_limit_reached()
                    return
                accepted = min(int(frames), int(remaining))
                pcm = (np.clip(indata[:accepted], -1.0, 1.0) * 32767.0).astype("<i2")
                try:
                    writer_queue.put_nowait(pcm.tobytes())
                    self._sample_count += accepted
                    if self._sample_count >= MEETING_MAX_SAMPLES:
                        self._mark_limit_reached()
                except queue.Full:
                    # Never continue after dropping a block: doing so creates an
                    # undetectable hole in the middle of an otherwise plausible
                    # transcript. Stop at the last contiguous queued sample and
                    # let the watchdog flush/finalize that honest prefix.
                    self._mark_capture_failed(
                        "Meeting recording stopped because the disk writer could "
                        "not keep up. The audio captured before that point was saved.")

        stream = None
        writer_thread = None
        try:
            # Construct the stream before starting any background resources. A
            # missing/unavailable microphone must not leave a blocked writer.
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                device=self._settings.get("mic_device", None),
                callback=_cb,
            )
            writer_thread = threading.Thread(target=_writer, daemon=True)
            self._writer_thread = writer_thread
            writer_thread.start()
            self._stream = stream
            self._start_time = time.time()
            self._recording = True
            stream.start()
        except Exception:
            self._recording = False
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            if writer_thread is not None:
                try:
                    writer_queue.put_nowait(None)
                except queue.Full:
                    pass
                writer_thread.join(timeout=2.0)
            try:
                os.remove(audio_path)
            except OSError:
                pass
            self._stream = None
            self._writer_queue = None
            self._writer_thread = None
            self._audio_filename = None
            self._audio_full_path = None
            raise

        # Timer thread for island updates
        self._timer_thread = threading.Thread(
            target=self._timer_loop, daemon=True)
        self._timer_thread.start()

    def finish_capture(self, title=""):
        """Stop capture and persist a resumable meeting record before AI work."""
        with self._finish_lock:
            if self._capture_finalized:
                if title and self._last_meeting_id:
                    meeting_store.update_meeting(
                        self._last_meeting_id, title=title.strip())
                return self._last_meeting_id
            meeting_id = self._finish_capture_once(title)
            self._capture_finalized = True
            self._last_meeting_id = meeting_id
            return meeting_id

    def _finish_capture_once(self, title=""):
        """Single, serialised implementation behind :meth:`finish_capture`."""
        if self._audio_full_path is None:
            return None
        with self._lock:
            self._recording = False

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        writer_queue = self._writer_queue
        writer_thread = self._writer_thread
        if writer_queue is not None:
            try:
                writer_queue.put(None, timeout=2.0)
            except queue.Full:
                self._writer_error = (
                    self._writer_error
                    or "The audio writer stopped before capture could be finalized.")
        if writer_thread is not None:
            writer_thread.join(timeout=10.0)
            if writer_thread.is_alive() and writer_queue is not None:
                # The queue may have drained during the first join. Make one
                # final non-blocking attempt to wake the writer, then give up in
                # bounded time rather than hanging stop/quit forever.
                try:
                    writer_queue.put_nowait(None)
                except queue.Full:
                    pass
                writer_thread.join(timeout=2.0)
            if writer_thread.is_alive():
                self._writer_error = (
                    self._writer_error or "The audio writer did not stop cleanly.")
                self._writer_queue = writer_queue
                self._writer_thread = writer_thread
                raise RuntimeError(self._writer_error)
        self._writer_queue = None
        self._writer_thread = None

        if self._sample_count <= 0:
            try:
                os.remove(self._audio_full_path)
            except OSError:
                pass
            self._audio_filename = None
            self._audio_full_path = None
            return None
        if not os.path.isfile(self._audio_full_path) or os.path.getsize(
                self._audio_full_path) <= 44:
            raise RuntimeError(self._writer_error or "The meeting audio file is incomplete.")
        try:
            written = _wav_audio_info(self._audio_full_path)
        except Exception as e:
            raise RuntimeError(
                self._writer_error or f"The meeting audio file is incomplete: {e}") from e
        if (written["channels"] != 1 or written["sample_width"] != 2
                or written["sample_rate"] != SAMPLE_RATE
                or written["frame_count"] <= 0):
            raise RuntimeError(
                self._writer_error or "The meeting audio file has invalid parameters.")
        # Accepted samples can exceed written frames if the writer itself fails
        # with blocks still queued. The closed WAV header is the source of truth
        # for both duration and the resumable record.
        self._sample_count = int(written["frame_count"])
        duration = written["duration_sec"]
        branding.protect_private_path(self._audio_full_path)
        if not title:
            title = _auto_meeting_title(duration)
        meeting_id = meeting_store.save_meeting(
            title=title,
            audio_path=self._audio_filename,
            duration_sec=round(duration, 1),
            segments=[],
            speakers=[],
            processing_mode=_meeting_processing_mode(self._settings),
            status="interrupted" if self._writer_error else "processing",
            error=self._writer_error,
            capture_warning=self._writer_error,
        )
        if not meeting_id:
            raise RuntimeError("The meeting metadata could not be saved.")
        # The durable store now owns the filename.  Clearing the capture path
        # prevents any later lifecycle path from accidentally treating it as a
        # still-open recording.
        self._audio_filename = None
        self._audio_full_path = None
        return meeting_id

    def process_pending(self, meeting_id):
        """Transcribe a durable pending meeting; safe to retry after restart."""
        if not meeting_id:
            return None
        with _PROCESSING_LOCK:
            if meeting_id in _PROCESSING_IDS:
                return None
            _PROCESSING_IDS.add(meeting_id)
        try:
            return self._process_pending_once(meeting_id)
        finally:
            with _PROCESSING_LOCK:
                _PROCESSING_IDS.discard(meeting_id)

    def _process_pending_once(self, meeting_id):
        """Single-flight implementation behind :meth:`process_pending`."""
        record = meeting_store.get_meeting(meeting_id)
        if not record:
            return None
        status = record.get("status", "ready")
        if status == "ready":
            return meeting_id
        if status not in ("processing", "interrupted"):
            return None
        path = _resolve_audio_path(record.get("audio_path"))
        if not path:
            meeting_store.update_meeting(
                meeting_id, status="failed", error="Recording file is missing.")
            return None
        try:
            # An interrupted record visibly returns to processing while it is
            # retried.  Do this before expensive audio/model work.
            meeting_store.update_meeting(
                meeting_id, status="processing", error=None)
            segments, speakers = _process_wav_path(
                path, self._transcribe, self._settings,
                island_callback=self._island_cb)
            meeting_store.update_meeting(
                meeting_id, segments=segments, speakers=speakers,
                status="ready", error=None)
            if self._island_cb:
                self._island_cb("done", record.get("duration_sec", 0),
                                len(speakers))
            return meeting_id
        except Exception as e:
            print(f"[meeting] processing failed: {e}")
            meeting_store.update_meeting(
                meeting_id, status="interrupted", error=str(e))
            return meeting_id

    def stop(self, title=""):
        """Compatibility path: durably finish capture, then process it."""
        meeting_id = self.finish_capture(title)
        if not meeting_id:
            return None
        return self.process_pending(meeting_id)

    def recover_pending(self):
        """Resume meetings interrupted by a previous shutdown."""
        recovered = []
        meeting_store.cleanup_orphan_audio()
        for record in meeting_store.list_pending_meetings():
            if self.process_pending(record.get("id")):
                recovered.append(record.get("id"))
        return recovered

    def pause(self):
        """Pause frame accumulation without closing the stream.
        The stream stays open; incoming frames are discarded.
        The island shows 'paused' state."""
        if self._stream is None or not self._recording:
            return False
        self._recording = False
        if self._island_cb:
            self._island_cb("paused", self._captured_seconds(), 0)
        return True

    def resume(self):
        """Resume frame accumulation after a pause.
        The island returns to 'recording' state."""
        if (self._stream is None or self._recording or self._limit_reached
                or self._writer_error):
            return False
        self._recording = True
        if self._island_cb:
            self._island_cb("recording", self._captured_seconds(), 0)
        return True

    def _captured_seconds(self):
        """Recorded-audio time, excluding time spent paused."""
        return int(self._sample_count / float(SAMPLE_RATE))

    @property
    def limit_reached(self):
        """Whether capture stopped accepting audio at the four-hour limit."""
        return self._limit_reached

    def _mark_limit_reached(self):
        """Hard-stop sample accumulation and notify the owning controller once."""
        if self._limit_reached:
            return
        self._limit_reached = True
        self._recording = False
        if self._island_cb:
            self._island_cb("limit_reached", MEETING_MAX_SECONDS, 0)

    def _mark_capture_failed(self, message):
        """Stop at the last contiguous block after a capture-integrity error."""
        first_failure = not self._writer_error
        if first_failure:
            self._writer_error = str(message or "Meeting audio capture failed.")
        self._recording = False
        if first_failure and self._island_cb:
            self._island_cb("capture_error", self._captured_seconds(), 0)

    def _timer_loop(self):
        """Update the island with elapsed time every second."""
        while self._stream is not None:
            if self._limit_reached:
                # The audio callback cannot safely block on stream shutdown or
                # disk flush. Finalize from this helper thread within one tick so
                # the WAV header + pending metadata are durable even if the user
                # does not immediately press Stop.
                try:
                    self.finish_capture("")
                except Exception as e:
                    print(f"[meeting] limit finalization failed: {e}")
                return
            if self._writer_error:
                # As with the duration cap, stream shutdown and disk flush cannot
                # run in PortAudio's callback. Finalize on this helper thread.
                try:
                    self.finish_capture("")
                except Exception as e:
                    print(f"[meeting] capture-error finalization failed: {e}")
                return
            elapsed = self._captured_seconds()
            if self._recording and self._island_cb:
                self._island_cb("recording", elapsed, 0)
            time.sleep(1.0)


def _new_meeting_audio_path():
    """Allocate a collision-proof private recording filename."""
    meetings_dir = os.path.join(branding.DATA_DIR, "meetings_audio")
    os.makedirs(meetings_dir, mode=0o700, exist_ok=True)
    branding.protect_private_path(meetings_dir, directory=True)
    filename = f"meeting_{uuid.uuid4().hex}.wav"
    return filename, os.path.join(meetings_dir, filename)


def _wav_audio_info(path):
    """Return validated WAV metadata without reading its sample payload."""
    with wave.open(path, "rb") as wf:
        channels = int(wf.getnchannels())
        width = int(wf.getsampwidth())
        sample_rate = int(wf.getframerate())
        frame_count = int(wf.getnframes())
    if channels <= 0 or channels > 32:
        raise ValueError(f"Unsupported meeting WAV channel count: {channels}.")
    if width not in (1, 2, 3, 4):
        raise ValueError(f"Unsupported meeting WAV sample width: {width} bytes.")
    if sample_rate <= 0 or sample_rate > 384000:
        raise ValueError(f"Unsupported meeting WAV sample rate: {sample_rate} Hz.")
    duration = frame_count / float(sample_rate)
    return {
        "channels": channels,
        "sample_width": width,
        "sample_rate": sample_rate,
        "frame_count": frame_count,
        "duration_sec": duration,
    }


def _decode_pcm_frames(frames, width, channels):
    """Decode interleaved integer PCM bytes to mono float32."""
    import numpy as np
    if width == 1:
        audio = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8)
        if len(raw) % 3:
            raise ValueError("Meeting WAV contains an incomplete 24-bit sample.")
        triples = raw.reshape(-1, 3).astype(np.int32)
        values = triples[:, 0] | (triples[:, 1] << 8) | (triples[:, 2] << 16)
        values = (values ^ 0x800000) - 0x800000
        audio = values.astype(np.float32) / 8388608.0
    elif width == 4:
        audio = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported meeting WAV sample width: {width} bytes.")
    if channels > 1:
        if audio.size % channels:
            raise ValueError("Meeting WAV contains an incomplete channel frame.")
        audio = audio.reshape(-1, channels).mean(axis=1)
    return np.asarray(audio, dtype=np.float32).reshape(-1)


def _load_wav_audio(path):
    """Load a bounded WAV. Long-form processing uses the chunk iterator below."""
    info = _wav_audio_info(path)
    if info["duration_sec"] > MEETING_MAX_SECONDS:
        raise ValueError(
            f"Meeting audio exceeds the {MEETING_MAX_SECONDS // 3600}-hour limit.")
    with wave.open(path, "rb") as wf:
        frames = wf.readframes(info["frame_count"])
    audio = _decode_pcm_frames(
        frames, info["sample_width"], info["channels"])
    return audio, info["sample_rate"]


def _resampled_frame_total(source_frames, source_rate, target_rate=SAMPLE_RATE):
    """Exact cumulative target-frame boundary for blockwise resampling."""
    return max(0, int(round(
        int(source_frames) * float(target_rate) / int(source_rate))))


def _resample_audio(audio, source_rate, target_rate=SAMPLE_RATE,
                    output_length=None):
    """Resample one bounded mono block without retaining the source file."""
    import numpy as np
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if output_length is None:
        new_len = _resampled_frame_total(
            len(audio), source_rate, target_rate)
    else:
        new_len = max(0, int(output_length))
    if audio.size == 0 or new_len == 0:
        return np.empty(0, dtype=np.float32)
    if int(source_rate) == int(target_rate) and new_len == len(audio):
        return audio
    try:
        import scipy.signal
        return scipy.signal.resample(audio, new_len).astype(np.float32)
    except ImportError:
        old_x = np.arange(len(audio), dtype=np.float64)
        new_x = np.linspace(0, max(0, len(audio) - 1), new_len)
        return np.interp(new_x, old_x, audio).astype(np.float32)


def _iter_wav_audio(path, chunk_seconds=LONG_FORM_CHUNK_SECONDS):
    """Yield bounded chunks with a small overlap around hard boundaries."""
    info = _wav_audio_info(path)
    if info["duration_sec"] > MEETING_MAX_SECONDS:
        raise ValueError(
            f"Meeting audio exceeds the {MEETING_MAX_SECONDS // 3600}-hour limit.")
    source_rate = info["sample_rate"]
    frames_per_chunk = max(1, int(source_rate * float(chunk_seconds)))
    overlap_seconds = min(
        LONG_FORM_OVERLAP_SECONDS, max(0.0, float(chunk_seconds) * 0.1))
    overlap_frames = min(
        max(0, int(source_rate * overlap_seconds)), frames_per_chunk - 1)
    stride_frames = max(1, frames_per_chunk - overlap_frames)
    source_frame_offset = 0
    with wave.open(path, "rb") as wf:
        while source_frame_offset < info["frame_count"]:
            wf.setpos(source_frame_offset)
            raw = wf.readframes(min(
                frames_per_chunk, info["frame_count"] - source_frame_offset))
            if not raw:
                break
            audio = _decode_pcm_frames(
                raw, info["sample_width"], info["channels"])
            offset = source_frame_offset / float(source_rate)
            source_frame_end = source_frame_offset + len(audio)
            audio = _resample_audio(audio, source_rate, SAMPLE_RATE)
            if audio.size:
                yield audio, offset
            if source_frame_end >= info["frame_count"]:
                break
            source_frame_offset += stride_frames


_TRANSCRIPT_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def _strip_repeated_boundary_prefix(text, prior_text, max_words=64):
    """Remove words repeated by an overlapped transcription boundary."""
    text = str(text or "")
    previous = list(_TRANSCRIPT_WORD_RE.finditer(str(prior_text or "")))
    current = list(_TRANSCRIPT_WORD_RE.finditer(text))
    if not previous or not current:
        return text.strip()
    previous_words = [m.group(0).casefold() for m in previous]
    current_words = [m.group(0).casefold() for m in current]
    limit = min(len(previous_words), len(current_words), int(max_words))
    for count in range(limit, 0, -1):
        if previous_words[-count:] == current_words[:count]:
            cut = current[count - 1].end()
            return text[cut:].lstrip(" \t\r\n,.;:!?-–—").strip()
    return text.strip()


def _process_audio(audio, sample_rate, transcribe_fn, settings,
                   island_callback=None):
    """Shared transcription/diarisation stage for durable meeting audio."""
    if island_callback:
        island_callback("transcribing", 0, 0)
    segments = _transcribe_audio_segments(
        audio, sample_rate, transcribe_fn, settings)
    if island_callback:
        island_callback("diarising", 0, 0)
    try:
        result = meeting_diarise.diarise(
            audio, sample_rate, segments=segments, settings=settings)
        return result.get("segments", segments), result.get("speakers", [])
    except Exception as e:
        print(f"[meeting] diarisation failed: {e}")
        speakers = [{"label": "Speaker 1", "name": None,
                     "color": meeting_diarise.SPEAKER_COLORS[0]}]
        for segment in segments:
            segment["speaker"] = "Speaker 1"
        return segments, speakers


def _transcribe_audio_segments(audio, sample_rate, transcribe_fn, settings):
    """Transcribe one bounded chunk while respecting Local/Cloud selection."""
    # Meetings need speech timestamps for useful speaker assignment.  The old
    # path requested plain text and then spread sentences evenly over the whole
    # file; consecutive synthetic segments had zero silence gaps, so the
    # default lightweight diariser labelled virtually every real meeting as one
    # speaker.  Mumble's local Whisper path already exposes word timestamps via
    # ``want_words=True``.  Keep a compatibility fallback for embedders whose
    # callable predates that keyword.
    # Explicit Cloud mode must remain Cloud: Mumble's ``want_words=True`` path
    # intentionally forces local Whisper. Cloud providers currently return
    # plain text, so those chunks use the documented approximate fallback until
    # verbose timestamp responses are implemented by the shared STT adapter.
    want_words = ((settings.get("transcription_mode", "local") or "local")
                  .strip().lower() != "cloud")
    try:
        transcription = transcribe_fn(audio, want_words=want_words)
    except TypeError as exc:
        if "want_words" not in str(exc):
            raise
        transcription = transcribe_fn(audio)

    words = []
    if (isinstance(transcription, tuple) and len(transcription) >= 2):
        raw_text = transcription[0] or ""
        words = transcription[1] or []
    else:
        raw_text = transcription or ""

    duration = len(audio) / float(max(sample_rate, 1))
    segments = _word_timestamps_to_segments(words, duration)
    if not segments:
        segments = _transcription_to_segments(raw_text, audio, sample_rate)
    return segments


def _process_wav_path(path, transcribe_fn, settings, island_callback=None,
                      chunk_seconds=LONG_FORM_CHUNK_SECONDS):
    """Transcribe a durable WAV in bounded chunks and stitch absolute times.

    A single chunk can use the configured neural diariser. Multi-chunk files
    use one global lightweight pass after per-chunk energy computation; running
    pyannote independently per chunk would reset speaker identities at every
    ten-minute boundary and produce misleading labels.
    """
    if island_callback:
        island_callback("transcribing", 0, 0)

    all_segments = []
    first_audio = None
    chunk_count = 0
    covered_until = None
    for audio, offset in _iter_wav_audio(path, chunk_seconds=chunk_seconds):
        chunk_count += 1
        if chunk_count == 1:
            first_audio = audio
        elif chunk_count == 2:
            # We now know this is long-form; no need to retain chunk one.
            first_audio = None
        relative = _transcribe_audio_segments(
            audio, SAMPLE_RATE, transcribe_fn, settings)
        prior_text = " ".join(
            str(s.get("text", "") or "") for s in all_segments[-8:])
        for segment in relative:
            start = float(segment.get("start_sec", 0.0) or 0.0)
            end = float(segment.get("end_sec", start) or start)
            absolute_start = start + offset
            absolute_end = end + offset
            if covered_until is not None:
                original_text = str(segment.get("text", "") or "").strip()
                deduped_text = _strip_repeated_boundary_prefix(
                    original_text, prior_text)
                if absolute_end <= covered_until:
                    # Audio coverage is not proof that speech was transcribed.
                    # Retain novel text recovered wholly inside the overlap;
                    # discard only text that lexically repeats the committed
                    # transcript (or retain its novel suffix).
                    if deduped_text != original_text:
                        segment["text"] = deduped_text
                        if not segment["text"]:
                            continue
                elif absolute_start < covered_until:
                    segment["text"] = deduped_text
                    if not segment["text"]:
                        continue
                    absolute_start = covered_until
                    start = max(start, covered_until - offset)
            segment["energy"] = round(
                meeting_diarise._compute_segment_energy(
                    audio, start, end, SAMPLE_RATE), 1)
            segment["start_sec"] = round(absolute_start, 2)
            segment["end_sec"] = round(absolute_end, 2)
            all_segments.append(segment)
            prior_text = (prior_text + " " + str(
                segment.get("text", "") or "")).strip()
        chunk_end = offset + (len(audio) / float(SAMPLE_RATE))
        covered_until = (chunk_end if covered_until is None
                         else max(covered_until, chunk_end))

    if island_callback:
        island_callback("diarising", 0, 0)
    if chunk_count == 0:
        return [], []
    if chunk_count == 1:
        result = meeting_diarise.diarise(
            first_audio, SAMPLE_RATE, segments=all_segments, settings=settings)
    else:
        result = meeting_diarise._lightweight_with_segments(
            None, SAMPLE_RATE, all_segments)
        result["method"] = "lightweight-chunked"
        result = meeting_diarise.infer_speaker_names(result)
    return result.get("segments", all_segments), result.get("speakers", [])


def process_audio_file(path, transcribe_fn, settings, title="", on_saved=None):
    """Transcribe + diarise a pre-recorded WAV/MP3/FLAC/OGG file.

    Returns the meeting_id, or None on failure."""
    try:
        path = os.fspath(path)
    except TypeError:
        return None
    # Preflight duration, then normalize in bounded blocks.  A four-hour source
    # must never be materialized as one multi-gigabyte float array.
    try:
        audio_path, duration = _normalize_audio_file(path)
    except Exception as e:
        print(f"[meeting] audio read failed: {e}")
        return None

    # Persist the normalized audio and a pending record BEFORE expensive work.
    if not title:
        title = _auto_meeting_title(duration)
    # Publish the processing record and claim it under one lease. Recovery can
    # see the row as soon as save_meeting returns, so adding the ID later would
    # leave a race where it could start a second transcription.
    with _PROCESSING_LOCK:
        meeting_id = meeting_store.save_meeting(
            title=title,
            audio_path=audio_path,
            duration_sec=round(duration, 1),
            segments=[],
            speakers=[],
            processing_mode=_meeting_processing_mode(settings),
            status="processing",
        )
        if meeting_id:
            _PROCESSING_IDS.add(meeting_id)
    if not meeting_id:
        meeting_store._delete_audio_file(audio_path, missing_ok=True)
        return None
    try:
        if on_saved:
            try:
                on_saved(meeting_id)
            except Exception as e:
                # Persistence succeeded; a UI notification failure must never
                # turn a recoverable meeting into an import failure.
                print(f"[meeting] import saved callback failed: {e}")
        try:
            segments, speakers = _process_wav_path(
                _resolve_audio_path(audio_path), transcribe_fn, settings)
            meeting_store.update_meeting(
                meeting_id, segments=segments, speakers=speakers,
                status="ready", error=None)
            return meeting_id
        except Exception as e:
            meeting_store.update_meeting(
                meeting_id, status="interrupted", error=str(e))
            print(f"[meeting] import processing failed: {e}")
            # The normalized audio and pending metadata are already durable.
            # Return its ID so the UI can show/retry the interrupted meeting.
            return meeting_id
    finally:
        with _PROCESSING_LOCK:
            _PROCESSING_IDS.discard(meeting_id)


def _format_timestamp(seconds):
    seconds = max(0, int(float(seconds or 0)))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return (f"{hours}:{minutes:02d}:{secs:02d}" if hours
            else f"{minutes}:{secs:02d}")


def _speaker_name(meeting_record, label):
    for speaker in meeting_record.get("speakers", []) or []:
        if speaker.get("label") == label:
            return speaker.get("name") or label or "Speaker"
    return label or "Speaker"


def _meeting_transcript_lines(meeting_record, timestamps=True):
    """Render one line per stored segment, preserving segment boundaries."""
    lines = []
    for segment in meeting_record.get("segments", []) or []:
        content = str(segment.get("text", "") or "").strip()
        if not content:
            continue
        speaker = _speaker_name(
            meeting_record, segment.get("speaker", "Speaker"))
        if timestamps:
            stamp = _format_timestamp(segment.get("start_sec", 0))
            lines.append(f"[{stamp}] {speaker}: {content}")
        else:
            lines.append(f"{speaker}: {content}")
    return lines


def _chunk_transcript_lines(lines, max_chars=ANALYSIS_CHUNK_CHARS):
    """Group whole transcript segments into context-sized analysis chunks."""
    chunks = []
    current = []
    current_chars = 0
    for original in lines or []:
        pending = [str(original)]
        while pending:
            line = pending.pop(0)
            # Preserve normal segment boundaries. Only a pathological single
            # segment is split, and then every character still reaches a map.
            if len(line) > max_chars:
                cut = line.rfind(" ", 0, max_chars)
                if cut < max_chars // 2:
                    cut = max_chars
                pending.insert(0, line[cut:].lstrip())
                line = line[:cut].rstrip()
            added = len(line) + (1 if current else 0)
            if current and current_chars + added > max_chars:
                chunks.append("\n".join(current))
                current = []
                current_chars = 0
                added = len(line)
            if line:
                current.append(line)
                current_chars += added
    if current:
        chunks.append("\n".join(current))
    return chunks


def _analysis_context(settings, context=""):
    """Resolve the configured LLM call context, or None when unavailable."""
    invocation = processing_route.snapshot_inputs(
        settings, feature="meetings", lane="meeting_analysis",
        context=context, context_policy="meeting_transcript",
    )
    info = ai.PROVIDERS.get(invocation.route.provider) or {}
    return ai, info, invocation


def _analysis_call(context, system, user, max_tokens, timeout):
    ai_module, info, invocation = context
    decision = invocation.route
    return processing_route.call_provider(
        decision, ai_module.cerebras_chat,
        system, user, decision.api_key, model=decision.model, url=info.get("url"),
        max_tokens=max_tokens, timeout=timeout,
        expected_feature=decision.feature, expected_lane=decision.lane)


def _dedupe_strings(items):
    """Stable, case-insensitive dedupe for model-produced string arrays."""
    output = []
    seen = set()
    for item in items or []:
        value = str(item or "").strip()
        identity = re.sub(r"\s+", " ", value).casefold()
        if value and identity not in seen:
            seen.add(identity)
            output.append(value)
    return output


def _strip_json_fence(result):
    text = (result or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_json_list(result):
    import json
    text = _strip_json_fence(result)
    if text.startswith("[") and text.endswith("]"):
        parsed = json.loads(text)
        return _dedupe_strings(parsed if isinstance(parsed, list) else [])
    return _dedupe_strings(
        line.strip("- ").strip() for line in text.splitlines() if line.strip())


def _parse_deep_json(result):
    import json
    data = json.loads(_strip_json_fence(result))
    if not isinstance(data, dict):
        raise ValueError("Deep meeting analysis did not return a JSON object.")
    return {
        "summary": str(data.get("summary", "") or "").strip(),
        "action_items": _dedupe_strings(data.get("action_items", []) or []),
        "key_decisions": _dedupe_strings(data.get("key_decisions", []) or []),
        "open_questions": _dedupe_strings(data.get("open_questions", []) or []),
    }


def summarize_meeting(meeting_id, settings):
    """Summarize every transcript segment with segment-aware map/reduce."""
    meeting_record = meeting_store.get_meeting(meeting_id)
    if not meeting_record:
        return None
    chunks = _chunk_transcript_lines(
        _meeting_transcript_lines(meeting_record, timestamps=True))
    context = _analysis_context(settings, context="\n\n".join(chunks))
    if not chunks or context is None:
        return None

    title = meeting_record.get("title", "Meeting")
    final_system = (
        "You are a precise meeting summarizer. Summarize the complete meeting. "
        "Lead with a one-sentence TL;DR, then list Key Decisions, Action Items "
        "(with assignee if mentioned), and Open Questions. Be concise. Output "
        "only the summary, no preamble.")
    try:
        if len(chunks) == 1:
            summary = _analysis_call(
                context, final_system, f"Meeting: {title}\n\n{chunks[0]}",
                max_tokens=900, timeout=90)
        else:
            map_system = (
                "Summarize this chronological part of a longer meeting. Preserve "
                "every decision, action item with assignee/deadline, unresolved "
                "question, and context needed for the whole-meeting summary. "
                "Output only concise notes.")
            partials = []
            for index, chunk in enumerate(chunks, 1):
                partial = _analysis_call(
                    context, map_system,
                    f"Meeting: {title}\nPart {index} of {len(chunks)}\n\n{chunk}",
                    max_tokens=900, timeout=90)
                if (partial or "").strip():
                    partials.append(f"Part {index}:\n{partial.strip()}")
            if not partials:
                return None
            summary = _analysis_call(
                context, final_system,
                f"Meeting: {title}\n\nChronological part summaries:\n\n"
                + "\n\n".join(partials), max_tokens=1100, timeout=90)
        summary = (summary or "").strip()
        if summary:
            meeting_store.update_meeting(meeting_id, summary=summary)
        return summary or None
    except Exception as e:
        print(f"[meeting] summary failed: {e}")
        return None


def _extract_list_analysis(meeting_id, settings, system, store_field,
                           error_label):
    meeting_record = meeting_store.get_meeting(meeting_id)
    if not meeting_record:
        return []
    chunks = _chunk_transcript_lines(
        _meeting_transcript_lines(meeting_record, timestamps=True))
    context = _analysis_context(settings, context="\n\n".join(chunks))
    if not chunks or context is None:
        return []
    title = meeting_record.get("title", "Meeting")
    try:
        items = []
        for index, chunk in enumerate(chunks, 1):
            result = _analysis_call(
                context, system,
                f"Meeting: {title}\nPart {index} of {len(chunks)}\n\n{chunk}",
                max_tokens=600, timeout=60)
            items.extend(_parse_json_list(result))
        items = _dedupe_strings(items)
        # A successful empty result is authoritative and clears stale findings;
        # exceptions return before this write and preserve the prior value.
        meeting_store.update_meeting(meeting_id, **{store_field: items})
        return items
    except Exception as e:
        print(f"[meeting] {error_label} failed: {e}")
        return []


def extract_action_items(meeting_id, settings):
    system = (
        "Extract every action item from this meeting part. Return a JSON array "
        "of strings, each with assignee and deadline when mentioned. Return [] "
        "when there are none; output JSON only.")
    return _extract_list_analysis(
        meeting_id, settings, system, "action_items", "action items extraction")


def extract_key_decisions(meeting_id, settings):
    system = (
        "Extract every key decision from this meeting part. Return a JSON array "
        "of clear, concise decision strings. Return [] when there are none; "
        "output JSON only.")
    return _extract_list_analysis(
        meeting_id, settings, system, "key_decisions", "key decisions extraction")


def extract_open_questions(meeting_id, settings):
    """Track unresolved questions chronologically across every transcript part."""
    import json
    meeting_record = meeting_store.get_meeting(meeting_id)
    if not meeting_record:
        return []
    chunks = _chunk_transcript_lines(
        _meeting_transcript_lines(meeting_record, timestamps=True))
    context = _analysis_context(settings, context="\n\n".join(chunks))
    if not chunks or context is None:
        return []
    title = meeting_record.get("title", "Meeting")
    system = (
        "Maintain the complete list of questions still unresolved after this "
        "chronological meeting part. Start from the supplied existing unresolved "
        "questions, add newly raised unanswered questions, and remove questions "
        "answered or resolved in this part. Return the complete updated JSON "
        "array of clear question strings; output JSON only.")
    try:
        unresolved = []
        for index, chunk in enumerate(chunks, 1):
            result = _analysis_call(
                context, system,
                f"Meeting: {title}\nPart {index} of {len(chunks)}\n"
                f"Existing unresolved questions: "
                f"{json.dumps(unresolved, ensure_ascii=False)}\n\n{chunk}",
                max_tokens=600, timeout=60)
            unresolved = _parse_json_list(result)
        # A successful empty result is authoritative even for one short meeting.
        meeting_store.update_meeting(meeting_id, open_questions=unresolved)
        return unresolved
    except Exception as e:
        print(f"[meeting] open questions extraction failed: {e}")
        return []


def process_meeting_deep(meeting_id, settings):
    """Analyze all chunks, then reconcile their chronological final state."""
    import json
    meeting_record = meeting_store.get_meeting(meeting_id)
    if not meeting_record:
        return None
    chunks = _chunk_transcript_lines(
        _meeting_transcript_lines(meeting_record, timestamps=True))
    context = _analysis_context(settings, context="\n\n".join(chunks))
    if not chunks or context is None:
        return None
    title = meeting_record.get("title", "Meeting")
    analysis_system = (
        "Analyze this meeting transcript and return a valid JSON object with "
        "EXACTLY: summary (2-4 sentence string), action_items (string array), "
        "key_decisions (string array), open_questions (unresolved string array). "
        "For a meeting part, mention answered questions and superseded decisions "
        "in the summary so a chronological reducer can reconcile later state. "
        "Be complete. Return JSON only, without markdown fences.")
    try:
        partials = []
        for index, chunk in enumerate(chunks, 1):
            raw = _analysis_call(
                context, analysis_system,
                f"Meeting: {title}\nPart {index} of {len(chunks)}\n\n{chunk}",
                max_tokens=1200, timeout=90)
            partials.append(_parse_deep_json(raw))

        if len(partials) == 1:
            final = partials[0]
        else:
            reduction_system = (
                "Merge these chronological partial meeting analyses into one "
                "complete JSON object with EXACTLY: summary, action_items, "
                "key_decisions, open_questions. Process parts in order: remove "
                "questions answered later and reconcile later cancellations or "
                "supersessions. Deduplicate without dropping distinct current "
                "facts. Return JSON only.")
            reduced_raw = _analysis_call(
                context, reduction_system,
                f"Meeting: {title}\n\nPartial analyses:\n"
                + json.dumps(partials, ensure_ascii=False),
                max_tokens=1600, timeout=90)
            final = _parse_deep_json(reduced_raw)
            # All three arrays are stateful. A later part may complete/cancel an
            # action, supersede a decision, or answer a question; blindly
            # re-unioning mapped candidates would resurrect obsolete findings.
            if not final["summary"]:
                final["summary"] = "\n\n".join(
                    part["summary"] for part in partials if part["summary"])

        meeting_store.update_meeting(
            meeting_id, summary=final["summary"],
            action_items=final["action_items"],
            key_decisions=final["key_decisions"],
            open_questions=final["open_questions"], processing_mode="deep")
        return final
    except Exception as e:
        print(f"[meeting] deep processing failed: {e}")
        return None


def export_meeting(meeting_id, fmt="txt"):
    """Export a meeting to the requested format.

    Returns {"ok": True, "content": str, "mime": str} or
            {"ok": False, "message": str}.
    Supports: txt, json, markdown (or md), html, clipboard.
    """
    meeting = meeting_store.get_meeting(meeting_id)
    if not meeting:
        return {"ok": False, "message": "Meeting not found."}

    import json

    fmt = (fmt or "txt").lower().strip()

    if fmt == "json":
        return {
            "ok": True,
            "content": json.dumps(meeting, indent=2),
            "mime": "application/json",
        }

    if fmt in ("markdown", "md"):
        content = _build_markdown_export(meeting)
        return {"ok": True, "content": content, "mime": "text/markdown"}

    if fmt == "html":
        content = _build_html_export(meeting)
        return {"ok": True, "content": content, "mime": "text/html"}

    if fmt == "clipboard":
        # Return TXT content for clipboard copy
        content = _build_txt_export(meeting)
        return {"ok": True, "content": content, "mime": "text/plain"}

    if fmt == "txt":
        content = _build_txt_export(meeting)
        return {"ok": True, "content": content, "mime": "text/plain"}

    return {"ok": False, "message": f"Unsupported export format: {fmt}"}


def set_processing_mode(mode, settings):
    """Persist the meeting processing mode. Returns True on success."""
    mode = (mode or "").strip().lower()
    if mode not in ("lightweight", "deep"):
        return False
    settings.set("meeting_processing_mode", mode)
    return True


def _meeting_processing_mode(settings):
    """Resolve the persisted mode defensively for new meeting records."""
    mode = (settings.get("meeting_processing_mode", "lightweight")
            or "lightweight").strip().lower()
    return mode if mode in ("lightweight", "deep") else "lightweight"


# ── Export builders ──────────────────────────────────────────────────────────


def _build_txt_export(meeting):
    """Build a plain-text export of the meeting."""
    lines = [f"# {meeting.get('title', 'Meeting')}",
             f"Duration: {meeting.get('duration_display', '')}",
             ""]
    if meeting.get("capture_warning"):
        lines.extend([
            "WARNING: Recording ended early.",
            str(meeting["capture_warning"]),
            "",
        ])
    for seg in meeting.get("segments", []):
        sp = _speaker_name(meeting, seg.get("speaker", "Speaker"))
        ts = seg.get("start_sec", 0)
        txt = seg.get("text", "")
        lines.append(f"[{_format_timestamp(ts)}] {sp}: {txt}")

    if meeting.get("summary"):
        lines.append("")
        lines.append("--- Summary ---")
        lines.append(meeting["summary"])

    if meeting.get("action_items"):
        lines.append("")
        lines.append("--- Action Items ---")
        for item in meeting["action_items"]:
            lines.append(f"\u2022 {item}")

    if meeting.get("key_decisions"):
        lines.append("")
        lines.append("--- Key Decisions ---")
        for item in meeting["key_decisions"]:
            lines.append(f"\u2022 {item}")

    if meeting.get("open_questions"):
        lines.append("")
        lines.append("--- Open Questions ---")
        for item in meeting["open_questions"]:
            lines.append(f"\u2022 {item}")

    return "\n".join(lines)


def _build_markdown_export(meeting):
    """Build a markdown export of the meeting."""
    lines = [f"# {meeting.get('title', 'Meeting')}",
             "",
             f"**Duration:** {meeting.get('duration_display', '')}",
             f"**Processing Mode:** {meeting.get('processing_mode', 'lightweight')}",
             ""]
    if meeting.get("capture_warning"):
        lines.extend([
            "> **Recording ended early.** " + str(meeting["capture_warning"]),
            "",
        ])

    if meeting.get("summary"):
        lines.append("## Summary")
        lines.append("")
        lines.append(meeting["summary"])
        lines.append("")

    if meeting.get("action_items"):
        lines.append("## Action Items")
        lines.append("")
        for item in meeting["action_items"]:
            lines.append(f"- {item}")
        lines.append("")

    if meeting.get("key_decisions"):
        lines.append("## Key Decisions")
        lines.append("")
        for item in meeting["key_decisions"]:
            lines.append(f"- {item}")
        lines.append("")

    if meeting.get("open_questions"):
        lines.append("## Open Questions")
        lines.append("")
        for item in meeting["open_questions"]:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("## Transcript")
    lines.append("")
    for seg in meeting.get("segments", []):
        sp = _speaker_name(meeting, seg.get("speaker", "Speaker"))
        ts = seg.get("start_sec", 0)
        txt = seg.get("text", "")
        ts_str = _format_timestamp(ts)
        lines.append(f"**[{ts_str}] {sp}:** {txt}")
        lines.append("")

    return "\n".join(lines)


def _build_html_export(meeting):
    """Build a self-contained HTML export of the meeting."""
    title = meeting.get("title", "Meeting")
    # Escape HTML-special characters in user-supplied text
    import html as _html

    def esc(s):
        return _html.escape(str(s))

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>{esc(title)}</title>",
        "<style>",
        "  body { font-family: system-ui, -apple-system, sans-serif; "
        "max-width: 800px; margin: 0 auto; padding: 40px 20px; "
        "background: #0d0d0d; color: #e0e0e0; line-height: 1.6; }",
        "  h1 { color: #D4AF37; border-bottom: 1px solid #333; "
        "padding-bottom: 8px; }",
        "  h2 { color: #D4AF37; margin-top: 28px; }",
        "  .meta { color: #888; margin-bottom: 24px; }",
        "  .warning { color: #f2c66d; border: 1px solid #7a5b20; "
        "padding: 10px 12px; border-radius: 8px; }",
        "  .segment { margin-bottom: 8px; padding: 6px 0; "
        "border-bottom: 1px solid #1a1a1a; }",
        "  .speaker { font-weight: 600; color: #ccc; }",
        "  .time { color: #666; font-size: 0.85em; margin-right: 8px; }",
        "  ul { padding-left: 20px; }",
        "  li { margin-bottom: 4px; }",
        "</style>",
        "</head>",
        "<body>",
        f"<h1>{esc(title)}</h1>",
        f'<p class="meta">Duration: {esc(meeting.get("duration_display", ""))} '
        f'&middot; Processing: {esc(meeting.get("processing_mode", "lightweight"))}</p>',
    ]

    if meeting.get("capture_warning"):
        parts.append(
            '<p class="warning"><strong>Recording ended early.</strong> '
            + esc(meeting["capture_warning"]) + "</p>")

    if meeting.get("summary"):
        parts.append("<h2>Summary</h2>")
        parts.append(f"<p>{esc(meeting['summary'])}</p>")

    if meeting.get("action_items"):
        parts.append("<h2>Action Items</h2>")
        parts.append("<ul>")
        for item in meeting["action_items"]:
            parts.append(f"<li>{esc(item)}</li>")
        parts.append("</ul>")

    if meeting.get("key_decisions"):
        parts.append("<h2>Key Decisions</h2>")
        parts.append("<ul>")
        for item in meeting["key_decisions"]:
            parts.append(f"<li>{esc(item)}</li>")
        parts.append("</ul>")

    if meeting.get("open_questions"):
        parts.append("<h2>Open Questions</h2>")
        parts.append("<ul>")
        for item in meeting["open_questions"]:
            parts.append(f"<li>{esc(item)}</li>")
        parts.append("</ul>")

    parts.append("<h2>Transcript</h2>")
    for seg in meeting.get("segments", []):
        sp = _speaker_name(meeting, seg.get("speaker", "Speaker"))
        ts = seg.get("start_sec", 0)
        txt = seg.get("text", "")
        ts_str = _format_timestamp(ts)
        parts.append(
            f'<div class="segment">'
            f'<span class="time">[{ts_str}]</span>'
            f'<span class="speaker">{esc(sp)}:</span> '
            f'{esc(txt)}'
            f'</div>'
        )

    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)


# ── Internal helpers ────────────────────────────────────────────────────────


def _word_timestamps_to_segments(words, audio_duration):
    """Build speech segments from Whisper word timestamps.

    Boundaries retain real silence gaps for diarisation. We also split at
    sentence punctuation and cap very long turns so transcript rendering stays
    readable. Malformed model output is ignored; callers fall back to the
    plain-text approximation when nothing valid remains.
    """
    try:
        duration = max(0.0, float(audio_duration or 0.0))
    except (TypeError, ValueError, OverflowError):
        duration = 0.0
    if not math.isfinite(duration):
        duration = 0.0

    clean = []
    for item in words or []:
        if isinstance(item, dict):
            token = item.get("word", "")
            start = item.get("start")
            end = item.get("end")
            probability = item.get("prob", item.get("probability", 0.5))
        else:
            token = getattr(item, "word", "")
            start = getattr(item, "start", None)
            end = getattr(item, "end", None)
            probability = getattr(item, "probability", 0.5)
        token = str(token or "").strip()
        if not token:
            continue
        try:
            start = float(start)
            end = float(end)
            probability = float(probability)
        except (TypeError, ValueError, OverflowError):
            continue
        if not (math.isfinite(start) and math.isfinite(end)):
            continue
        start = max(0.0, start)
        end = max(start, end)
        if duration:
            start = min(start, duration)
            end = min(max(start, end), duration)
        if not math.isfinite(probability):
            probability = 0.5
        clean.append({
            "word": token,
            "start": start,
            "end": end,
            "prob": min(1.0, max(0.0, probability)),
        })

    if not clean:
        return []
    clean.sort(key=lambda word: (word["start"], word["end"]))

    groups = []
    current = []
    for word in clean:
        if current:
            previous = current[-1]
            silence_gap = max(0.0, word["start"] - previous["end"])
            turn_length = previous["end"] - current[0]["start"]
            sentence_end = bool(re.search(
                r"[.!?][\"')\]]*$", previous["word"]))
            if (silence_gap >= 0.65 or turn_length >= 15.0
                    or (sentence_end and turn_length >= 2.0)):
                groups.append(current)
                current = []
        current.append(word)
    if current:
        groups.append(current)

    segments = []
    for group in groups:
        text = _join_transcribed_words([word["word"] for word in group])
        if not text:
            continue
        confidence = sum(word["prob"] for word in group) / len(group)
        segments.append({
            "start_sec": round(group[0]["start"], 2),
            "end_sec": round(group[-1]["end"], 2),
            "text": text,
            "confidence": round(confidence, 3),
        })
    return segments


def _join_transcribed_words(tokens):
    """Join stripped Whisper tokens without spaces before punctuation."""
    text = ""
    attach_left = re.compile(
        r"^[,.;:!?%\)\]\}]|^['’](?:s|re|ve|ll|d|m|t)\b",
        re.IGNORECASE)
    for token in tokens:
        token = str(token or "").strip()
        if not token:
            continue
        if not text or attach_left.search(token) or text.endswith(("(", "[", "{")):
            text += token
        else:
            text += " " + token
    return text.strip()


def _transcription_to_segments(raw_text, audio, sample_rate):
    """Convert a raw transcript string into approximate segments.
    When whisper word timestamps aren't available (standard transcribe path),
    we split the text into sentences and assign estimated start/end times
    proportional to text length. This is a reasonable fallback."""
    import re
    text = (raw_text or "").strip()
    if not text:
        return []

    duration = len(audio) / max(sample_rate, 1)
    # Split into sentence-like chunks
    chunks = re.split(r'(?<=[.!?])\s+', text)
    chunks = [c.strip() for c in chunks if c.strip()]
    if not chunks:
        return [{"start_sec": 0.0, "end_sec": round(duration, 2),
                 "text": text, "confidence": 0.5}]

    # Assign times proportional to text length
    total_chars = sum(len(c) for c in chunks) or 1
    segments = []
    current_time = 0.0
    for i, chunk in enumerate(chunks):
        chunk_duration = (len(chunk) / total_chars) * duration
        end_time = min(current_time + chunk_duration, duration)
        if i == len(chunks) - 1:
            end_time = duration  # last chunk gets remaining time
        segments.append({
            "start_sec": round(current_time, 2),
            "end_sec": round(end_time, 2),
            "text": chunk,
            "confidence": 0.5,
        })
        current_time = end_time

    return segments


def _source_audio_info(path):
    """Inspect an import without decoding it; returns metadata + backend."""
    try:
        if str(path).lower().endswith(".wav"):
            info = _wav_audio_info(path)
            info["backend"] = "wave"
            return info
    except (wave.Error, EOFError):
        # Float/extended WAV variants unsupported by stdlib wave can still be
        # decoded safely by libsndfile below.
        pass
    try:
        import soundfile as sf
    except ImportError:
        raise ValueError(
            "This audio format requires 'soundfile'. Install: pip install soundfile")
    info = sf.info(path)
    sample_rate = int(info.samplerate)
    channels = int(info.channels)
    frame_count = int(info.frames)
    if sample_rate <= 0 or sample_rate > 384000:
        raise ValueError(f"Unsupported meeting audio sample rate: {sample_rate} Hz.")
    if channels <= 0 or channels > 32:
        raise ValueError(f"Unsupported meeting audio channel count: {channels}.")
    return {
        "backend": "soundfile",
        "channels": channels,
        "sample_width": None,
        "sample_rate": sample_rate,
        "frame_count": frame_count,
        "duration_sec": frame_count / float(sample_rate),
    }


def _iter_source_audio(path, info):
    """Yield mono float32 blocks from a supported import backend."""
    import numpy as np
    source_rate = info["sample_rate"]
    # Bound by both time and frame count so unusual high-rate/many-channel files
    # cannot allocate an enormous decode block.
    block_frames = max(1, min(
        int(source_rate * NORMALIZE_BLOCK_SECONDS), 1_000_000))
    if info["backend"] == "wave":
        with wave.open(path, "rb") as wf:
            while True:
                raw = wf.readframes(block_frames)
                if not raw:
                    break
                yield _decode_pcm_frames(
                    raw, info["sample_width"], info["channels"])
        return

    import soundfile as sf
    with sf.SoundFile(path, "r") as source:
        while True:
            block = source.read(
                block_frames, dtype="float32", always_2d=True)
            if block.size == 0:
                break
            yield np.asarray(block, dtype=np.float32).mean(axis=1)


def _normalize_audio_file(path):
    """Stream an import into private 16 kHz mono PCM and return (name, seconds)."""
    import numpy as np
    info = _source_audio_info(path)
    declared_duration = float(info["duration_sec"])
    if declared_duration <= 0:
        raise ValueError("Recording contains no audio.")
    if declared_duration > MEETING_MAX_SECONDS:
        raise ValueError(
            f"Meeting audio exceeds the {MEETING_MAX_SECONDS // 3600}-hour limit.")

    filename, destination = _new_meeting_audio_path()
    frames_written = 0
    source_frames_seen = 0
    try:
        with wave.open(destination, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(SAMPLE_RATE)
            for block in _iter_source_audio(path, info):
                block = np.asarray(block, dtype=np.float32).reshape(-1)
                if not block.size:
                    continue
                block = np.nan_to_num(
                    block, nan=0.0, posinf=1.0, neginf=-1.0)
                source_frames_seen += len(block)
                if (info.get("frame_count", 0) > 0
                        and source_frames_seen > info["frame_count"]):
                    raise ValueError("Audio decoder returned more frames than declared.")
                target_total = _resampled_frame_total(
                    source_frames_seen, info["sample_rate"], SAMPLE_RATE)
                if target_total > MEETING_MAX_SAMPLES:
                    raise ValueError(
                        f"Meeting audio exceeds the {MEETING_MAX_SECONDS // 3600}-hour limit.")
                block = _resample_audio(
                    block, info["sample_rate"], SAMPLE_RATE,
                    output_length=target_total - frames_written)
                # Decoder metadata is authoritative for preflight, but enforce
                # the sample boundary again against malformed/streaming sources.
                remaining = MEETING_MAX_SAMPLES - frames_written
                if remaining <= 0:
                    raise ValueError(
                        f"Meeting audio exceeds the {MEETING_MAX_SECONDS // 3600}-hour limit.")
                if len(block) > remaining:
                    raise ValueError(
                        f"Meeting audio exceeds the {MEETING_MAX_SECONDS // 3600}-hour limit.")
                pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype("<i2")
                output.writeframesraw(pcm.tobytes())
                frames_written += len(pcm)
        if frames_written <= 0:
            raise ValueError("Recording contains no audio.")
        branding.protect_private_path(destination)
        return filename, frames_written / float(SAMPLE_RATE)
    except Exception:
        try:
            os.remove(destination)
        except OSError:
            pass
        raise


def _save_meeting_audio(audio, sample_rate=16000):
    """Write audio to a WAV file in the Mumble data dir. Returns a relative
    path string suitable for storage."""
    import wave

    filename, path = _new_meeting_audio_path()

    import numpy as np
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm.tobytes())

    branding.protect_private_path(path)

    return filename  # store just the filename, resolve relative to meetings_audio


def _resolve_audio_path(filename):
    """Resolve a stored relative audio filename to its full path."""
    path = meeting_store._safe_audio_path(filename)
    return path if path and os.path.isfile(path) else None


def _auto_meeting_title(duration_sec=0):
    from datetime import datetime
    now = datetime.now()
    m, s = divmod(int(duration_sec), 60)
    return f"Meeting — {now:%b %d, %Y} · {m} min"
