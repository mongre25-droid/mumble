#!/usr/bin/env python3
"""Linux wrapper around Mumble's shared, truthful test runner."""

import os
import sys
from pathlib import Path


SAFE_TESTS = {
    "test_bindings.py", "test_formatting.py", "test_mode_select.py",
    "test_big_shift.py", "test_foreign_boost.py", "test_local_engine.py",
    "test_model_free.py", "test_presets.py", "test_favorites.py",
    "test_webui_api.py", "test_cmd_auth.py", "test_settings_merge.py",
    "test_context_store.py", "test_meeting.py", "test_stats.py", "test_tips.py",
    "test_local_wiring.py", "test_meeting_controller.py",
    "test_core_engine_audit.py", "test_ui_bug_regressions.py",
    "test_service_platform_regressions.py",
    "test_core_port_regressions.py", "test_service_port_regressions.py",
    "test_ui_port_regressions.py", "test_bug_audit_regressions.py",
    "test_downloader.py", "test_model_backend.py", "test_providers.py",
    "test_settings_recovery.py", "test_stt_providers.py",
    "test_tts_providers.py", "test_mac_platform.py", "test_linux_platform.py",
    "test_linux_parity_regressions.py", "test_linux_runtime_regressions.py",
    "test_linux_security_regressions.py", "test_prompt_template_registry.py",
    "test_system_search.py", "test_recording_limits.py",
}


def main():
    root_app = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root_app))
    from runner_core import main as run_main

    return run_main(
        here=os.path.dirname(os.path.abspath(__file__)),
        safe_tests=SAFE_TESTS,
        allow_live=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
