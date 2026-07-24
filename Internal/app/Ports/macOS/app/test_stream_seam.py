#!/usr/bin/env python3
"""The stream/tail SEAM test — no microphone, no model.

Drives the REAL Mumble._stream_worker with a stub object and a fake
transcriber. The audio is arange(N) so every sample's VALUE is its INDEX —
letting us assert exactly which samples the streamed chunks covered, and that
the tail slice `audio[_stream_processed_samples:]` tiles the recording with
no gap and no overlap (the unit-mismatch bug this guards against: the old
worker compared a callback-BLOCK count to a SAMPLE threshold and desynced the
seam on its skip/error paths).

Run: python test_stream_seam.py
"""

import threading
import time
import types

import numpy as np

from mumble_mac import SAMPLE_RATE, Mumble

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [ok  ] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


BLOCK = 1024  # samples per audio callback block (typical)


def make_obj(transcribe, results=None):
    """A minimal stand-in for Mumble carrying just what _stream_worker uses."""
    obj = types.SimpleNamespace(
        lock=threading.Lock(),
        frames=[],
        recording=True,
        _stream_done=threading.Event(),
        _stream_results=results if results is not None else [],
        _stream_processed_samples=0,
        _stream_session_id=1,
        _stream_idle=threading.Event(),
        _stream_inflight_samples=0,
        _local_transcribe=transcribe,
    )
    obj._stream_idle.set()
    return obj


def feed(obj, start, seconds):
    """Append `seconds` of index-valued audio as BLOCK-sized callback blocks
    (shape (n, 1), exactly like sounddevice's indata)."""
    n = int(SAMPLE_RATE * seconds)
    samples = np.arange(start, start + n, dtype=np.float32)
    for i in range(0, n, BLOCK):
        obj.frames.append(samples[i:i + BLOCK].reshape(-1, 1))
    return start + n


def run_worker(obj, prep, settle=2.5):
    """Run the real worker on a thread; `prep(obj)` feeds audio while it runs."""
    t = threading.Thread(target=Mumble._stream_worker, args=(obj,), daemon=True)
    obj._stream_worker_thread = t
    t.start()
    prep(obj)
    deadline = time.time() + settle
    while time.time() < deadline:
        time.sleep(0.1)
    obj._stream_done.set()
    t.join(timeout=3.0)
    return obj


print("== seam coverage: chunks + tail tile the recording exactly ==")
chunks = []


def fake_transcribe(audio_chunk, beam=1):
    chunks.append(np.asarray(audio_chunk).copy())
    return f"seg{len(chunks)}"


obj = make_obj(fake_transcribe)
total = feed(obj, 0, 7.0)  # 7s: above the 4s chunk threshold

run_worker(obj, lambda o: None)

check("worker actually fired (the old block-vs-sample bug kept it dormant)",
      len(chunks) >= 1)
covered = int(obj._stream_processed_samples)
check("counter equals EXACTLY the samples the transcriber received",
      covered == sum(len(c) for c in chunks))
check("streamed text recorded per chunk",
      obj._stream_results == [f"seg{i + 1}" for i in range(len(chunks))])

# Reconstruct the full recording exactly as _process does, then check the seam.
audio = np.concatenate(obj.frames, axis=0).flatten()
tail = audio[covered:] if covered < len(audio) else audio[:0]
stitched = np.concatenate([np.concatenate(chunks), tail]) if chunks else tail
check("chunks + tail == the whole recording (no gap, no overlap, in order)",
      len(stitched) == len(audio) and bool(np.array_equal(stitched, audio)))
check("tail starts at the exact seam index",
      len(tail) == 0 or tail[0] == covered)

print("\n== multi-chunk: coverage stays exact across several fires ==")
chunks.clear()
obj2 = make_obj(fake_transcribe)


def prep2(o):
    nxt = feed(o, 0, 6.0)     # first chunk
    time.sleep(1.0)           # let the worker consume it
    feed(o, nxt, 6.0)         # second chunk
    time.sleep(1.0)


run_worker(obj2, prep2, settle=1.0)
check("two+ chunks fired", len(chunks) >= 2)
audio2 = np.concatenate(obj2.frames, axis=0).flatten()
covered2 = int(obj2._stream_processed_samples)
tail2 = audio2[covered2:] if covered2 < len(audio2) else audio2[:0]
stitched2 = np.concatenate([np.concatenate(chunks), tail2])
check("multi-chunk stitch tiles the recording exactly",
      bool(np.array_equal(stitched2, audio2)))
check("chunks are contiguous (no overlap between consecutive chunks)",
      all(chunks[i + 1][0] == chunks[i][-1] + 1 for i in range(len(chunks) - 1)))

print("\n== short recording: below the threshold the worker never fires ==")
chunks.clear()
obj3 = make_obj(fake_transcribe)
feed(obj3, 0, 2.0)  # under the 4s chunk threshold
run_worker(obj3, lambda o: None, settle=1.0)
check("no chunks for a short clip", len(chunks) == 0)
check("seam stays at 0 (full pass handles everything)",
      obj3._stream_processed_samples == 0)

print("\n== error path: a failed chunk stops streaming, seam stays exact ==")
calls = {"n": 0}


def failing_transcribe(audio_chunk, beam=1):
    calls["n"] += 1
    raise RuntimeError("decode exploded")


obj4 = make_obj(failing_transcribe)
feed(obj4, 0, 7.0)
run_worker(obj4, lambda o: None, settle=1.0)
check("transcriber was attempted once then streaming stopped", calls["n"] == 1)
check("seam NOT advanced on error (tail covers the failed chunk)",
      obj4._stream_processed_samples == 0)
check("no phantom text from the failed chunk", obj4._stream_results == [])

print("\n== silence: empty text still advances the seam (covered audio) ==")
obj5 = make_obj(lambda a, beam=1: "   ")
n5 = feed(obj5, 0, 7.0)
run_worker(obj5, lambda o: None, settle=1.0)
check("silence advances the seam", obj5._stream_processed_samples > 0)
check("silence adds no text", obj5._stream_results == [])
audio5 = np.concatenate(obj5.frames, axis=0).flatten()
check("seam index never exceeds the recording length",
      obj5._stream_processed_samples <= len(audio5))

print()
if failed:
    print(f"{failed} FAILED, {passed} passed")
    raise SystemExit(1)
print(f"ALL PASS ({passed} checks)")
