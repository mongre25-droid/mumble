"""Issue #26 macOS-native shared-contract regressions.

These tests use injected fake native backends so they are safe on the Windows
source-review host.  They prove contract behaviour, not physical macOS use.
"""

from pathlib import Path
import inspect
import queue
import subprocess
import threading
import time

from insertion import (
    ClipboardRestoreState,
    ClipboardWriteFailure,
    ImagePayload,
    InsertionRequest,
    NativeAcceptance,
    RichPayload,
    TargetEditability,
    TextPayload,
)
from macos_insertion import (
    MacClipboardAdapter, MacNativeInputAdapter, MacTargetAdapter, _fingerprint,
)
from macos_permissions import MacPermissionAuthority, PermissionState
from experimental.system_search.macos_spotlight import SpotlightSearchProvider
from experimental.system_search.macos_drag import MacNativeDragAdapter
from experimental.system_search.engine import SystemSearchEngine
from dictation_session import DurableDictationSession
from mumble_mac import Mumble
import mumble_mac as mumble_mac_module


class FakePasteboard:
    def __init__(self):
        self.change_count = 7
        self.formats = {
            "public.utf8-plain-text": b"before",
            "public.rtf": b"{\\rtf1 before}",
        }

    def read_formats(self):
        return dict(self.formats)

    def replace_formats(self, values):
        self.formats = dict(values)
        self.change_count += 1
        return self.change_count


def _request(payload):
    values = {
        "content_kind": "text",
        "text": "",
        "image_path": "",
        "rich_html": "",
        "rich_rtf": "",
    }
    if isinstance(payload, ImagePayload):
        values.update(content_kind="image", image_path=payload.path)
    elif isinstance(payload, RichPayload):
        values.update(
            content_kind="rich", text=payload.text,
            rich_html=payload.html, rich_rtf=payload.rtf,
        )
    else:
        values.update(text=payload.text)
    return InsertionRequest(
        operation_id="a" * 32,
        source="issue26-test",
        activation_target=None,
        restore_clipboard=True,
        **values,
    )


def test_pasteboard_preserves_formats_and_never_overwrites_newer_content():
    backend = FakePasteboard()
    adapter = MacClipboardAdapter(backend=backend)
    snapshot = adapter.snapshot()
    assert dict((name, data) for _key, name, data in snapshot.formats) == {
        "public.utf8-plain-text": b"before",
        "public.rtf": b"{\\rtf1 before}",
    }

    ownership = adapter.write(_request(RichPayload(
        text="after", html="<b>after</b>", rtf="{\\rtf1 after}"
    )), snapshot)
    assert backend.formats == {
        "public.utf8-plain-text": b"after",
        "public.html": b"<b>after</b>",
        "public.rtf": b"{\\rtf1 after}",
    }
    assert adapter.restore(snapshot, ownership).state is ClipboardRestoreState.RESTORED
    assert backend.formats["public.rtf"] == b"{\\rtf1 before}"

    ownership = adapter.write(_request(TextPayload("Mumble")), adapter.snapshot())
    backend.replace_formats({"public.utf8-plain-text": b"newer user copy"})
    assert adapter.restore(snapshot, ownership).state is ClipboardRestoreState.NEWER_EXTERNAL
    assert backend.formats["public.utf8-plain-text"] == b"newer user copy"


def test_partial_pasteboard_write_rolls_back_only_while_mumble_owns_it():
    class PartialFailure(FakePasteboard):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def replace_formats(self, values):
            if self.fail_once:
                self.fail_once = False
                name, payload = next(iter(values.items()))
                self.formats = {name: payload}
                self.change_count += 1
                error = OSError("partial pasteboard write")
                error.owned_sequence = self.change_count
                error.partial_fingerprint = _fingerprint(self.formats)
                raise error
            return super().replace_formats(values)

    backend = PartialFailure()
    adapter = MacClipboardAdapter(backend=backend)
    snapshot = adapter.snapshot()
    try:
        adapter.write(_request(RichPayload(
            text="after", html="<b>after</b>", rtf="{\\rtf1 after}"
        )), snapshot)
        raise AssertionError("partial write should fail")
    except ClipboardWriteFailure as error:
        assert error.clipboard_restored is True
        assert error.clipboard_changed_externally is False
    assert backend.formats == {
        "public.utf8-plain-text": b"before",
        "public.rtf": b"{\\rtf1 before}",
    }


def test_pasteboard_snapshot_and_partial_rollback_detect_external_races():
    class SnapshotRace(FakePasteboard):
        def read_formats(self):
            result = super().read_formats()
            self.change_count += 1
            return result

    try:
        MacClipboardAdapter(backend=SnapshotRace()).snapshot()
        raise AssertionError("mixed pasteboard snapshot should fail")
    except RuntimeError as error:
        assert str(error) == "pasteboard_changed_during_snapshot"

    class ExternalRace(FakePasteboard):
        def replace_formats(self, values):
            name, payload = next(iter(values.items()))
            self.formats = {name: payload}
            self.change_count += 1
            error = OSError("partial pasteboard write")
            error.owned_sequence = self.change_count
            error.partial_fingerprint = _fingerprint(self.formats)
            self.change_count += 1
            raise error

    backend = ExternalRace()
    adapter = MacClipboardAdapter(backend=backend)
    snapshot = adapter.snapshot()
    try:
        adapter.write(_request(TextPayload("Mumble")), snapshot)
        raise AssertionError("raced partial write should fail")
    except ClipboardWriteFailure as error:
        assert error.clipboard_restored is False
        assert error.clipboard_changed_externally is True
    assert backend.formats == {"public.utf8-plain-text": b"Mumble"}


def test_native_input_reports_submission_without_inventing_confirmation():
    posted = []
    native = MacNativeInputAdapter(
        modifier_probe=lambda: set(),
        post_chord=lambda key: posted.append(key) or 4,
        post_unicode=lambda text: len(text),
    )
    assert native.ready(0.01) == (True, "")
    result = native.send_paste()
    assert result == NativeAcceptance(
        requested=4, accepted=4, submitted=True, confirmation=None
    )
    assert posted == ["v"]
    unknown = MacNativeInputAdapter(
        modifier_probe=lambda: set(), post_chord=lambda _key: True,
    ).send_paste()
    assert unknown.submitted is True
    assert unknown.accepted is None
    assert unknown.confirmation is None


def test_accessibility_target_keeps_process_identity_and_read_only_truth_separate():
    backend = type("TargetBackend", (), {
        "capture": staticmethod(lambda: {
            "pid": 42,
            "process_created": 123_456,
            "focused": 99,
            "role": "AXStaticText",
            "editable": False,
            "read_only": False,
            "protected": False,
        }),
    })()
    target = MacTargetAdapter(
        backend=backend, permission_probe=lambda: True
    ).current()
    assert target.process_creation_id == 123_456
    assert target.editability is TargetEditability.NOT_EDITABLE
    assert target.read_only is False


def test_same_process_same_role_different_fields_are_distinct_targets():
    values = iter((101, 202))
    backend = type("TargetBackend", (), {
        "capture": lambda _self: {
            "pid": 42, "process_created": 123_456,
            "focused": next(values), "role": "AXTextField",
            "editable": True, "read_only": False, "protected": False,
        },
    })()
    adapter = MacTargetAdapter(backend=backend, permission_probe=lambda: True)
    first = adapter.current()
    second = adapter.current()
    assert first.same_destination(second) is False
    native_source = (
        Path(__file__).parents[3] / "macos_insertion.py"
    ).read_text(encoding="utf-8")
    assert "CFHash(focused)" in native_source
    missing = type("TargetBackend", (), {
        "capture": staticmethod(lambda: {
            "pid": 42, "process_created": 123_456, "focused": 0,
            "role": "AXTextField", "editable": "unknown",
        }),
    })()
    assert MacTargetAdapter(
        backend=missing, permission_probe=lambda: True
    ).current() is None


def test_permission_authority_distinguishes_denied_revoked_and_recovery():
    values = {"microphone": False, "accessibility": False,
              "input_monitoring": None}
    authority = MacPermissionAuthority(
        probes={name: (lambda key=name: values[key]) for name in values}
    )
    first = authority.refresh()
    assert first.microphone is PermissionState.DENIED
    assert first.accessibility is PermissionState.DENIED
    assert first.input_monitoring is PermissionState.UNKNOWN

    values.update(microphone=True, accessibility=True, input_monitoring=True)
    ready = authority.refresh()
    assert ready.all_ready

    values["accessibility"] = False
    revoked = authority.refresh()
    assert revoked.accessibility is PermissionState.REVOKED
    assert revoked.recoverable == ("accessibility",)

    values["accessibility"] = True
    assert authority.refresh().all_ready


def test_hotkey_registration_is_transactional_and_retryable(monkeypatch):
    controller = type("BindingController", (), {})()
    controller._input_bindings_lock = threading.Lock()
    controller._input_bindings_registered = False
    for attr in ("_hk_main", "_hk_quick", "_hk_history",
                 "_hk_search", "_hk_web_search"):
        setattr(controller, attr, None)
    released = []
    monkeypatch.setattr(
        mumble_mac_module.bindings, "unregister",
        lambda handle: released.append(handle) or True,
    )
    controller._unregister_input_bindings = lambda: (
        Mumble._unregister_input_bindings(controller)
    )
    controller._register_hotkey = lambda: setattr(controller, "_hk_main", "main")
    controller._register_quick = lambda: setattr(controller, "_hk_quick", "quick")
    controller._register_history = lambda: (_ for _ in ()).throw(
        RuntimeError("transient listener failure"))
    controller._register_search = lambda: setattr(controller, "_hk_search", "find")
    controller._register_web_search = lambda: setattr(
        controller, "_hk_web_search", "web")
    assert Mumble._register_input_bindings(controller) is False
    assert controller._input_bindings_registered is False
    assert all(getattr(controller, attr) is None for attr in (
        "_hk_main", "_hk_quick", "_hk_history", "_hk_search", "_hk_web_search"))
    assert set(released) == {"main", "quick", "find", "web"}

    controller._register_history = lambda: setattr(
        controller, "_hk_history", "history")
    assert Mumble._register_input_bindings(controller) is True
    assert controller._input_bindings_registered is True
    watcher_source = inspect.getsource(
        Mumble._wait_for_accessibility_and_register)
    assert "registered = self._register_input_bindings()" in watcher_source
    assert "if registered and" in watcher_source


class FakeProcess:
    def __init__(self, rows, delay=0.0):
        self.rows = rows
        self.delay = delay
        self.started = time.monotonic()
        self.terminated = False
        self.returncode = None

    def poll(self):
        if self.terminated:
            self.returncode = -15
        elif time.monotonic() - self.started >= self.delay:
            self.returncode = 0
        return self.returncode

    def communicate(self, timeout=None):
        if self.poll() is None:
            raise subprocess.TimeoutExpired("mdfind", timeout)
        return (b"\0".join(str(row).encode() for row in self.rows) + b"\0", b"")

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        del timeout
        self.returncode = -15
        return self.returncode

    def kill(self):
        self.terminated = True


class Cancellation:
    cancelled = False


def test_spotlight_results_are_bounded_contained_and_cancellable(tmp_path):
    inside = tmp_path / "Report.pdf"
    inside.write_bytes(b"pdf")
    outside = tmp_path.parent / "private.txt"
    processes = []

    def spawn(_args):
        process = FakeProcess([inside, outside, inside])
        processes.append(process)
        return process

    provider = SpotlightSearchProvider(home=tmp_path, process_factory=spawn)
    assert provider.status()["available"]
    result = provider.query(
        "report", limit=1, deadline=time.monotonic() + 1,
        cancellation=Cancellation(), generation=9,
    )
    assert result["state"] == "complete"
    assert [row["target"] for row in result["items"]] == [str(inside)]
    assert result["items"][0]["source"] == "spotlight"

    slow = FakeProcess([inside], delay=10)
    provider = SpotlightSearchProvider(
        home=tmp_path, process_factory=lambda _args: slow,
    )
    token = Cancellation()
    token.cancelled = True
    cancelled = provider.query(
        "report", limit=5, deadline=time.monotonic() + 1,
        cancellation=token, generation=10,
    )
    assert cancelled["state"] == "cancelled"
    assert slow.terminated


def test_mumble_find_declares_macos_support_and_keeps_file_work_bounded(tmp_path):
    provider = SpotlightSearchProvider(
        home=tmp_path,
        process_factory=lambda _args: FakeProcess([]),
    )
    engine = SystemSearchEngine(
        platform="macos",
        home=tmp_path,
        data_dir=tmp_path / "state",
        file_provider=provider,
        start_background=False,
    )
    try:
        status = engine.status()
        assert status["supported"] is True
        assert status["platform"] == "macos"
        assert status["hotkey"] == "ctrl+option+f"
        pressure = status["provider_work"]
        assert pressure["capacity"] >= pressure["owned"]
    finally:
        engine.close()


def test_native_drag_consumes_only_the_exact_fresh_appkit_gesture(tmp_path):
    target = tmp_path / "Report.pdf"
    target.write_bytes(b"pdf")
    now = [10.0]
    calls = []
    adapter = MacNativeDragAdapter(
        clock=lambda: now[0], max_event_age=0.5,
        begin_drag=lambda *args: calls.append(args) or object(),
    )
    view, event, source = object(), object(), object()
    adapter.arm(view, event, source)
    result = adapter.start(target)
    assert result["ok"] is True
    assert calls == [(target.resolve(), view, event, source)]
    assert adapter.start(target)["ok"] is False

    adapter.arm(view, event, source)
    now[0] += 0.6
    assert adapter.start(target)["ok"] is False
    assert len(calls) == 1


def test_durable_queue_pressure_preserves_the_boundary_callback_once():
    controller = type("DurableController", (), {})()
    controller._dictation_session = object()
    controller._dictation_capture_stop_requested = False
    controller._dictation_capture_error = None
    controller._dictation_capture_queue = queue.Queue(maxsize=1)
    controller._dictation_capture_queue.put((b"before", 3))
    controller._dictation_buffer_lock = threading.Lock()
    controller._dictation_queued_samples = 3
    controller._dictation_emergency_payload = None
    controller._dictation_durable_samples = 3
    stops = []
    controller._request_storage_guard_stop = lambda: stops.append(True)

    accepted = Mumble._append_durable_audio(controller, [[0.25], [-0.25]])
    assert accepted == 2
    assert controller._dictation_emergency_payload[1] == 2
    assert controller._dictation_durable_samples == 5
    assert stops == [True]
    assert Mumble._append_durable_audio(controller, [[0.5]]) == 0
    assert stops == [True]


def test_storage_guard_waits_for_startup_owner_then_stops_once():
    controller = type("PressureController", (), {})()
    controller._notify = lambda *_args: None
    controller._shutting_down = threading.Event()
    controller.lock = threading.Lock()
    controller.recording = True
    controller.busy = True
    stopped = []
    controller.stop_recording = lambda: (
        stopped.append(True), setattr(controller, "recording", False)
    )
    worker = threading.Thread(
        target=Mumble._stop_after_durable_pressure, args=(controller,)
    )
    worker.start()
    time.sleep(0.05)
    controller.busy = False
    worker.join(timeout=1.0)
    assert stopped == [True]
    assert worker.is_alive() is False


def test_stop_binds_destination_before_durable_drain_or_transcription():
    source = inspect.getsource(Mumble.stop_recording)
    begin = source.index("self._insertion_module.begin")
    assert begin < source.index("self._seal_durable_dictation")
    assert begin < source.index("self._transcribe_durable_dictation")
    shutdown = inspect.getsource(Mumble._quit)
    assert "self._dictation_session is not None" in shutdown
    assert shutdown.index("self._seal_durable_dictation") < shutdown.index("os._exit")
    seal = inspect.getsource(Mumble._seal_durable_dictation)
    assert "reconcile_unresolved_pcm16" in seal


def test_product_ui_surfaces_permission_recovery_and_separate_find_start():
    root = Path(__file__).parent
    html = (root / "webui" / "index.html").read_text(encoding="utf-8")
    javascript = (root / "webui" / "app.js").read_text(encoding="utf-8")
    shell = (root / "webui_shell.py").read_text(encoding="utf-8")
    assert 'id="macos-permissions"' in html
    assert html.count("data-macos-permission-request=") == 3
    assert 'call("get_macos_permissions")' in javascript
    assert 'call("open_macos_permission_settings"' in javascript
    assert 'start_mode == "find"' in shell
    find_branch = shell.split('if start_mode == "find":', 1)[1].split("else:", 1)[0]
    assert "_make_main" not in find_branch
    main_window = shell.split("def _make_main", 1)[1].split(
        "def _ensure_main_front", 1)[0]
    assert "a._show_system_search = search_lifecycle.show" in main_window
    assert "a._hide_system_search = search_lifecycle.hide" in main_window
    assert "a._toggle_system_search = search_lifecycle.toggle" in main_window
    launcher = inspect.getsource(Mumble._open_web_ui)
    live_process = launcher.split(
        "if proc is not None and proc.poll() is None:", 1
    )[1].split("return True", 1)[0]
    assert 'if str(start_mode).lower() != "find":' in live_process


def test_interrupted_dictation_recovers_to_history_without_reinsertion(tmp_path):
    session = DurableDictationSession.create(
        tmp_path,
        session_id="d" * 32,
        sample_rate=16_000,
        channels=1,
        segment_max_samples=16_000,
    )
    session.append_pcm16(b"\0\0" * 800)

    class History:
        def __init__(self):
            self.rows = {}

        def find_record(self, record_id):
            return self.rows.get(record_id)

        def add(self, text, mode, duration, record_id=None):
            row = {"text": text, "mode": mode, "duration": duration,
                   "record_id": record_id}
            self.rows[record_id] = row
            return row

    controller = type("RecoveryController", (), {})()
    controller._dictation_session_root = str(tmp_path)
    controller._dictation_finalizer_id = "f" * 32
    controller.history = History()
    controller._transcribe_durable_session = lambda _session: (
        "recovered words", 800
    )

    first = Mumble.recover_durable_dictations(controller)
    second = Mumble.recover_durable_dictations(controller)
    finalization = session.read_manifest()["finalization"]
    assert first == {"recovered": 1, "errors": 0}
    assert second == {"recovered": 0, "errors": 0}
    assert controller.history.rows[session.session_id]["text"] == "recovered words"
    assert finalization["state"] == "complete"
    assert finalization["insertion_outcome"] == "saved_only"


def test_multisegment_dictation_reconciles_timestamped_overlap(tmp_path):
    first = tmp_path / "first.pcm"
    second = tmp_path / "second.pcm"
    first.write_bytes(b"\0\0" * 4)
    second.write_bytes(b"\0\0" * 4)
    manifest = {
        "audio": {"sample_rate": 4}, "next_sample": 8,
        "segments": [
            {"filename": first.name, "sample_count": 4},
            {"filename": second.name, "sample_count": 4},
        ],
    }
    session = type("Session", (), {
        "path": tmp_path,
        "verify": lambda _self: manifest,
    })()
    calls = []
    responses = iter((
        ("old repeat", [
            {"word": "old", "start": 0.0, "end": 0.2},
            {"word": "repeat", "start": 0.8, "end": 1.0},
        ]),
        ("repeat corrected end", [
            {"word": "repeat", "start": 0.0, "end": 0.2},
            {"word": "corrected", "start": 0.6, "end": 0.8},
            {"word": "end", "start": 1.2, "end": 1.4},
        ]),
    ))
    controller = type("TranscriptController", (), {})()
    controller._dictation_inference_overlap_samples = 2
    controller._reconcile_timestamped_segment = (
        Mumble._reconcile_timestamped_segment
    )
    controller._merge_durable_text = Mumble._merge_durable_text
    controller._transcribe = lambda audio, want_words=False: (
        calls.append((len(audio), want_words)), next(responses)
    )[1]
    text, samples = Mumble._transcribe_durable_session(controller, session)
    assert calls == [(4, True), (6, True)]
    assert text == "old repeat corrected end"
    assert samples == 8

    for responses, expected in ((
        iter((("first words", []), ("second words", [
            {"word": "second", "start": 0.6, "end": 0.8},
            {"word": "words", "start": 1.2, "end": 1.4},
        ]))),
        "first words second words",
    ), (
        iter((("first words", [
            {"word": "first", "start": 0.0, "end": 0.2},
            {"word": "words", "start": 0.8, "end": 1.0},
        ]), ("second words", []))),
        "first words second words",
    )):
        controller._transcribe = lambda _audio, want_words=False: next(responses)
        mixed, _samples = Mumble._transcribe_durable_session(
            controller, session)
        assert mixed == expected


def test_supported_macos_architecture_seams_are_explicit():
    import importlib.util
    verifier = Path(__file__).parents[1] / "verify_architecture.py"
    spec = importlib.util.spec_from_file_location("verify_architecture", verifier)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.verify("arm64", 64)[0]
    assert module.verify("x86_64", 64)[0]
    assert not module.verify("i386", 32)[0]
