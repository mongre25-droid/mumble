"""Fixed process boundary for non-cooperative native Windows Search work."""

from __future__ import annotations

from concurrent.futures import Future, InvalidStateError
import multiprocessing
import threading
import time

from .windows_search import WindowsSearchProvider


PROCESS_PROVIDER_CAPACITY = 2


class _DeadlineCancellation:
    """Child-process cancellation view without pretending ADO can be interrupted."""

    def __init__(self, deadline):
        self._deadline = float(deadline)

    @property
    def cancelled(self):
        return time.monotonic() >= self._deadline


def _windows_search_process_main(connection):
    """Serve serial native queries in one reusable, terminable child process."""
    provider = WindowsSearchProvider()
    try:
        while True:
            request = connection.recv()
            if request.get("kind") == "stop":
                return
            request_id = int(request["request_id"])
            timeout = max(0.0, float(request.get("timeout") or 0.0))
            deadline = time.monotonic() + timeout
            try:
                result = provider.query(
                    request.get("query") or "",
                    limit=request.get("limit") or 1,
                    deadline=deadline,
                    cancellation=_DeadlineCancellation(deadline),
                    generation=request.get("generation") or 0,
                )
                connection.send({
                    "request_id": request_id,
                    "ok": True,
                    "result": result,
                })
            except BaseException as exc:
                connection.send({
                    "request_id": request_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:160]}",
                })
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        try:
            connection.close()
        except Exception:
            pass


class WindowsSearchProcessProvider:
    """One fixed two-process native-provider boundary for the application process.

    A timed-out ADO call remains owned by its existing worker. New queries fail
    closed to the engine's usable app-only result path until a worker completes.
    Final application shutdown terminates and joins the fixed children, so a
    native call that ignores cancellation cannot keep Python alive indefinitely.
    """

    name = "Windows Search"

    def __init__(self, capacity=PROCESS_PROVIDER_CAPACITY, worker_target=None):
        self.capacity = max(1, min(PROCESS_PROVIDER_CAPACITY, int(capacity)))
        self._worker_target = worker_target or _windows_search_process_main
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.RLock()
        self._slots = []
        self._next_request_id = 1
        self._closing = False
        self._last_error = ""

    def status(self):
        with self._lock:
            return {
                "available": not self._closing and not bool(self._last_error),
                "state": "error" if self._last_error else (
                    "closed" if self._closing else "ready"
                ),
                "name": self.name,
                "message": self._last_error,
            }

    def work_status(self):
        with self._lock:
            slots = list(self._slots)
            closing = self._closing
        processes = sum(
            bool(slot["process"] and slot["process"].is_alive())
            for slot in slots
        )
        threads = sum(
            bool(slot["thread"] and slot["thread"].is_alive())
            for slot in slots
        )
        owned = sum(slot["future"] is not None for slot in slots)
        return {
            "capacity": self.capacity,
            "executors": 0 if closing else 1,
            "processes": processes,
            "threads": threads,
            "owned": owned,
            "running": owned,
            "pending": 0,
            "ownership_entries": owned,
        }

    def _start_locked(self):
        if self._slots or self._closing:
            return
        started = []
        try:
            for index in range(self.capacity):
                parent_connection, child_connection = self._context.Pipe()
                process = self._context.Process(
                    target=self._worker_target,
                    args=(child_connection,),
                    name=f"mumble-find-provider-{index + 1}",
                    daemon=True,
                )
                slot = {
                    "connection": parent_connection,
                    "process": process,
                    "thread": None,
                    "future": None,
                    "request_id": None,
                    "generation": None,
                }
                process.start()
                child_connection.close()
                monitor = threading.Thread(
                    target=self._monitor,
                    args=(slot,),
                    name=f"mumble-find-provider-monitor-{index + 1}",
                    daemon=True,
                )
                slot["thread"] = monitor
                self._slots.append(slot)
                started.append(slot)
                monitor.start()
        except Exception:
            self._last_error = "Windows Search worker processes could not start."
            self._terminate_slots(started)
            self._slots.clear()
            raise

    def _monitor(self, slot):
        try:
            while True:
                response = slot["connection"].recv()
                request_id = int(response.get("request_id") or 0)
                with self._lock:
                    if request_id != slot["request_id"]:
                        continue
                    future = slot["future"]
                    slot["future"] = None
                    slot["request_id"] = None
                    slot["generation"] = None
                if future is None or future.done():
                    continue
                if response.get("ok"):
                    result = response.get("result")
                    with self._lock:
                        if isinstance(result, dict) and result.get("state") == "error":
                            self._last_error = str(result.get("message") or "")[:160]
                        elif isinstance(result, dict):
                            self._last_error = ""
                    try:
                        future.set_result(result)
                    except InvalidStateError:
                        pass
                else:
                    error = str(response.get("error") or "provider worker failed")
                    with self._lock:
                        self._last_error = error[:160]
                    try:
                        future.set_exception(RuntimeError(error))
                    except InvalidStateError:
                        pass
        except (EOFError, BrokenPipeError, OSError):
            pass
        finally:
            with self._lock:
                future = slot["future"]
                slot["future"] = None
                slot["request_id"] = None
                slot["generation"] = None
                closing = self._closing
                if not closing:
                    self._last_error = "Windows Search worker exited unexpectedly."
            if future is not None and not future.done():
                try:
                    future.set_exception(RuntimeError(
                        "Windows Search worker stopped before completing the query."
                    ))
                except InvalidStateError:
                    pass

    def submit(self, query, *, limit, deadline, cancellation, generation):
        del cancellation  # Native running work remains owned until completion.
        with self._lock:
            if self._closing:
                raise RuntimeError("Windows Search provider is closed.")
            self._start_locked()
            slot = next((
                item for item in self._slots
                if item["future"] is None and item["process"].is_alive()
            ), None)
            if slot is None:
                raise RuntimeError("Windows Search provider capacity is occupied.")
            request_id = self._next_request_id
            self._next_request_id += 1
            future = Future()
            future.set_running_or_notify_cancel()
            slot["future"] = future
            slot["request_id"] = request_id
            slot["generation"] = int(generation)
            request = {
                "kind": "query",
                "request_id": request_id,
                "query": str(query or ""),
                "limit": int(limit),
                "timeout": max(0.0, float(deadline) - time.monotonic()),
                "generation": int(generation),
            }
            try:
                slot["connection"].send(request)
            except Exception:
                slot["future"] = None
                slot["request_id"] = None
                slot["generation"] = None
                future.set_exception(RuntimeError(
                    "Windows Search worker could not receive the query."
                ))
                raise
            return future

    def cancel(self, generation):
        """Report ownership only; a running native call cannot be cancelled safely."""
        with self._lock:
            return any(
                slot["generation"] == int(generation)
                and slot["future"] is not None
                for slot in self._slots
            )

    @staticmethod
    def _terminate_slots(slots):
        for slot in slots:
            process = slot.get("process")
            if process is not None and process.is_alive():
                process.terminate()
        for slot in slots:
            process = slot.get("process")
            if process is None:
                continue
            process.join(0.5)
            if process.is_alive():
                try:
                    process.kill()
                except AttributeError:
                    process.terminate()
                process.join(0.5)

    def shutdown(self):
        with self._lock:
            if self._closing:
                return
            self._closing = True
            slots = list(self._slots)
            futures = [
                slot["future"] for slot in slots if slot["future"] is not None
            ]
        for slot in slots:
            if slot["future"] is None:
                try:
                    slot["connection"].send({"kind": "stop"})
                except Exception:
                    pass
        self._terminate_slots(slots)
        for slot in slots:
            try:
                slot["connection"].close()
            except Exception:
                pass
        for future in futures:
            if not future.done():
                try:
                    future.set_exception(RuntimeError(
                        "Windows Search stopped during application shutdown."
                    ))
                except InvalidStateError:
                    pass
        for slot in slots:
            monitor = slot.get("thread")
            if monitor is not None and monitor is not threading.current_thread():
                monitor.join(0.5)
        with self._lock:
            for slot in self._slots:
                slot["future"] = None
                slot["request_id"] = None
                slot["generation"] = None
