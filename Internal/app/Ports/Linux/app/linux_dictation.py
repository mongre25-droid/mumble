"""Bounded live-capture owner for Linux durable logical dictation."""

from __future__ import annotations

import queue
import threading

import numpy as np

from dictation_session import DurableDictationSession


class DurableLinuxCapture:
    """Convert float callback blocks into immutable bounded PCM16 segments."""

    def __init__(self, root, *, sample_rate=16000, segment_seconds=30,
                 queue_blocks=4, session_id=None, on_pressure=None):
        self.sample_rate = int(sample_rate)
        self.segment_samples = self.sample_rate * max(1, int(segment_seconds))
        self.session = DurableDictationSession.create(
            root, session_id=session_id, sample_rate=self.sample_rate,
            channels=1, segment_max_samples=self.segment_samples)
        self._queue = queue.Queue(maxsize=max(1, int(queue_blocks)))
        self._pending = bytearray()
        self._error = None
        self._accepted_samples = 0
        self._sealed = False
        self._pressure_stop = False
        self._emergency = None
        self._on_pressure = on_pressure
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._writer, name="mumble-linux-durable-capture",
            daemon=True)
        self._thread.start()

    @property
    def accepted_samples(self):
        return self._accepted_samples

    def accept(self, block):
        pcm = np.rint(np.clip(np.asarray(block).reshape(-1), -1.0, 1.0)
                      * 32767.0).astype("<i2").tobytes()
        if not pcm:
            return 0
        accepted = 0
        segment_bytes = self.segment_samples * 2
        for offset in range(0, len(pcm), segment_bytes):
            payload = pcm[offset:offset + segment_bytes]
            count = len(payload) // 2
            with self._lock:
                if self._sealed or self._pressure_stop or self._error is not None:
                    break
                try:
                    self._queue.put_nowait(payload)
                except queue.Full:
                    # Reserve the callback that first observes pressure, then
                    # request Stop outside the real-time callback thread.
                    self._emergency = payload
                    self._pressure_stop = True
                    callback = self._on_pressure
                else:
                    callback = None
            accepted += count
            self._accepted_samples += count
            if callback is not None:
                threading.Thread(target=callback,
                                 name="mumble-linux-storage-stop",
                                 daemon=True).start()
                break
        return accepted

    def _append_pending(self, *, final=False):
        segment_bytes = self.segment_samples * 2
        while len(self._pending) >= segment_bytes or (final and self._pending):
            size = segment_bytes if len(self._pending) >= segment_bytes else len(self._pending)
            payload = bytes(self._pending[:size])
            self.session.append_pcm16(payload)
            del self._pending[:size]

    def _writer(self):
        try:
            while True:
                payload = self._queue.get()
                try:
                    if payload is None:
                        break
                    self._pending.extend(payload)
                    self._append_pending()
                finally:
                    self._queue.task_done()
            self._append_pending(final=True)
        except Exception as exc:
            self._error = exc

    def finish(self, timeout=25.0):
        with self._lock:
            if self._sealed:
                return self.session.verify()
            self._sealed = True
            emergency = self._emergency
            self._emergency = None
        if emergency is not None:
            self._queue.put(emergency)
        self._queue.put(None)
        self._thread.join(timeout=max(0.1, float(timeout)))
        if self._thread.is_alive():
            raise RuntimeError("durable_capture_drain_timeout")
        if self._error is not None:
            raise RuntimeError("durable_capture_write_failed") from self._error
        return self.session.verify()

    def discard_empty(self):
        manifest = self.finish()
        if manifest.get("next_sample"):
            return False
        self.session.discard_unfinalized()
        return True

    def iter_audio(self):
        manifest = self.session.verify()
        for segment in manifest["segments"]:
            payload = (self.session.path / segment["filename"]).read_bytes()
            yield np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32767.0
