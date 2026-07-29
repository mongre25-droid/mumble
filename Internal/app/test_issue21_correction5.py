"""Focused regressions for the fifth Issue #21 correction."""

import json
from pathlib import Path
import subprocess
import sys

APP_DIR = Path(__file__).resolve().parent
LINUX_PORT = APP_DIR / "Ports" / "Linux"


def _isolated_app_result(script):
    result = subprocess.run(
        [sys.executable, "-c", script, str(APP_DIR)],
        cwd=APP_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_fallback_settings_provider_activation_requires_fresh_confirmation():
    evidence = _isolated_app_result(r'''
import json
import os
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="issue21_fallback_") as owned:
    os.environ["MUMBLE_TEST_DATA_DIR"] = owned
    os.environ["MUMBLE_OFFLINE_TESTS"] = "1"
    sys.path.insert(0, sys.argv[1])
    import ai
    from app_window import AppWindow
    from settings import Settings

    settings = Settings()
    settings.update(
        llm_provider="openrouter", cerebras_api_key="new-key",
        cerebras_model="exact-model", _confirmed_text_models={},
    )
    class Controller:
        def __init__(self):
            self.settings = settings
            self.pro_key_failed = True
        def _apply_settings_change(self, _key):
            return True
    window = AppWindow.__new__(AppWindow)
    window.ctrl = Controller()
    window.settings = settings

    ai.fetch_models = lambda _provider, _key: []
    denied = window._activate_model_provider(
        "cerebras", "exact-model", key="new-key"
    )
    denied_provider = settings.get("llm_provider")
    ai.fetch_models = lambda _provider, _key: ["exact-model"]
    accepted = window._activate_model_provider(
        "cerebras", "exact-model", key="new-key"
    )
    print(json.dumps({
        "denied": denied, "denied_provider": denied_provider,
        "accepted": accepted, "provider": settings.get("llm_provider"),
        "key_failed": window.ctrl.pro_key_failed,
    }))
''')
    assert evidence["denied"][0] is False
    assert evidence["denied_provider"] == "openrouter"
    assert evidence["accepted"][0] is True
    assert evidence["provider"] == "cerebras"
    assert evidence["key_failed"] is False


def test_fallback_activation_preserves_malformed_primary_or_backup():
    evidence = _isolated_app_result(r'''
import json
import os
from pathlib import Path
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="issue21_fallback_corrupt_") as owned:
    os.environ["MUMBLE_TEST_DATA_DIR"] = owned
    os.environ["MUMBLE_OFFLINE_TESTS"] = "1"
    sys.path.insert(0, sys.argv[1])
    import ai
    import branding
    from app_window import AppWindow
    from settings import Settings

    primary = Path(branding.SETTINGS_PATH)
    backup = Path(str(primary) + ".bak")
    primary.parent.mkdir(parents=True, exist_ok=True)
    valid = json.dumps({
        "llm_provider": "openrouter", "cerebras_api_key": "new-key",
        "cerebras_model": "exact-model", "_confirmed_text_models": {},
    }).encode("utf-8")
    malformed = b'{"llm_provider":'

    class Controller:
        pro_key_failed = True
        def _apply_settings_change(self, _key):
            return True

    settings = Settings()
    outcomes = []
    for primary_bytes, backup_bytes in ((malformed, valid), (valid, malformed)):
        primary.write_bytes(primary_bytes)
        backup.write_bytes(backup_bytes)
        before = (primary.read_bytes(), backup.read_bytes())
        controller = Controller()
        controller.settings = settings
        window = AppWindow.__new__(AppWindow)
        window.ctrl = controller
        window.settings = settings
        ai.fetch_models = lambda _provider, _key: ["exact-model"]
        result = window._activate_model_provider(
            "cerebras", "exact-model", key="new-key"
        )
        outcomes.append({
            "ok": result[0],
            "unchanged": before == (primary.read_bytes(), backup.read_bytes()),
        })
    print(json.dumps(outcomes))
''')
    assert evidence == [
        {"ok": False, "unchanged": True},
        {"ok": False, "unchanged": True},
    ]


def test_windows_generic_setting_bridge_rejects_provider_save():
    evidence = _isolated_app_result(r'''
import json
import os
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="issue21_bridge_") as owned:
    os.environ["MUMBLE_TEST_DATA_DIR"] = owned
    os.environ["MUMBLE_OFFLINE_TESTS"] = "1"
    sys.path.insert(0, sys.argv[1])
    import webui_shell
    from settings import Settings

    api = webui_shell.Api.__new__(webui_shell.Api)
    api.settings = Settings()
    api.settings.update(llm_provider="cerebras")
    webui_shell._ctrl_send = lambda *_args, **_kwargs: {"ok": True}
    result = api.set_setting("llm_provider", "openrouter")
    print(json.dumps({"result": result,
                      "provider": api.settings.get("llm_provider")}))
''')
    assert evidence["result"]["ok"] is False
    assert "activate_model_provider" in evidence["result"]["message"]
    assert evidence["provider"] == "cerebras"


def test_linux_extracted_runtime_can_import_shared_model_authority():
    script = r'''
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, sys.argv[1])
import build_release

with tempfile.TemporaryDirectory(prefix="mumble_linux_build_") as build_raw, \
     tempfile.TemporaryDirectory(prefix="mumble_linux_extract_") as extract_raw:
    outputs = build_release.build(Path(build_raw))
    archive = next(path for path in outputs if path.suffix == ".zip")
    with zipfile.ZipFile(archive) as package:
        package.extractall(extract_raw)
    roots = list(Path(extract_raw).iterdir())
    if len(roots) != 1:
        raise RuntimeError("release archive did not contain one root directory")
    root = roots[0]
    result = subprocess.run(
        [sys.executable, "-c", "import model_authority; print(model_authority.AUTHORITY_KEY)"],
        cwd=root / "app", capture_output=True, text=True, timeout=10,
        env={**os.environ, "MUMBLE_OFFLINE_TESTS": "1"},
    )
    print(json.dumps({"code": result.returncode, "stdout": result.stdout,
                      "stderr": result.stderr}))
'''
    result = subprocess.run(
        [sys.executable, "-c", "import os\n" + script, str(LINUX_PORT)],
        cwd=LINUX_PORT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["code"] == 0, evidence
    assert evidence["stdout"].strip() == "_confirmed_text_models"


def test_windows_has_no_direct_provider_persistence_path():
    shell = (APP_DIR / "webui_shell.py").read_text(encoding="utf-8")
    window = (APP_DIR / "app_window.py").read_text(encoding="utf-8")
    controller = (APP_DIR / "mumble.py").read_text(encoding="utf-8")

    assert 'key == "llm_provider"' in shell
    assert "_activate_model_provider(" in window
    assert "self.ctrl.set_llm_provider(" not in window
    assert "def set_llm_provider(" not in controller
