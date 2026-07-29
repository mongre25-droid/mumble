"""Focused regressions for the fourth Issue #21 correction."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


APP_DIR = Path(__file__).resolve().parent
PLATFORM_APP_DIRS = (
    APP_DIR,
    APP_DIR / "Ports" / "macOS" / "app",
    APP_DIR / "Ports" / "Linux" / "app",
)
PORT_APP_DIRS = PLATFORM_APP_DIRS[1:]


def test_model_authority_is_one_canonical_cross_platform_contract():
    authority_paths = [path / "model_authority.py" for path in PLATFORM_APP_DIRS]
    assert all(path.exists() for path in authority_paths)
    canonical = authority_paths[0].read_text(encoding="utf-8").replace("\r\n", "\n")
    assert all(
        path.read_text(encoding="utf-8").replace("\r\n", "\n") == canonical
        for path in authority_paths[1:]
    )

    route = (APP_DIR / "processing_route.py").read_text(encoding="utf-8")
    for duplicate in (
        "def begin_model_confirmation(", "def stage_model_confirmation(",
        "def complete_model_confirmation(", "def invalidate_model_confirmation(",
        "def model_confirmation_is_current(",
    ):
        assert duplicate not in route

    for platform_app in PLATFORM_APP_DIRS:
        shell = (platform_app / "webui_shell.py").read_text(encoding="utf-8")
        settings = (platform_app / "settings.py").read_text(encoding="utf-8")
        assert "model_authority.ModelDiscoveryAuthority" in shell
        assert "processing_route.begin_model_confirmation" not in shell
        assert "processing_route.stage_model_confirmation" not in shell
        assert "processing_route.complete_model_confirmation" not in shell
        assert "def authority_read(" in settings
        assert "def authority_update(" in settings
        assert "model_authority.storage_snapshot(" in settings
        assert "model_authority.storage_update(" in settings
        assert "def activate_confirmed_provider(" not in settings
        assert "def atomic_mapping_read(" not in settings
        controller_name = (
            "mumble.py" if platform_app == APP_DIR else
            "mumble_mac.py" if platform_app.parent.name == "macOS" else
            "mumble_linux.py"
        )
        controller = (platform_app / controller_name).read_text(encoding="utf-8")
        assert "model_authority.controller_reload_response(" in controller

    authority = (APP_DIR / "model_authority.py").read_text(encoding="utf-8")
    assert authority.count("if not self._acknowledged(AUTHORITY_KEY):") >= 2


@pytest.mark.parametrize("platform_app", PORT_APP_DIRS, ids=("macos", "linux"))
def test_port_authority_operations_preserve_malformed_primary_and_backup(
    platform_app,
):
    script = r'''
import json
import os
from pathlib import Path
import sys
import tempfile

owned = tempfile.TemporaryDirectory(prefix="issue21_corruption_")
os.environ["MUMBLE_TEST_DATA_DIR"] = owned.name
os.environ["MUMBLE_OFFLINE_TESTS"] = "1"
sys.path.insert(0, sys.argv[1])
import ai
import branding
import model_authority
from settings import Settings

primary = Path(branding.SETTINGS_PATH)
backup = Path(str(primary) + ".bak")
primary.parent.mkdir(parents=True, exist_ok=True)
valid = json.dumps({
    "llm_provider": "cerebras",
    "cerebras_api_key": "safe-key",
    "cerebras_model": "safe-model",
    "_confirmed_text_models": {},
}).encode("utf-8")
malformed = b'{"llm_provider":'
wrong_shape = b'{"llm_provider":[]}'
settings = Settings()

def snapshot():
    return {
        "primary": primary.read_bytes() if primary.exists() else None,
        "backup": backup.read_bytes() if backup.exists() else None,
    }

def run_case(primary_bytes, backup_bytes):
    if primary_bytes is None:
        primary.unlink(missing_ok=True)
    else:
        primary.write_bytes(primary_bytes)
    if backup_bytes is None:
        backup.unlink(missing_ok=True)
    else:
        backup.write_bytes(backup_bytes)
    expected = snapshot()
    outcomes = []
    for operation in ("read", "update", "activate"):
        before = snapshot()
        try:
            if operation == "read":
                settings.authority_read()
                outcome = "accepted"
            elif operation == "update":
                settings.authority_update(
                    lambda state: state.update(llm_provider="openrouter")
                )
                outcome = "accepted"
            else:
                authority = model_authority.ModelDiscoveryAuthority(
                    settings=settings,
                    providers=ai.PROVIDERS,
                    fetch_models=lambda _provider, _key: ["safe-model"],
                    controller_reload=lambda _key: {"ok": True},
                )
                outcome = authority.activate_provider("cerebras")
        except Exception as error:
            outcome = type(error).__name__
        outcomes.append({
            "operation": operation,
            "outcome": outcome,
            "unchanged": snapshot() == before == expected,
        })
    return outcomes

print(json.dumps({
    "bad_primary": run_case(malformed, valid),
    "bad_backup": run_case(None, malformed),
    "wrong_shape_primary": run_case(wrong_shape, valid),
    "wrong_shape_backup": run_case(None, wrong_shape),
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
    for scenario in evidence.values():
        assert all(item["unchanged"] for item in scenario)
        assert scenario[0]["outcome"] == "ValueError"
        assert scenario[1]["outcome"] == "ValueError"
        assert scenario[2]["outcome"]["ok"] is False


def test_shared_storage_contract_preserves_unrelated_pending_values():
    sys.path.insert(0, str(APP_DIR))
    try:
        import model_authority

        current, result = model_authority.storage_update(
            ("ok", {"llm_provider": "cerebras", "user_name": "disk"}),
            ("missing", {}),
            {"llm_provider": "cerebras", "user_name": "pending"},
            {"user_name"},
            lambda mapping: isinstance(mapping, dict),
            lambda mapping: mapping.update(llm_provider="openrouter"),
        )
    finally:
        sys.path.remove(str(APP_DIR))
    assert result is None
    assert current == {"llm_provider": "openrouter", "user_name": "pending"}
