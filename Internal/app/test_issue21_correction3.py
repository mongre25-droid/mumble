"""Focused regressions for the third Issue #21 correction."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


APP_DIR = Path(__file__).resolve().parent
PLATFORM_APP_DIRS = (
    APP_DIR,
    APP_DIR / "Ports" / "macOS" / "app",
    APP_DIR / "Ports" / "Linux" / "app",
)
PORT_TESTS = (
    APP_DIR / "Ports" / "macOS" / "app" / "test_webui_api.py",
    APP_DIR / "Ports" / "Linux" / "app" / "test_webui_api.py",
)


@pytest.mark.parametrize("test_path", PORT_TESTS, ids=("macos", "linux"))
def test_port_webui_api_owns_offline_temporary_storage_before_bridge_import(
    test_path,
):
    """Importing either test must never expose the user's normal data path."""
    probe = r'''
import json
import os
from pathlib import Path
import runpy
import sys
import types

sys.modules["webui_shell"] = types.ModuleType("webui_shell")
runpy.run_path(sys.argv[1], run_name="issue21_storage_probe")
print(json.dumps({
    "path": os.environ.get("MUMBLE_TEST_DATA_DIR", ""),
    "offline": os.environ.get("MUMBLE_OFFLINE_TESTS", ""),
    "exists_during_test": Path(os.environ.get("MUMBLE_TEST_DATA_DIR", "")).is_dir(),
}))
'''
    env = dict(os.environ)
    env.pop("MUMBLE_TEST_DATA_DIR", None)
    env.pop("MUMBLE_OFFLINE_TESTS", None)
    result = subprocess.run(
        [sys.executable, "-c", probe, str(test_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["offline"] == "1"
    assert evidence["exists_during_test"] is True
    assert evidence["path"]
    assert not Path(evidence["path"]).exists()


@pytest.mark.parametrize(
    "platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux")
)
def test_model_confirmation_requires_affirmative_live_controller_ack(platform_app):
    script = r'''
import json
import os
from pathlib import Path
import sys
import tempfile

owned = tempfile.TemporaryDirectory(prefix="issue21_ack_")
os.environ["MUMBLE_TEST_DATA_DIR"] = owned.name
os.environ["MUMBLE_OFFLINE_TESTS"] = "1"
sys.path.insert(0, sys.argv[1])
import processing_route
import webui_shell

host = webui_shell.Api()
host.settings.update(
    pro_mode=True,
    local_only_mode=False,
    instant_text=False,
    llm_provider="cerebras",
    cerebras_api_key="ack-required-key",
    cerebras_model="ack-required-model",
)
webui_shell.ai.fetch_models = lambda _provider, _key: ["ack-required-model"]
webui_shell._ctrl_send = lambda *_args, **_kwargs: None
result = host.list_models("cerebras")
controller_settings = type(host.settings)()
route = processing_route.snapshot(
    controller_settings, feature="deck", lane="deck_reason"
)

print(json.dumps({
    "result": result,
    "effective": route.effective_route,
    "reason": route.reason,
}))
owned.cleanup()
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["result"]["ok"] is False
    assert evidence["effective"] == "local"
    assert evidence["reason"] == "unconfirmed_model"


@pytest.mark.parametrize(
    "platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux")
)
def test_durable_generation_rejects_reversed_completion_across_hosts(platform_app):
    script = r'''
import json
import os
import sys
import tempfile
import threading

owned = tempfile.TemporaryDirectory(prefix="issue21_generation_")
os.environ["MUMBLE_TEST_DATA_DIR"] = owned.name
os.environ["MUMBLE_OFFLINE_TESTS"] = "1"
sys.path.insert(0, sys.argv[1])
import processing_route
import webui_shell

host_a = webui_shell.Api()
host_a.settings.update(
    pro_mode=True, local_only_mode=False, instant_text=False,
    llm_provider="cerebras", cerebras_api_key="old-host-key",
    cerebras_model="old-host-model",
)
host_b = webui_shell.Api()
controller_settings = type(host_a.settings)()
def acknowledge(_message, timeout=0.8):
    controller_settings.load()
    return {"ok": True}
webui_shell._ctrl_send = acknowledge

old_started = threading.Event()
release_old = threading.Event()
def fetch(_provider, key):
    if key == "old-host-key":
        old_started.set()
        release_old.wait(timeout=5)
        return ["old-host-model"]
    return ["new-host-model"]
webui_shell.ai.fetch_models = fetch

old_result = {}
worker = threading.Thread(
    target=lambda: old_result.update(host_a.list_models("cerebras"))
)
worker.start()
old_started.wait(timeout=5)
host_b.settings.update(
    cerebras_api_key="new-host-key", cerebras_model="new-host-model"
)
new_result = host_b.list_models("cerebras")
release_old.set()
worker.join(timeout=5)
controller_settings.load()
route = processing_route.snapshot(
    controller_settings, feature="deck", lane="deck_reason"
)

# Removing the resettable provider record must not let an older same-credential
# completion match a newly allocated generation with the same integer value.
aba_old = webui_shell.Api()
aba_old.settings.update(
    cerebras_api_key="same-host-key", cerebras_model="aba-old-model"
)
aba_started = threading.Event()
release_aba = threading.Event()
def aba_fetch(_provider, _key):
    if threading.current_thread().name == "aba-old":
        aba_started.set()
        release_aba.wait(timeout=5)
        return ["aba-old-model"]
    return ["aba-new-model"]
webui_shell.ai.fetch_models = aba_fetch
aba_old_result = {}
aba_worker = threading.Thread(
    name="aba-old", target=lambda: aba_old_result.update(
        aba_old.list_models("cerebras")
    )
)
aba_worker.start()
aba_started.wait(timeout=5)
aba_old.settings.atomic_mapping_update(
    "_confirmed_text_models", lambda records: records.pop("cerebras", None)
)
aba_new = webui_shell.Api()
aba_new.settings.update(
    cerebras_api_key="same-host-key", cerebras_model="aba-new-model"
)
aba_new_result = aba_new.list_models("cerebras")
release_aba.set()
aba_worker.join(timeout=5)
controller_settings.load()
aba_route = processing_route.snapshot(
    controller_settings, feature="deck", lane="deck_reason"
)
print(json.dumps({
    "old": old_result,
    "new": new_result,
    "effective": route.effective_route,
    "model": route.model,
    "reason": route.reason,
    "aba_old": aba_old_result,
    "aba_new": aba_new_result,
    "aba_effective": aba_route.effective_route,
    "aba_model": aba_route.model,
}))
owned.cleanup()
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["new"]["ok"] is True, evidence
    assert evidence["old"].get("obsolete") is True
    assert evidence["effective"] == "hosted"
    assert evidence["model"] == "new-host-model"
    assert evidence["reason"] == "ready"
    assert evidence["aba_new"]["ok"] is True
    assert evidence["aba_old"].get("obsolete") is True
    assert evidence["aba_effective"] == "hosted"
    assert evidence["aba_model"] == "aba-new-model"


@pytest.mark.parametrize(
    "platform_app", PLATFORM_APP_DIRS, ids=("windows", "macos", "linux")
)
def test_provider_activation_rejects_older_cross_provider_completion(platform_app):
    script = r'''
import json
import os
import sys
import tempfile
import threading

owned = tempfile.TemporaryDirectory(prefix="issue21_activation_")
os.environ["MUMBLE_TEST_DATA_DIR"] = owned.name
os.environ["MUMBLE_OFFLINE_TESTS"] = "1"
sys.path.insert(0, sys.argv[1])
import webui_shell

old_host = webui_shell.Api()
old_host.settings.update(
    llm_provider="cerebras",
    openrouter_api_key="shared-openrouter-key",
    openrouter_model="old-openrouter-model",
    cerebras_api_key="shared-cerebras-key",
    cerebras_model="current-cerebras-model",
)
new_host = webui_shell.Api()
controller_settings = type(old_host.settings)()
def acknowledge(_message, timeout=0.8):
    controller_settings.load()
    return {"ok": True}
webui_shell._ctrl_send = acknowledge

old_started = threading.Event()
release_old = threading.Event()
def fetch(provider, _key):
    if provider == "openrouter":
        old_started.set()
        release_old.wait(timeout=5)
        return ["old-openrouter-model"]
    return ["current-cerebras-model"]
webui_shell.ai.fetch_models = fetch
old_result = {}
worker = threading.Thread(
    target=lambda: old_result.update(
        old_host.activate_model_provider("openrouter")
    )
)
worker.start()
old_started.wait(timeout=5)
new_result = new_host.activate_model_provider("cerebras")
release_old.set()
worker.join(timeout=5)
controller_settings.load()
print(json.dumps({
    "old": old_result,
    "new": new_result,
    "provider": controller_settings.get("llm_provider"),
}))
owned.cleanup()
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(platform_app)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=platform_app,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["new"]["ok"] is True, evidence
    assert evidence["old"].get("obsolete") is True
    assert evidence["provider"] == "cerebras"


@pytest.mark.parametrize(
    "platform_app,module_name",
    (
        (PLATFORM_APP_DIRS[0], "mumble"),
        (PLATFORM_APP_DIRS[1], "mumble_mac"),
        (PLATFORM_APP_DIRS[2], "mumble_linux"),
    ),
    ids=("windows", "macos", "linux"),
)
def test_real_deck_dispatch_uses_only_acknowledged_current_model(
    platform_app, module_name
):
    script = r'''
import json
import os
import sys
import threading
import types

os.environ["MUMBLE_TEST_DATA_DIR"] = sys.argv[3]
os.environ["MUMBLE_OFFLINE_TESTS"] = "1"
sys.path.insert(0, sys.argv[1])
controller = __import__(sys.argv[2])
import webui_shell

host = webui_shell.Api()
host.settings.update(
    pro_mode=True, local_only_mode=False, instant_text=False,
    llm_provider="cerebras", cerebras_api_key="first-action-key",
    cerebras_model="first-action-model",
)
controller_settings = type(host.settings)()
drop_discovered_ack = False
discovered_applied = threading.Event()
release_dropped_ack = threading.Event()
def acknowledge(_message, timeout=0.8):
    controller_settings.load()
    record = controller_settings.get("_confirmed_text_models", {}).get("cerebras", {})
    if drop_discovered_ack and record.get("state") == "discovered":
        discovered_applied.set()
        release_dropped_ack.wait(timeout=5)
        return None
    return {"ok": True}
webui_shell._ctrl_send = acknowledge

transport = []
def provider_spy(*args, **kwargs):
    transport.append({"key": args[3], "model": args[4]})
    return iter(["deck result"])
controller.ai.cerebras_intent = provider_spy
controller.local_engine.local_llm_ready = lambda: False

app = controller.Mumble.__new__(controller.Mumble)
app.settings = controller_settings
app.island = None
app._notify = lambda *_args: None
app._idle = lambda: None
app._collect_text = lambda chunks: "".join(chunks)
app._mark_llm_ok = lambda: None
app._paste = lambda _text, **_kwargs: True
app._set_state = lambda _state: None
app.history = types.SimpleNamespace(add=lambda *_args, **_kwargs: None)

def run_deck():
    before = len(transport)
    app._run_deck_job_impl(
        [{"source": "transcript", "text": "private material"}],
        "Summarize faithfully", "Summarize", None,
    )
    return len(transport) - before

before_confirmation = run_deck()
webui_shell.ai.fetch_models = lambda _provider, _key: ["first-action-model"]
drop_discovered_ack = True
lost_result = {}
lost_worker = threading.Thread(
    target=lambda: lost_result.update(host.list_models("cerebras"))
)
lost_worker.start()
discovered_applied.wait(timeout=5)
during_lost_ack = run_deck()
release_dropped_ack.set()
lost_worker.join(timeout=5)
after_lost_ack = run_deck()
drop_discovered_ack = False
first_result = host.list_models("cerebras")
after_confirmation = run_deck()

old_host = webui_shell.Api()
old_host.settings.update(
    cerebras_api_key="stale-action-key", cerebras_model="stale-action-model"
)
new_host = webui_shell.Api()
old_started = threading.Event()
release_old = threading.Event()
def raced_fetch(_provider, key):
    if key == "stale-action-key":
        old_started.set()
        release_old.wait(timeout=5)
        return ["stale-action-model"]
    return ["current-action-model"]
webui_shell.ai.fetch_models = raced_fetch
old_result = {}
worker = threading.Thread(
    target=lambda: old_result.update(old_host.list_models("cerebras"))
)
worker.start()
old_started.wait(timeout=5)
new_host.settings.update(
    cerebras_api_key="current-action-key",
    cerebras_model="current-action-model",
)
new_result = new_host.list_models("cerebras")
release_old.set()
worker.join(timeout=5)
controller_settings.load()
after_stale_completion = run_deck()

print(json.dumps({
    "before": before_confirmation,
    "during_lost": during_lost_ack,
    "after_lost": after_lost_ack,
    "lost_result": lost_result,
    "after": after_confirmation,
    "after_stale": after_stale_completion,
    "first_result": first_result,
    "new_result": new_result,
    "old_result": old_result,
    "transport": transport,
}))
'''
    with tempfile.TemporaryDirectory(prefix="issue21_action_") as owned:
        result = subprocess.run(
            [sys.executable, "-c", script, str(platform_app), module_name, owned],
            capture_output=True,
            text=True,
            timeout=45,
            cwd=platform_app,
        )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["before"] == 0
    assert evidence["during_lost"] == 0
    assert evidence["after_lost"] == 0
    assert evidence["lost_result"]["ok"] is False
    assert evidence["after"] == 1
    assert evidence["after_stale"] == 1
    assert evidence["first_result"]["ok"] is True
    assert evidence["new_result"]["ok"] is True
    assert evidence["old_result"].get("obsolete") is True
    assert evidence["transport"] == [
        {"key": "first-action-key", "model": "first-action-model"},
        {"key": "current-action-key", "model": "current-action-model"},
    ]
