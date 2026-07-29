#!/usr/bin/env python3
"""Focused regressions for Linux-only controller and lifecycle behaviour."""

import os
import re
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

from PIL import Image

import mumble_linux as linux
import processing_route


def _processing_snapshot(feature, lane):
    settings = {
        "pro_mode": True,
        "local_only_mode": False,
        "llm_provider": "cerebras",
        "cerebras_api_key": "frozen-test-key",
        "cerebras_model": "frozen-test-model",
        "user_name": "Frozen Test User",
        "prompt_prefs": {"tone": "frozen"},
        "primary_language": "en",
        "english_only": True,
        "foreign_languages": [],
        "vocabulary": {},
        "vocabulary_terms": [],
        "modes": {},
        "format_enabled": True,
        "polish_aggressiveness": "Light",
        "instant_text": False,
    }
    return processing_route.snapshot_inputs(
        settings,
        feature=feature,
        lane=lane,
        context="frozen context",
        context_policy="dictation",
        context_strict=False,
        local_model_ready=False,
    )


class LinuxRuntimeRegressions(unittest.TestCase):
    def test_high_confidence_second_opinion_forwards_frozen_email_authority(self):
        snapshot = _processing_snapshot("email", "email")
        app = linux.Mumble.__new__(linux.Mumble)
        captured = {}

        def cloud_generate(*args, **kwargs):
            captured.update(args=args, kwargs=kwargs)
            return "email", "rerouted result"

        app._cloud_generate = cloud_generate
        cfg = {
            "key": snapshot.route.api_key,
            "model": snapshot.route.model,
            "url": linux.ai.CEREBRAS_URL,
            "provider": snapshot.route.provider,
        }

        result = app._handle_second_opinion(
            "clean words", "email", "high", False,
            "raw words", "Frozen Test User", "frozen context", {}, False,
            cfg=cfg, prompt_cfg=cfg, invocation_snapshot=snapshot,
        )

        self.assertEqual(result, ("email", "rerouted result"))
        self.assertEqual(captured["args"][3], "email")
        self.assertIs(captured["kwargs"]["invocation_snapshot"], snapshot)
        self.assertEqual(captured["kwargs"]["expected_feature"], "email")
        self.assertEqual(captured["kwargs"]["expected_lane"], "email")

    def test_high_confidence_second_opinion_wrong_authority_has_zero_transport(self):
        snapshot = _processing_snapshot("dictation", "text")
        app = linux.Mumble.__new__(linux.Mumble)
        cfg = {
            "key": snapshot.route.api_key,
            "model": snapshot.route.model,
            "url": linux.ai.CEREBRAS_URL,
            "provider": snapshot.route.provider,
        }

        with mock.patch.object(
                linux.ai, "cerebras_chat_stream") as transport:
            result = app._handle_second_opinion(
                "clean words", "email", "high", False,
                "raw words", "Frozen Test User", "frozen context", {}, False,
                cfg=cfg, prompt_cfg=cfg, invocation_snapshot=snapshot,
            )

        self.assertEqual(result, ("text", "clean words"))
        transport.assert_not_called()

    def test_controller_keyboard_calls_exist_on_bindings_facade(self):
        with open(linux.__file__, "r", encoding="utf-8") as handle:
            source = handle.read()
        calls = set(re.findall(r"\bkeyboard\.([A-Za-z_]\w*)", source))
        self.assertEqual(calls, {"release", "send", "unhook_all"})
        for name in calls:
            self.assertTrue(callable(getattr(linux.keyboard, name, None)), name)

    def test_x11_send_uses_xdotool_before_raw_uinput(self):
        def which(name):
            return "/usr/bin/xdotool" if name == "xdotool" else None

        completed = types.SimpleNamespace(returncode=0)
        with mock.patch.dict(os.environ, {
                "DISPLAY": ":1", "XDG_SESSION_TYPE": "x11"}, clear=True), \
                mock.patch("shutil.which", side_effect=which), \
                mock.patch("subprocess.run", return_value=completed) as run, \
                mock.patch.object(linux.keyboard.keyboard, "send") as raw_send:
            self.assertTrue(linux.keyboard.send("ctrl+v"))
        self.assertIn("--clearmodifiers", run.call_args.args[0])
        raw_send.assert_not_called()

    def test_xwayland_requires_a_live_display_not_an_installed_binary(self):
        self.assertFalse(linux._usable_x11_display(""))
        with mock.patch("shutil.which", return_value="/usr/bin/xdpyinfo"), \
                mock.patch("subprocess.run",
                           return_value=types.SimpleNamespace(returncode=1)):
            self.assertFalse(linux._usable_x11_display(":99"))
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("os.path.exists", return_value=True):
            self.assertTrue(linux._usable_x11_display(":1.0"))

    def test_only_mumble_injected_gdk_backend_is_scrubbed_for_external_apps(self):
        with mock.patch.dict(linux.os.environ, {
                "GDK_BACKEND": "x11", "MUMBLE_FORCED_GDK_BACKEND": "1"},
                clear=False):
            child = linux._external_child_env()
            self.assertNotIn("GDK_BACKEND", child)
            self.assertNotIn("MUMBLE_FORCED_GDK_BACKEND", child)
            self.assertEqual(linux.os.environ["GDK_BACKEND"], "x11")
        with mock.patch.dict(linux.os.environ, {"GDK_BACKEND": "wayland"},
                             clear=True):
            self.assertEqual(
                linux._external_child_env().get("GDK_BACKEND"), "wayland")

    def test_headless_after_returns_a_cancellable_timer(self):
        fired = threading.Event()
        with mock.patch.object(linux, "GLib", None):
            root = linux._GlibRoot()
            timer = root.after(150, fired.set)
            self.assertIsNotNone(timer)
            root.after_cancel(timer)
            self.assertFalse(fired.wait(0.25))

    def test_primary_input_gid_does_not_trigger_false_warning(self):
        app = linux.Mumble.__new__(linux.Mumble)
        group = types.SimpleNamespace(gr_gid=1234, gr_mem=[])
        user = types.SimpleNamespace(pw_name="mumble-user")
        fake_grp = types.SimpleNamespace(getgrnam=lambda _name: group)
        fake_pwd = types.SimpleNamespace(getpwuid=lambda _uid: user)
        with mock.patch.dict(sys.modules, {"grp": fake_grp, "pwd": fake_pwd}), \
                mock.patch.object(linux.bindings, "_linux_event_paths",
                                  return_value=["/dev/input/event0"]), \
                mock.patch.object(linux.bindings,
                                  "_linux_event_is_readable",
                                  return_value=True), \
                mock.patch.object(linux.os, "access", return_value=True), \
                mock.patch.object(linux.os, "geteuid", return_value=1000,
                                  create=True), \
                mock.patch.object(linux.os, "getuid", return_value=1000,
                                  create=True), \
                mock.patch.object(linux.os, "getgid", return_value=1234,
                                  create=True), \
                mock.patch.object(linux.os, "getegid", return_value=1234,
                                  create=True), \
                mock.patch.object(linux.os, "getgroups", return_value=[],
                                  create=True), \
                mock.patch("subprocess.Popen") as popen:
            app._check_input_permissions()
        popen.assert_not_called()

    def test_image_copy_fails_closed_when_ownership_is_not_confirmed(self):
        app = linux.Mumble.__new__(linux.Mumble)
        app.clipboard = None
        notices = []
        app._notify = lambda title, body: notices.append((title, body))
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "clip.png")
            Image.new("RGB", (2, 2), "red").save(path)

            def which(name):
                return "/usr/bin/xclip" if name == "xclip" else None

            with mock.patch.object(linux.bindings, "_session_type",
                                   return_value="x11"), \
                    mock.patch("shutil.which", side_effect=which), \
                    mock.patch("subprocess.Popen"), \
                    mock.patch.object(linux.Mumble, "_clipboard_has_image",
                                      return_value=False):
                self.assertFalse(app.copy_image(path))
        self.assertTrue(any("ownership" in body.lower()
                            for _title, body in notices))

    def test_wayland_image_copy_never_falls_through_to_xclip(self):
        app = linux.Mumble.__new__(linux.Mumble)
        app.clipboard = None
        app._notify = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "clip.png")
            Image.new("RGB", (2, 2), "red").save(path)

            def which(name):
                return "/usr/bin/xclip" if name == "xclip" else None

            with mock.patch.object(linux.bindings, "_session_type",
                                   return_value="wayland"), \
                    mock.patch("shutil.which", side_effect=which), \
                    mock.patch("subprocess.Popen") as popen:
                self.assertFalse(app.copy_image(path))
            popen.assert_not_called()

    def test_normal_restart_waits_for_old_lock_owner_before_exec(self):
        app = linux.Mumble.__new__(linux.Mumble)
        app._notify = mock.Mock()
        app._quit = mock.Mock()

        def which(name):
            return "/bin/sh" if name == "sh" else None

        with mock.patch.object(linux.update, "pending_swap_script",
                               return_value=""), \
                mock.patch("shutil.which", side_effect=which), \
                mock.patch("subprocess.Popen") as popen:
            self.assertTrue(app._restart())
        argv = popen.call_args.args[0]
        self.assertEqual(argv[:2], ["/bin/sh", "-c"])
        self.assertIn("kill -0", argv[2])
        app._quit.assert_called_once()

    def test_restart_failure_keeps_current_process_alive(self):
        app = linux.Mumble.__new__(linux.Mumble)
        app._notify = mock.Mock()
        app._quit = mock.Mock()
        with mock.patch.object(linux.update, "pending_swap_script",
                               side_effect=RuntimeError("bad marker")):
            self.assertFalse(app._restart())
        app._quit.assert_not_called()
        app._notify.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
