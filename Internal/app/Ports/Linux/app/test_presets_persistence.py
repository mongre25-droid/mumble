"""Cross-process persistence regressions for Linux custom presets."""

import contextlib
import threading

import cloud_sync
import presets
import webui_shell


def _value(name):
    return {
        "title": name,
        "description": name + " description",
        "instruction": "Apply the " + name + " custom instruction.",
    }


def _use_temp_store(monkeypatch, tmp_path):
    path = tmp_path / "presets.json"
    monkeypatch.setattr(presets, "PRESETS_PATH", str(path))
    return path


def test_concurrent_disjoint_merges_preserve_every_slot(monkeypatch, tmp_path):
    _use_temp_store(monkeypatch, tmp_path)
    barrier = threading.Barrier(len(presets.CUSTOM_SLOTS))
    results = {}

    def writer(slot):
        barrier.wait()
        results[slot] = presets.merge_custom({slot: _value(str(slot))})

    threads = [threading.Thread(target=writer, args=(slot,))
               for slot in presets.CUSTOM_SLOTS]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert not any(thread.is_alive() for thread in threads)
    # A contended writer may honestly time out (especially the Windows test
    # fallback); every successful concurrent delta must survive, and a caller
    # can safely retry a reported failure without clobbering those successes.
    stored = presets.load_custom()
    assert set(stored) == {slot for slot, ok in results.items() if ok}
    for slot, ok in results.items():
        if not ok:
            assert presets.merge_custom({slot: _value(str(slot))})
    assert set(presets.load_custom()) == set(presets.CUSTOM_SLOTS)


def test_lock_and_write_failures_are_honest(monkeypatch, tmp_path):
    path = _use_temp_store(monkeypatch, tmp_path)
    slot = presets.CUSTOM_SLOTS[0]
    assert presets.save_custom({slot: _value("original")})
    original = path.read_bytes()

    @contextlib.contextmanager
    def unavailable_lock(_path):
        yield False

    with monkeypatch.context() as forced:
        forced.setattr(presets, "exclusive_file_lock", unavailable_lock)
        assert not presets.save_custom({slot: _value("lost")})
    assert path.read_bytes() == original

    with monkeypatch.context() as forced:
        forced.setattr(
            presets, "_atomic_write_custom", lambda _data: False)
        assert not presets.mutate_custom({slot: _value("also lost")})
    assert path.read_bytes() == original


def test_webui_stale_form_preserves_unseen_cloud_addition(monkeypatch, tmp_path):
    _use_temp_store(monkeypatch, tmp_path)
    local_slot, cloud_slot = presets.CUSTOM_SLOTS[:2]
    assert presets.save_custom({local_slot: _value("local old")})

    # Avoid Api.__init__: this focused call path does not need Settings or any
    # of the other live stores.
    api = webui_shell.Api.__new__(webui_shell.Api)
    api._preset_snapshot = None
    api.get_presets()

    assert presets.merge_custom({cloud_slot: _value("cloud")})
    rows = [dict({"slot": local_slot}, **_value("local edited"))]
    assert api.save_presets(rows)["ok"]
    # Saving the unchanged, still-open form a second time must also preserve
    # the cloud slot that this form has never rendered.
    assert api.save_presets(rows)["ok"]

    stored = presets.load_custom()
    assert stored[local_slot]["title"] == "local edited"
    assert stored[cloud_slot]["title"] == "cloud"


class _PresetPullHarness:
    def __init__(self, payload):
        self.payload = payload
        self._last_sync = {"presets": ""}
        self.synced = False
        self.error = ""

    def _is_signed_in(self):
        return True

    def _is_enabled(self, _kind):
        return True

    def _get_user_id(self):
        return "user-1"

    def _pull_single_row(self, _table, _uid):
        return self.payload, "2099-01-01T00:00:00Z"

    def _mark_synced(self, _kind):
        self.synced = True

    def _mark_error(self, _kind, message):
        self.error = message


def test_cloud_pull_uses_atomic_merge_and_reports_failure(monkeypatch, tmp_path):
    _use_temp_store(monkeypatch, tmp_path)
    local_slot, remote_slot = presets.CUSTOM_SLOTS[:2]
    assert presets.save_custom({local_slot: _value("local")})
    payload = {str(remote_slot): _value("remote")}

    harness = _PresetPullHarness(payload)
    result = cloud_sync.SyncManager.pull_presets(harness)
    assert result["ok"] and harness.synced
    assert set(presets.load_custom()) == {local_slot, remote_slot}

    harness = _PresetPullHarness(payload)
    monkeypatch.setattr(presets, "merge_custom", lambda *_args: False)
    result = cloud_sync.SyncManager.pull_presets(harness)
    assert not result["ok"]
    assert "busy or unwritable" in result["message"]
    assert harness.error


def test_cloud_push_rejects_corrupt_local_store(monkeypatch, tmp_path):
    path = _use_temp_store(monkeypatch, tmp_path)
    path.write_text("{not valid json", encoding="utf-8")
    harness = _PresetPullHarness({})

    result = cloud_sync.SyncManager.push_presets(harness)

    assert not result["ok"]
    assert "unreadable" in result["message"]
    assert harness.error


def test_full_sync_does_not_push_after_failed_pull():
    class Harness:
        pushed = False

        def _is_enabled(self, _kind):
            return True

        def pull_presets(self):
            return {"ok": False, "message": "disk full"}

        def push_presets(self):
            self.pushed = True
            return {"ok": True}

    harness = Harness()
    result = cloud_sync.SyncManager.sync_presets(harness)
    assert not result["ok"]
    assert result["push"]["skipped"]
    assert not harness.pushed
