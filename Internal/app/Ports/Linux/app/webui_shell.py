#!/usr/bin/env python3
"""Mumble — the main window, rendered as HTML/CSS/JS in a pywebview shell.

This replaces the tkinter main window (Home / History / Stats / Settings) with
the Golden-Black web UI in webui/. The classic tkinter window is preserved,
frozen, as **Mumble Lite** (MumbleLite/) and as the in-process fallback when
pywebview / the Edge WebView2 runtime is unavailable.

The `Api` class is the JS bridge exposed at `window.pywebview.api.*`. It reads
AND writes the same on-disk stores the controller uses (settings.json,
history.json, clipboard.json, prompts.json, presets.json, favorites.json,
stats.json), so the web UI is a real, functional front-end — not a mock.

Architecture note (honest): this shell runs as its own process (launched by the
controller for the main window, exactly like the old preview did). Settings the
user changes here are persisted to disk immediately; the long-running controller
picks them up on its next read / next launch. A future slice can host the
webview inside the controller process for instant live-apply (STATUS).

Run standalone:  .venv\\Scripts\\python.exe webui_shell.py
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai  # noqa: E402
import branding  # noqa: E402
import local_engine as _local_engine  # noqa: E402
import model_authority  # noqa: E402
import processing_route  # noqa: E402
import presets as presets_mod  # noqa: E402
import reader_store  # noqa: E402
from clipboard import Clipboard  # noqa: E402
from favorites import Favorites  # noqa: E402
from history import History  # noqa: E402
from prompt_history import PromptHistory  # noqa: E402
from settings import DEFAULTS, REMOVED_SETTINGS, Settings  # noqa: E402
from stats import Stats  # noqa: E402


# The command channel: the controller serves paste/job/record/status on
# CMD_PORT; this shell listens on WEBUI_PORT for the History/come-to-front
# commands. WEBUI_PORT doubles as this window's single-instance lock.
CMD_PORT = 49519
WEBUI_PORT = 49520
MAX_READER_IMPORT_BYTES = 32 * 1024 * 1024


def _external_app_env():
    """Environment for apps opened outside Mumble's forced GTK backend."""
    env = dict(os.environ)
    if env.get("MUMBLE_FORCED_GDK_BACKEND") == "1":
        env.pop("GDK_BACKEND", None)
        env.pop("MUMBLE_FORCED_GDK_BACKEND", None)
    return env


MAX_READER_TEXT_CHARS = 600_000


def _mask_api_key(v):
    """ITEM 7: a NON-secret preview of a saved API key for the Settings UI — eight
    bullets plus the last 4 characters, e.g. '•••••••• 9f3a'. Enough for the user
    to recognise *which* key is stored without the secret bytes crossing the JS
    bridge; the real key only comes back via reveal_setting(). Any value containing
    a bullet is rejected by set_setting, so an unedited masked field can't be
    written back over the real key."""
    v = (v or "").strip()
    if not v:
        return ""
    dots = "•" * 8
    return f"{dots} {v[-4:]}" if len(v) >= 8 else dots


def _is_masked_api_key(value):
    """Whether *value* is a non-secret preview returned by get_settings()."""
    return isinstance(value, str) and (
        "•" in value or value.strip() == "[saved]"
    )


_CMD_TOKEN = None


def _read_cmd_token(force=False):
    """The controller's per-session command token (it writes the file at start;
    we attach it to every command so the otherwise-unauthenticated channel can
    reject foreign processes). Cached, with a forced re-read when the controller
    restarts and rotates the token."""
    global _CMD_TOKEN
    if _CMD_TOKEN is None or force:
        try:
            with open(branding.cmd_token_path(), "r", encoding="utf-8") as f:
                _CMD_TOKEN = f.read().strip()
        except OSError:
            _CMD_TOKEN = ""
    return _CMD_TOKEN


def _webui_payload(obj):
    """Attach the controller session token to a web-window command."""
    payload = dict(obj)
    payload["token"] = _read_cmd_token(force=True)
    return payload


def _webui_token_ok(req):
    """Validate controller/second-instance commands sent to ``WEBUI_PORT``."""
    import hmac

    # Always re-read: the controller rotates this token on every restart and a
    # long-lived web window must immediately stop accepting the previous value.
    want = _read_cmd_token(force=True) or ""
    got = req.get("token") or "" if isinstance(req, dict) else ""
    return bool(want) and hmac.compare_digest(str(got), str(want))


def _ctrl_send(obj, timeout=2.0, _retry=True):
    """One JSON command to the controller; returns its reply dict or None
    (None = no controller running, e.g. the shell launched standalone)."""
    try:
        payload = dict(obj)
        payload["token"] = _read_cmd_token()
        with branding.ipc_connect("controller", CMD_PORT, timeout=timeout) as s:
            s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            s.settimeout(timeout)
            data = b""
            while not data.endswith(b"\n") and len(data) < 65536:
                chunk = s.recv(8192)
                if not chunk:
                    break
                data += chunk
        reply = json.loads(data.decode("utf-8", "replace") or "{}")
        # Controller was restarted and rotated its token → refresh once and retry
        # so a legitimate command isn't lost across a controller bounce.
        if (_retry and isinstance(reply, dict)
                and reply.get("message") == "unauthorized"):
            _read_cmd_token(force=True)
            return _ctrl_send(obj, timeout=timeout, _retry=False)
        return reply
    except Exception:
        return None


def _to_int(value, default):
    """int(value) that never raises — a non-numeric setting (e.g. a hand-edited
    "5k" history_max) or a malformed JS arg would otherwise throw across the
    bridge and blank the History/Clipboard tabs. Falls back to `default`."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Api:
    """window.pywebview.api.* — read + write over the live on-disk stores."""

    def __init__(self):
        self.settings = Settings()
        self._sync_manager = None  # lazy-init, shared across cloud_* calls
        self._system_search = None  # lazy local index; Linux only in this port
        # Turn a full settings-form submission into a per-slot delta. A cloud
        # pull after get_presets() is then preserved when this open form saves.
        self._preset_snapshot = None

    # ---- live-controller bridge -----------------------------------------
    def controller_alive(self):
        r = _ctrl_send({"cmd": "status"}, timeout=0.5)
        return bool(r and r.get("ok"))

    def get_app_status(self):
        """Live dictation state from the controller (for the status chip and
        the record button), or a calm default when running standalone."""
        r = _ctrl_send({"cmd": "status"}, timeout=0.4)
        if r and r.get("ok"):
            return {"live": True, "state": r.get("state", "idle"),
                    "text": r.get("text", "Ready"),
                    "recording": bool(r.get("recording")),
                    "active_mode": r.get("active_mode")}
        return {"live": False, "state": "idle", "text": "Ready",
                "recording": False, "active_mode": None}

    def linux_capabilities(self):
        """Truthful controller-owned Linux desktop capability snapshot."""
        response = _ctrl_send({"cmd": "linux_capabilities"}, timeout=1.0)
        if response and response.get("ok"):
            return response
        try:
            import linux_desktop
            return linux_desktop.capability_snapshot()
        except Exception as exc:
            return {"ok": False, "evidence": "source-probe-only",
                    "physical_parity": False, "message": str(exc)[:200]}
    # ---- Mumble Find (Linux experimental) -------------------------------
    def _get_system_search(self):
        engine = getattr(self, "_system_search", None)
        if engine is None:
            from experimental.system_search import SystemSearchEngine
            engine = SystemSearchEngine(
                settings=self.settings,
                data_dir=branding.DATA_DIR,
                platform="linux",
            )
            self._system_search = engine
        return engine

    def system_search_status(self):
        try:
            return self._get_system_search().status()
        except Exception as exc:
            print("system-search status failed:", exc)
            return {"ok": False, "supported": True,
                    "message": "The local search index could not start."}

    def system_search_query(self, query="", category="all", limit=12,
                            generation=None, deadline_ms=1500):
        try:
            return self._get_system_search().search(
                query, category, limit, generation, deadline_ms)
        except Exception as exc:
            print("system-search query failed:", exc)
            return {"ok": False, "results": [],
                    "message": "The local search index could not be read."}

    def system_search_cancel(self, generation):
        try:
            return self._get_system_search().cancel(generation)
        except Exception as exc:
            print("system-search cancellation failed:", exc)
            return False

    def system_search_refresh(self):
        try:
            return self._get_system_search().start_refresh(force=True)
        except Exception as exc:
            print("system-search refresh failed:", exc)
            return {"ok": False,
                    "message": "The local search index could not refresh."}

    def system_search_execute(self, result_id, action="open"):
        try:
            return self._get_system_search().execute(result_id, action)
        except Exception as exc:
            print("system-search action failed:", exc)
            return {"ok": False, "message": "That item could not be opened."}

    def system_search_drag(self, result_id):
        try:
            return self._get_system_search().execute(result_id, "drag")
        except Exception as exc:
            print("system-search drag failed:", exc)
            return {"ok": False, "status": "unsupported",
                    "message": "Drag is unavailable; use Open or Reveal.",
                    "alternatives": ["open", "reveal"]}

    def system_search_icons(self, result_ids, generation=None,
                            icon_version=None):
        try:
            return self._get_system_search().icons(
                result_ids, generation, icon_version)
        except Exception as exc:
            print("system-search icon load failed:", exc)
            return {"ok": True, "icons": {}}

    def system_search_show(self):
        callback = getattr(self, "_show_system_search", None)
        return (callback() if callback else
                {"ok": False, "state": "unavailable"})

    def system_search_hide(self):
        callback = getattr(self, "_hide_system_search", None)
        return (callback() if callback else
                {"ok": False, "state": "unavailable"})

    def system_search_toggle(self, operation_id=None):
        callback = getattr(self, "_toggle_system_search", None)
        return (callback(operation_id) if callback else
                {"ok": False, "state": "unavailable"})

    def system_search_assets(self):
        try:
            root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "experimental", "system_search")
            with open(os.path.join(root, "ui.css"), "r", encoding="utf-8") as handle:
                css = handle.read()
            with open(os.path.join(root, "ui.js"), "r", encoding="utf-8") as handle:
                javascript = handle.read()
            if len(css) > 200_000 or len(javascript) > 500_000:
                raise ValueError("system-search UI assets exceed their size limit")
            return {"ok": True, "css": css, "js": javascript}
        except Exception as exc:
            print("system-search asset load failed:", exc)
            return {"ok": False, "message": "Mumble Find UI is unavailable."}


    # Correction learning currently relies on Windows UI Automation. Keep the
    # shared WebUI bridge explicit and fail closed on Linux.
    def correction_learning_status(self):
        return {
            "ok": False, "enabled": False, "capture_available": False,
            "undo_available": False,
            "message": "Correction learning is not available on Linux.",
        }

    def correction_learning_open(self):
        return {"ok": False,
                "message": "Correction learning is not available on Linux."}

    def correction_learning_undo(self):
        return {"ok": False,
                "message": "Correction learning is not available on Linux."}

    def set_island_mode(self, mode):
        """Set the island's active processing mode from the web UI (Deck Smart
        Mode chips). A None or empty mode clears the selection."""
        r = _ctrl_send({"cmd": "set_island_mode", "mode": mode or None},
                       timeout=0.4)
        if r and r.get("ok"):
            return {"ok": True}
        return {"ok": False, "message": "Controller not reachable"}

    def start_recording(self):
        """The Home record button → EXACTLY the activation-hotkey pipeline
        (controller runs on_hotkey, same as pressing Ctrl+Win)."""
        r = _ctrl_send({"cmd": "record"})
        if r and r.get("ok"):
            return {"ok": True, "live": True,
                    "recording": bool(r.get("recording"))}
        return {"ok": False, "live": False,
                "message": "Recording runs in the full app — start Mumble "
                           "from the tray and press the hotkey anywhere."}

    def _dismiss_for_paste(self):
        """Get the main window out of the way so focus returns to the app being
        pasted into (its History paste buttons call this before deck_paste)."""
        try:
            win = getattr(self, "_window", None)
            if win is not None:
                win.minimize()
        except Exception:
            pass

    def deck_paste(self, text):
        """Quick-paste from History: get this window out of the way so focus
        returns to the app the user came from, then have the controller paste
        there — the same confirmed-restore pipeline as every other paste."""
        self._dismiss_for_paste()
        r = _ctrl_send({"cmd": "paste", "text": text or ""})
        if r and r.get("ok"):
            return {"ok": True, "live": True}
        return {"ok": False, "live": False}

    def deck_paste_image(self, path):
        self._dismiss_for_paste()
        r = _ctrl_send({"cmd": "paste_image", "path": path or ""})
        return {"ok": bool(r and r.get("ok")), "live": bool(r)}

    def set_app_focused(self, focused):
        """Perf: tell the controller the main window gained/lost focus so the
        island animation loop can pause while the user works in another app.
        No visible jump on refocus — the island freezes its frame counter and
        state timers exactly where they left off (VAL-PERF-005). Fire-and-forget:
        we don't wait for a reply because focus changes are latency-sensitive."""
        try:
            _ctrl_send({"cmd": "focused", "value": bool(focused)}, timeout=0.3)
        except Exception:
            pass  # controller may be slow — never block the UI on focus changes
        return True

    # ---- model discovery: powers the Settings model DROPDOWN ------------------
    def _model_discovery_authority(self):
        return model_authority.ModelDiscoveryAuthority(
            self.settings, ai.PROVIDERS, ai.fetch_models,
            lambda key: _ctrl_send({"cmd": "reload", "key": key}, timeout=0.8),
        )

    def begin_model_discovery(self, provider, provider_activation=False):
        return self._model_discovery_authority().begin(
            provider, bool(provider_activation)
        )

    def activate_model_provider(self, provider):
        return self._model_discovery_authority().activate_provider(provider)

    def list_models(self, provider, generation=None, request_id=None):
        """Fetch `provider`'s available model ids using the key already saved for
        it, so the Settings UI can offer a dropdown instead of free-text entry
        (owner v9). Returns {ok, models, message}; never raises into the bridge."""
        return self._model_discovery_authority().list_models(
            provider, generation, request_id
        )

    def get_openrouter_credits(self):
        """OpenRouter balance for the saved sk-or-… key (owner 2026-06-20 — surface
        credits inside Mumble). Returns {ok, total, used, remaining, message};
        never raises into the bridge."""
        key = self.settings.get("openrouter_api_key", "") or ""
        try:
            c = ai.get_openrouter_credits(key)
            return {
                "ok": True,
                "total": c["total"], "used": c["used"],
                "remaining": c["remaining"],
                "message": "$%.2f of $%.2f left" % (c["remaining"], c["total"]),
            }
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # ---- Reader (document-to-audio via OpenRouter TTS, owner 2026-06-20) -------
    def reader_tts_providers(self):
        """List available TTS providers with their auth status."""
        try:
            return ai.list_tts_providers()
        except Exception:
            # Fallback for old ai.py without the new abstraction
            return [{"id": "openrouter", "label": "OpenRouter",
                     "auth_setting": "openrouter_api_key",
                     "has_key": bool((self.settings.get("openrouter_api_key", "") or "").strip()),
                     "default_model": ai.OPENROUTER_TTS_DEFAULT_MODEL}]

    def reader_tts_models(self):
        """The curated TTS voices across ALL providers for the Reader UI.
        Returns provider list, full voice catalogue (male-first, high-quality
        only), and safe defaults for the saved provider/model/voice combo."""
        # Determine the current provider. The user's saved preference lives in
        # reader_tts_provider; fall back to openrouter if unset or invalid.
        saved_provider = (self.settings.get("reader_tts_provider", "") or "").strip()
        available = {p["id"] for p in ai.list_tts_providers()}
        if saved_provider not in available:
            saved_provider = "openrouter"
            self.settings.set("reader_tts_provider", saved_provider)

        # Get voices for the selected provider (or all if provider unspecified).
        voices = ai.get_tts_voices(saved_provider) if saved_provider else ai.get_tts_voices()

        # Determine has_key: at least one provider has a working key.
        has_any_key = any(
            bool((self.settings.get(auth, "") or "").strip())
            for auth in ["openrouter_api_key", "openai_api_key"]
        )

        # Determine the saved model/voice, self-healing stale picks.
        saved_model = (self.settings.get("reader_tts_model", "") or "").strip()
        saved_voice = (self.settings.get("reader_voice", "") or "").strip()

        # Build a set of valid (provider, model, voice) ids from the catalogue.
        valid_voice_ids = {
            v["id"] for v in voices
            if v.get("model") == saved_model and v.get("id")}
        valid_model_ids = {v["model"] for v in voices if v.get("model")}

        # Self-heal: if the saved model/voice don't exist in ANY provider's
        # catalogue, fall back to the current provider's defaults.
        if saved_model not in valid_model_ids:
            p = ai.get_tts_provider(saved_provider)
            saved_model = p.default_model
            saved_voice = ai.OPENROUTER_TTS_DEFAULT_VOICES.get(
                saved_model, p.default_voice)
        elif saved_voice and saved_voice not in valid_voice_ids:
            # Voice may have changed provider — use provider default.
            p = ai.get_tts_provider(saved_provider)
            saved_voice = ai.OPENROUTER_TTS_DEFAULT_VOICES.get(
                saved_model, p.default_voice)

        # Build models list: unique model ids with their label.
        seen_models = set()
        models_list = []
        for v in voices:
            if v["model"] not in seen_models:
                seen_models.add(v["model"])
                models_list.append([v["model"], v.get("model_label", v["model"])])

        return {
            "providers": ai.list_tts_providers(),
            "provider": saved_provider,
            "models": models_list,
            "voices": voices,
            "default_model": saved_model,
            "default_voice": saved_voice,
            "has_key": has_any_key,
        }

    def _reader_speech_decision(self, lane, provider=None, model=None):
        return processing_route.snapshot(
            self.settings,
            feature="reader",
            lane=lane,
            provider_override=provider,
            model_override=model,
        )

    def reader_tts(self, text, model=None, voice=None, provider=None):
        """Synthesize one chunk of text through the selected TTS provider and
        return base64 audio. Never raises into the bridge. Remembers the last
        good provider/model/voice so the Reader resumes with the user's pick.

        A frozen decision authorizes one exact provider/model attempt. Failure
        stops; no sibling model or different provider is attempted."""
        pid = provider or self.settings.get("reader_tts_provider", "openrouter")
        decision = self._reader_speech_decision(
            "reader_speech", provider=pid, model=model)
        if not decision.ready:
            return {"ok": False, "message": "Reader speech stayed on this device because its route is not ready.", "route": decision.public_dict()}
        try:
            audio, ctype, meta = processing_route.call_provider(
                decision, ai.synthesize_with_fallback, text,
                voice_id=voice, model=decision.model, provider_id=decision.provider,
                expected_feature="reader", expected_lane="reader_speech",
                expected_provider=decision.provider)
            if not meta.get("ok"):
                return {"ok": False, "message": meta.get("message", "TTS failed.")}
            if (meta.get("provider") or decision.provider) != decision.provider:
                return {"ok": False, "message": "Reader speech provider did not match its frozen route."}
            effective_provider = meta.get("provider") or pid
            effective_model = meta.get("model") or model
            effective_voice = meta.get("voice") or voice
            self.settings.update(
                reader_tts_provider=effective_provider,
                reader_tts_model=effective_model or "",
                reader_voice=effective_voice or "",
            )
            import base64
            result = {"ok": True,
                      "mime": ctype or "audio/mpeg",
                      "audio": base64.b64encode(audio).decode("ascii"),
                      "provider": effective_provider,
                      "model": effective_model,
                      "voice": effective_voice}
            if meta.get("fallback"):
                result["fallback"] = True
                result["fallback_provider"] = meta.get("fallback_provider", "")
            return result
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_tts_test(self, voice=None, model=None, provider=None, speed=1.0):
        """Synthesize a short test phrase so the user can audition a voice
        through the Reader's "Test" button. Returns an in-memory data URL so
        repeated previews do not leak temporary audio files."""
        try:
            pid = provider or self.settings.get("reader_tts_provider", "openrouter")
            decision = self._reader_speech_decision(
                "reader_speech_test", provider=pid, model=model)
            if not decision.ready:
                return {"ok": False, "message": "Reader speech test stayed on this device because its route is not ready.", "route": decision.public_dict()}
            test_phrase = "Hello. This is a test of the text to speech voice."
            audio, ctype, meta = processing_route.call_provider(
                decision, ai.synthesize_with_fallback, test_phrase,
                voice_id=voice, model=decision.model, provider_id=decision.provider,
                expected_feature="reader", expected_lane="reader_speech_test",
                expected_provider=decision.provider)
            if not meta.get("ok"):
                return {"ok": False, "message": meta.get("message", "TTS test failed.")}
            import base64
            mime = str(ctype or "audio/wav").splitlines()[0].strip()
            if not mime.lower().startswith("audio/"):
                mime = "audio/wav"
            encoded = base64.b64encode(bytes(audio)).decode("ascii")
            return {"ok": True, "url": f"data:{mime};base64,{encoded}"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_list(self):
        try:
            return reader_store.list_docs()
        except Exception as e:
            print("reader_list failed:", e)
            return []

    def reader_open(self, doc_id):
        try:
            return reader_store.get_doc(doc_id)
        except Exception as e:
            print("reader_open failed:", e)
            return None

    def reader_save(self, title, text):
        try:
            if len(text or "") > MAX_READER_TEXT_CHARS:
                return {"ok": False, "message": (
                    "That document contains more than 600,000 characters "
                    "(about 100,000 words). Split it into shorter documents.")}
            did = reader_store.save_doc(title, text)
            return {"ok": bool(did), "id": did}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_position(self, doc_id, position):
        try:
            return {"ok": reader_store.set_position(doc_id, position)}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_delete(self, doc_id):
        try:
            return {"ok": reader_store.delete_doc(doc_id)}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_star(self, doc_id, starred):
        try:
            return {"ok": reader_store.set_starred(doc_id, starred)}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_add_bookmark(self, doc_id, position, label=""):
        try:
            marks = reader_store.add_bookmark(doc_id, position, label)
            return {"ok": marks is not None, "bookmarks": marks or []}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_remove_bookmark(self, doc_id, position):
        try:
            marks = reader_store.remove_bookmark(doc_id, position)
            return {"ok": marks is not None, "bookmarks": marks or []}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_add_to_collection(self, doc_id, collection_name):
        try:
            ok = reader_store.add_to_collection(doc_id, collection_name)
            return {"ok": ok}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_create_collection(self, collection_name):
        try:
            created = reader_store.create_collection(collection_name)
            if not created:
                existing = {c["name"] for c in reader_store.list_collections()}
                if (collection_name or "").strip() in existing:
                    return {"ok": True, "created": False}
            return {"ok": created, "created": created,
                    "message": "Enter a collection name." if not created else ""}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_remove_from_collection(self, doc_id, collection_name):
        try:
            ok = reader_store.remove_from_collection(doc_id, collection_name)
            return {"ok": ok}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_list_collections(self):
        try:
            return reader_store.list_collections()
        except Exception as e:
            print("reader_list_collections failed:", e)
            return []

    def reader_list_collection_docs(self, collection_name, n=50):
        try:
            return reader_store.list_collection_docs(collection_name, n)
        except Exception as e:
            print("reader_list_collection_docs failed:", e)
            return []

    def reader_delete_collection(self, collection_name):
        try:
            n = reader_store.delete_collection(collection_name)
            if n is None:
                return {"ok": False,
                        "message": "The collection could not be saved."}
            return {"ok": True, "affected": n}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_log_session(self, doc_id, start_pos, end_pos, duration_sec):
        try:
            ok = reader_store.log_reading_session(
                doc_id, start_pos, end_pos, duration_sec)
            return {"ok": ok}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_record_stats(self, words_read, duration_sec, doc_completed=False):
        """Persist this reading session into the independent stats store so
        the Reader metrics (total time, pages, docs completed, streaks,
        words, avg session) are always up to date."""
        try:
            st = self._stats()
            ok = st.record_reader_session(
                words_read=words_read, duration_sec=duration_sec,
                doc_completed=bool(doc_completed))
            return {"ok": bool(ok)}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def get_reader_stats(self):
        """The six Reader core metrics + reading streak, all from stats.json."""
        try:
            st = self._stats()
            s = st.reader_summary()
            cur, best = st.reader_streak()
            s["current_streak"] = cur
            s["best_streak"] = best
            return s
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reader_reading_history(self, n=100):
        try:
            return reader_store.list_reading_history(n)
        except Exception as e:
            print("reader_reading_history failed:", e)
            return []

    def reader_continue_reading(self):
        try:
            return reader_store.continue_reading_doc()
        except Exception as e:
            print("reader_continue_reading failed:", e)
            return None

    # ---- Reader file import (redesign — owner 2026-06-29) ----------------------
    def reader_supported_formats(self):
        """Return the list of file extensions the reader can import."""
        try:
            import reader_parser
            return reader_parser.ParserRegistry.supported_extensions()
        except Exception as e:
            print("reader_supported_formats failed:", e)
            return [".txt", ".md"]

    def reader_import_file(self, path):
        """Import a file from disk into the reader library. Returns {ok, id,
        title, format, block_count, message}."""
        try:
            import reader_parser
            import reader_store
            if not isinstance(path, str) or not path.strip():
                return {"ok": False, "message": "Choose a document to import."}
            ext = os.path.splitext(path)[1].lower()
            if reader_parser.ParserRegistry.get(ext) is None:
                return {"ok": False, "message": (
                    f"Unsupported file format: {ext or 'no extension'}.")}
            try:
                if os.path.getsize(path) > MAX_READER_IMPORT_BYTES:
                    return {"ok": False,
                            "message": "That document is larger than 32 MB."}
            except FileNotFoundError:
                return {"ok": False, "message": "File not found."}
            doc = reader_parser.ParserRegistry.parse_file(
                path, max_bytes=MAX_READER_IMPORT_BYTES)
            plain_text = doc.plain_text().strip()
            if not plain_text:
                return {"ok": False, "message": (
                    "No readable text was found in that document. "
                    "Scanned PDFs need OCR before the Reader can speak them.")}
            if len(plain_text) > MAX_READER_TEXT_CHARS:
                return {"ok": False, "message": (
                    "That document contains more than 600,000 characters "
                    "(about 100,000 words). Split it into shorter documents.")}
            fmt = doc.metadata.get("format", "text")
            did = reader_store.save_doc_parsed(doc, fmt=fmt, source_path=path)
            return {"ok": bool(did), "id": did, "title": doc.title,
                    "format": fmt, "block_count": len(doc.blocks)}
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        except FileNotFoundError:
            return {"ok": False, "message": "File not found."}
        except Exception as e:
            print("reader_import_file failed:", e)
            return {"ok": False, "message": str(e)}

    def reader_import_bytes(self, data_b64, filename, source_path=""):
        """Import in-memory file bytes (base64 from drag-drop or clipboard)
        into the reader library. Returns {ok, id, title, format, block_count,
        message}."""
        try:
            import base64
            import binascii
            import reader_parser
            import reader_store
            if not isinstance(data_b64, str):
                return {"ok": False, "message": "The imported file data is invalid."}
            max_encoded = 4 * ((MAX_READER_IMPORT_BYTES + 2) // 3)
            if len(data_b64) > max_encoded:
                return {"ok": False,
                        "message": "That document is larger than 32 MB."}
            try:
                raw = base64.b64decode(data_b64, validate=True)
            except (binascii.Error, ValueError):
                return {"ok": False,
                        "message": "The imported file data is not valid base64."}
            if len(raw) > MAX_READER_IMPORT_BYTES:
                return {"ok": False,
                        "message": "That document is larger than 32 MB."}
            doc = reader_parser.ParserRegistry.parse_bytes(
                raw, filename, source_path=source_path)
            plain_text = doc.plain_text().strip()
            if not plain_text:
                return {"ok": False, "message": (
                    "No readable text was found in that document. "
                    "Scanned PDFs need OCR before the Reader can speak them.")}
            if len(plain_text) > MAX_READER_TEXT_CHARS:
                return {"ok": False, "message": (
                    "That document contains more than 600,000 characters "
                    "(about 100,000 words). Split it into shorter documents.")}
            fmt = doc.metadata.get("format", "text")
            did = reader_store.save_doc_parsed(doc, fmt=fmt,
                                               source_path=source_path)
            return {"ok": bool(did), "id": did, "title": doc.title,
                    "format": fmt, "block_count": len(doc.blocks)}
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        except Exception as e:
            print("reader_import_bytes failed:", e)
            return {"ok": False, "message": str(e)}

    def reader_import_clipboard(self, text, title=""):
        """Save clipboard text as a plain-text reader document."""
        try:
            import reader_store
            if len(text or "") > MAX_READER_TEXT_CHARS:
                return {"ok": False, "message": (
                    "That text contains more than 600,000 characters "
                    "(about 100,000 words). Split it into shorter documents.")}
            did = reader_store.save_doc(title or "Clipboard import", text)
            return {"ok": bool(did), "id": did}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _pick_file(self, file_types):
        """Show pywebview's native picker and return one selected path."""
        window = getattr(self, "_window", None)
        if window is None or not hasattr(window, "create_file_dialog"):
            raise RuntimeError("The Mumble window is not ready for a file picker.")
        selected = window.create_file_dialog(
            allow_multiple=False, file_types=tuple(file_types))
        if isinstance(selected, str):
            return selected
        return selected[0] if selected else ""

    def reader_show_file_picker(self):
        """Open a native file picker for supported formats and import the
        chosen file. Returns the same shape as reader_import_file, or
        {ok:False, cancelled:True} when the user cancels."""
        try:
            import reader_parser
            exts = reader_parser.ParserRegistry.supported_extensions()
            all_patterns = ";".join("*" + ext for ext in exts)
            path = self._pick_file((
                f"All supported ({all_patterns})",
                "Documents (*.pdf;*.docx;*.odt;*.rtf;*.epub)",
                "Spreadsheets (*.csv;*.xlsx)",
                "Presentations (*.pptx)",
                "Text and Markdown (*.txt;*.text;*.md;*.markdown)",
                "HTML (*.html;*.htm)",
                "All files (*.*)",
            ))
            if not path:
                return {"ok": False, "cancelled": True}
            return self.reader_import_file(path)
        except Exception as e:
            print("reader_show_file_picker failed:", e)
            return {"ok": False, "message": str(e)}

    def reader_summarize(self, text):
        """Summarize a document with the user's configured LLM provider and
        RETURN the summary (unlike run_deck_job, which pastes at the cursor).
        Returns {ok, summary, message}; never raises into the bridge."""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "message": "Nothing to summarize."}
        invocation = processing_route.snapshot_inputs(
            self.settings, feature="reader", lane="reader_summary",
            context=text, context_policy="reader_document",
        )
        decision = invocation.route
        text = invocation.context
        if not decision.ready:
            return {
                "ok": False,
                "message": "This summary stayed on this device because the selected text-processing route is not ready.",
                "route": decision.public_dict(),
            }
        try:
            provider = decision.provider
            info = ai.PROVIDERS[provider]
            # Token-limit policy: Cerebras' free tier is rate-limited (~30k
            # tokens/min), so cap the input to stay inside one request and ask
            # for a tight summary. Paid providers (OpenAI, Anthropic, OpenRouter,
            # DeepSeek, Groq) and a local model can take the WHOLE document — a
            # long chapter must not be silently truncated for them.
            if provider == "cerebras":
                doc, out_budget, t_out = text[:24000], 900, 90
            else:
                doc, out_budget, t_out = text, 2000, 120
            system = (
                "You are a precise summarizer. Summarize the user's text faithfully. "
                "Lead with a one-sentence TL;DR, then 3-6 short bullet points of the key "
                "information. Add nothing that isn't in the text. Output only the summary, "
                "no preamble.")
            user = "Summarize the following:\n\n" + doc
            summary = processing_route.call_provider(
                decision,
                ai.cerebras_chat,
                system, user, decision.api_key,
                model=decision.model, url=info.get("url"),
                max_tokens=out_budget, timeout=t_out,
                expected_feature="reader", expected_lane="reader_summary",
            )
            summary = (summary or "").strip()
            if not summary:
                return {"ok": False, "message": "The summary came back empty."}
            return {"ok": True, "summary": summary,
                    "route": decision.public_dict()}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # ---- store helpers: re-read disk ONLY when the file changed (owner v7) -----
    # The old build constructed a brand-new store on EVERY read call, each one
    # parsing the whole on-disk JSON (history & clipboard cap at 5000 entries).
    # A single History render builds the history AND favourites stores; the Home
    # overview builds Stats; refreshes/`pyRefresh` re-ran all of it — so opening
    # History or Home meant re-parsing thousands of entries on every tick, the
    # biggest steady-state cost behind the "heavy / less responsive" feel.
    # Now each store is cached and only rebuilt when its file's (mtime, size)
    # signature changes — which it does on any real write (a new transcript, a
    # clipboard capture, a favourite toggle, a delete/clear) and on the
    # controller's refresh push. So the data is never stale, but an unchanged
    # store costs one os.stat() instead of a full re-parse.
    def _cached_store(self, key, path, factory):
        try:
            st = os.stat(path)
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None   # file not created yet — build fresh (it's tiny/empty)
        cache = self.__dict__.setdefault("_store_cache", {})
        hit = cache.get(key)
        if sig is not None and hit is not None and hit[0] == sig:
            return hit[1]
        store = factory()
        if sig is not None:
            cache[key] = (sig, store)
        return store

    def _history(self):
        return self._cached_store(
            "history", branding.HISTORY_JSON,
            lambda: History(branding.HISTORY_JSON, branding.HISTORY_TXT,
                            _to_int(self.settings.get("history_max", 5000), 5000)))

    def _clipboard(self):
        return self._cached_store(
            "clipboard", branding.CLIPBOARD_JSON,
            lambda: Clipboard(branding.CLIPBOARD_JSON,
                              _to_int(self.settings.get("clipboard_max", 5000), 5000)))

    def _stats(self):
        return self._cached_store(
            "stats", branding.STATS_JSON, lambda: Stats(branding.STATS_JSON))

    def _prompts(self):
        return self._cached_store(
            "prompts", os.path.join(branding.DATA_DIR, "prompts.json"),
            lambda: PromptHistory())

    def _favs(self):
        return self._cached_store(
            "favorites", os.path.join(branding.DATA_DIR, "favorites.json"),
            lambda: Favorites())

    def reset_stats(self, scope="all"):
        """ITEM 13 (owner 2026-06-29): permanently clear the user's statistics.
        The web UI confirms first (it is irreversible). Default wipes everything;
        "dictation" / "reader" scope it. Stats keep recording normally afterwards —
        this only zeroes the accumulated history."""
        try:
            sc = scope if scope in ("all", "dictation", "reader") else "all"
            ok = self._stats().reset(sc)
            return {"ok": bool(ok)}
        except Exception as e:
            print("reset_stats failed:", e)
            return {"ok": False, "message": str(e)}

    # ======================================================================
    #  MEETING MODE (owner 2026-06-29 — meeting-mode milestone)
    # ======================================================================
    def meeting_list(self):
        """Return all saved meetings, newest first."""
        try:
            import meeting_store
            return meeting_store.list_meetings()
        except Exception as e:
            print("meeting_list failed:", e)
            return []

    def meeting_search(self, query):
        """Search titles and full transcripts only in the local meeting store."""
        try:
            import meeting_store
            items = meeting_store.search_meetings(query)
            if meeting_store.store_health() == "corrupt":
                return {"ok": False, "items": [],
                        "message": "Meeting metadata could not be read safely."}
            return {"ok": True, "items": items,
                    "total": len(meeting_store.list_meetings())}
        except Exception as e:
            print("meeting_search failed:", e)
            return {"ok": False, "items": [],
                    "message": "Search could not read the local meeting library."}

    def meeting_context(self):
        """Bounded credential-free Meetings context ledger."""
        routes = self._settings_route_state()
        selected = self.settings.get("mic_device", None)
        microphone = next((
            str(item.get("name") or "System default")
            for item in self.list_microphones()
            if item.get("index") == selected
            or str(item.get("index")) == str(selected)
        ), "System default")
        analysis = processing_route.snapshot_inputs(
            self.settings, feature="meetings", lane="meeting_analysis",
            context="", context_policy="meeting_transcript").route.public_dict()
        return {"ok": True, "microphone": microphone,
                "saved_location": "Private Mumble meeting library",
                "transcription": dict(routes["transcription"]),
                "analysis": analysis}

    def meeting_open(self, meeting_id):
        """Full meeting data with segments + speakers."""
        try:
            import meeting_store
            return meeting_store.get_meeting(meeting_id)
        except Exception as e:
            print("meeting_open failed:", e)
            return None

    def meeting_delete(self, meeting_id):
        try:
            import meeting_store
            ok = meeting_store.delete_meeting(meeting_id)
            return {"ok": bool(ok), "message": (
                "" if ok else "The recording could not be removed; the meeting was kept.")}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def meeting_star(self, meeting_id, starred):
        try:
            import meeting_store
            return {"ok": meeting_store.set_starred(meeting_id, starred)}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def meeting_rename_speaker(self, meeting_id, label, name):
        try:
            import meeting_store
            return {"ok": meeting_store.rename_speaker(meeting_id, label, name)}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def meeting_update_title(self, meeting_id, title):
        try:
            import meeting_store
            return {"ok": meeting_store.update_meeting(
                meeting_id, title=title)}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def meeting_summarize(self, meeting_id):
        """Run AI summarization via the configured LLM provider.
        Returns {ok, summary, message}."""
        try:
            import meeting
            summary = meeting.summarize_meeting(meeting_id, self.settings)
            if summary is meeting.ANALYSIS_IN_PROGRESS:
                return {"ok": False, "busy": True,
                        "message": "Meeting analysis is already running."}
            if summary:
                return {"ok": True, "summary": summary}
            return {"ok": False,
                    "message": ("Could not generate summary. An API key may "
                                "be needed in Settings.")}
        except Exception as e:
            return {"ok": False, "message": (
                f"{e} Check the API key and model in Settings.")}

    def meeting_extract_actions(self, meeting_id):
        """Extract action items via LLM. Returns {ok, items, message}."""
        try:
            import meeting
            items = meeting.extract_action_items(meeting_id, self.settings)
            if items is meeting.ANALYSIS_IN_PROGRESS:
                return {"ok": False, "busy": True, "items": [],
                        "message": "Meeting analysis is already running."}
            return {"ok": bool(items), "items": items}
        except Exception as e:
            return {"ok": False, "items": [], "message": str(e)}

    def meeting_extract_decisions(self, meeting_id):
        """Extract key decisions via LLM. Returns {ok, items, message}."""
        try:
            import meeting
            items = meeting.extract_key_decisions(meeting_id, self.settings)
            if items is meeting.ANALYSIS_IN_PROGRESS:
                return {"ok": False, "busy": True, "items": [],
                        "message": "Meeting analysis is already running."}
            return {"ok": bool(items), "items": items}
        except Exception as e:
            return {"ok": False, "items": [], "message": str(e)}

    def meeting_extract_questions(self, meeting_id):
        """Extract open questions via LLM. Returns {ok, items, message}."""
        try:
            import meeting
            items = meeting.extract_open_questions(meeting_id, self.settings)
            if items is meeting.ANALYSIS_IN_PROGRESS:
                return {"ok": False, "busy": True, "items": [],
                        "message": "Meeting analysis is already running."}
            return {"ok": bool(items), "items": items}
        except Exception as e:
            return {"ok": False, "items": [], "message": str(e)}

    def meeting_start_recording(self):
        """Ask the live controller to start a meeting recording.
        Returns {ok, recording, message}."""
        r = _ctrl_send({"cmd": "meeting_record_start"}, timeout=1.0)
        if r and r.get("ok"):
            return {"ok": True, "recording": True, "meeting_id": None}
        return {"ok": False, "message": (
            r or {}).get("message", "Controller unavailable.")}

    def meeting_stop_recording(self, title=""):
        """Ask the controller to stop recording and return the meeting id."""
        r = _ctrl_send({"cmd": "meeting_record_stop",
                        "title": title or ""}, timeout=10.0)
        if r and r.get("ok"):
            return {"ok": True, "meeting_id": r.get("meeting_id")}
        return {"ok": False, "message": (
            r or {}).get("message", "Controller unavailable.")}

    def meeting_import_audio(self, path):
        """Import a pre-recorded audio file. Returns {ok, meeting_id, message}."""
        if not path:
            if os.environ.get("MUMBLE_OFFLINE_TESTS") == "1":
                return {"ok": False, "cancelled": True}
            try:
                # soundfile/libsndfile does not decode AAC/M4A. Do not
                # advertise a format the import pipeline will reject.
                path = self._pick_file((
                    "Audio files (*.wav;*.mp3;*.flac;*.ogg)",
                    "WAV audio (*.wav)",
                    "All files (*.*)",
                ))
            except Exception as e:
                return {"ok": False,
                        "message": f"Could not open the file picker: {e}"}
            if not path:
                return {"ok": False, "cancelled": True}
        r = _ctrl_send({"cmd": "meeting_import_audio",
                        "path": path or ""}, timeout=30.0)
        if r and r.get("ok"):
            return {"ok": True, "meeting_id": r.get("meeting_id")}
        return {"ok": False, "message": (
            r or {}).get("message", "Controller unavailable.")}

    def meeting_get_tags(self):
        try:
            import meeting_store
            return meeting_store.list_tags()
        except Exception as e:
            print("meeting_get_tags failed:", e)
            return []

    def meeting_export(self, meeting_id, fmt="txt"):
        """Export a meeting transcript to the requested format.
        Returns {ok, content, mime} or {ok: False, message}."""
        try:
            import meeting
            return meeting.export_meeting(meeting_id, fmt)
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def meeting_play_audio(self, meeting_id):
        """Open a meeting's private audio file in the desktop player."""
        try:
            import meeting
            import meeting_store
            record = meeting_store.get_meeting(meeting_id)
            if not record:
                return {"ok": False, "message": "Meeting not found."}
            path = meeting._resolve_audio_path(record.get("audio_path"))
            if not path:
                return {"ok": False, "message": "Meeting audio is missing."}
            subprocess.Popen(["xdg-open", path])
            return {"ok": True}
        except Exception as e:
            return {"ok": False,
                    "message": f"Could not open the recording: {e}"}

    def meeting_pause_recording(self):
        """Pause the active meeting recording."""
        r = _ctrl_send({"cmd": "meeting_record_pause"}, timeout=1.0)
        if r and r.get("ok"):
            return {"ok": True}
        return {"ok": False, "message": (
            r or {}).get("message", "Controller unavailable.")}

    def meeting_resume_recording(self):
        """Resume a paused meeting recording."""
        r = _ctrl_send({"cmd": "meeting_record_resume"}, timeout=1.0)
        if r and r.get("ok"):
            return {"ok": True}
        return {"ok": False, "message": (
            r or {}).get("message", "Controller unavailable.")}

    def meeting_recording_status(self):
        r = _ctrl_send({"cmd": "meeting_record_status"}, timeout=5.0)
        if r and r.get("ok"):
            return r
        return {"ok": False, "active": False, "state": "unknown",
                "message": (r or {}).get("message", "Controller unavailable.")}

    def meeting_retry(self, meeting_id):
        """Retry a durable interrupted/failed transcription in the controller."""
        r = _ctrl_send(
            {"cmd": "meeting_retry", "meeting_id": meeting_id or ""},
            timeout=2.0,
        )
        if isinstance(r, dict):
            return r
        return {"ok": False, "message": "Controller unavailable."}

    def meeting_set_processing_mode(self, mode):
        """Persist the meeting processing mode (lightweight/deep)."""
        try:
            import meeting
            ok = meeting.set_processing_mode(mode, self.settings)
            if ok:
                return {"ok": True}
            valid = str(mode or "").strip().lower() in ("lightweight", "deep")
            return {"ok": False, "message": (
                "Couldn't save the processing mode." if valid else
                "Invalid mode. Must be 'lightweight' or 'deep'.")}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def meeting_extract_deep(self, meeting_id):
        """Run deep processing on a meeting (single LLM call)."""
        try:
            import meeting
            result = meeting.process_meeting_deep(meeting_id, self.settings)
            if result is meeting.DEEP_IN_PROGRESS:
                return {"ok": False, "busy": True,
                        "message": "Deep processing is already running."}
            if result:
                return {
                    "ok": True,
                    "summary": result.get("summary", ""),
                    "actions": result.get("action_items", []),
                    "decisions": result.get("key_decisions", []),
                    "questions": result.get("open_questions", []),
                }
            return {"ok": False,
                    "message": "Deep processing failed. "
                               "Check LLM key in Settings."}
        except Exception as e:
            return {"ok": False, "message": (
                f"{e} Check the API key and model in Settings.")}

    # ======================================================================
    #  READ
    # ======================================================================
    def get_overview(self):
        st = self._stats()
        s = st.summary()
        cur, best = st.streak()
        provider = self.settings.get("llm_provider", "cerebras")
        # Meeting stats (owner 2026-06-29 — meeting-mode milestone)
        try:
            import meeting_store
            meetings = meeting_store.list_meetings()
            meeting_count = len(meetings)
            meeting_minutes = sum(
                m.get("duration_sec", 0) for m in meetings) / 60.0
        except Exception:
            meeting_count = 0
            meeting_minutes = 0.0
        # Hardware info for the homepage hardware-awareness suggestion
        hw = branding.get_hardware_info()
        routes = self._settings_route_state()
        return {
            "version": branding.VERSION,
            "tagline": branding.APP_TAGLINE,
            "total_words": s.get("total_words", 0),
            "total_transcripts": s.get("total_transcripts", 0),
            "today_words": s.get("today_words", 0),
            "time_saved": s.get("typing_time_display", "—"),
            "current_streak": cur,
            "best_streak": best,
            "provider": provider,
            "model": self.settings.get("model", "base.en"),
            "pro_mode": bool(self.settings.get("pro_mode", True)),
            "transcription_mode": self.settings.get("transcription_mode", "local"),
            "cloud_transcription_provider": self.settings.get(
                "cloud_transcription_provider", "groq"),
            "transcription_route": dict(routes["transcription"]),
            "effective_transcription_mode": routes["transcription"]["effective"],
            "first_run": bool(self.settings.get("first_run", True)),
            # "Connected" must reflect the CURRENT provider's key — a saved
            # OpenAI key while you're on Cerebras is NOT configured (this was
            # the "API configured shows wrongly" bug).
            "key_configured": self._provider_key_ok(provider),
            # "Switch to Mumble Lite" is only offered when Lite actually ships
            # alongside this install (release zips may omit it).
            "lite_available": os.path.exists(self._lite_bat()),
            # Meeting stats
            "meeting_count": meeting_count,
            "meeting_minutes": round(meeting_minutes, 1),
            # Hardware info for the homepage hardware-awareness suggestion
            "hw_cpu_cores": hw["cpu_cores"],
            "hw_ram_gb": hw["ram_gb"],
            "hw_tier": hw["tier"],
            "update_enabled": __import__("update").update_channel_enabled(),
        }

    def _provider_key_ok(self, provider):
        if provider == "local":
            return True  # a local LLM needs no key
        try:
            import ai
            key_setting = (ai.PROVIDERS.get(provider) or {}).get(
                "key_setting", f"{provider}_api_key")
        except Exception:
            key_setting = f"{provider}_api_key"
        return bool((self.settings.get(key_setting, "") or "").strip())

    def _lite_bat(self):
        return os.path.join(os.path.dirname(branding.INSTALL_DIR),
                            "MumbleLite", "Open Mumble Lite.bat")

    def get_hotkeys(self):
        import bindings
        def pp(k, d):
            try:
                return bindings.pretty(self.settings.get(k, d) or d)
            except Exception:
                return self.settings.get(k, d) or d
        return {
            "hotkey": pp("hotkey", "ctrl+windows"),
            "quick_paste_hotkey": pp("quick_paste_hotkey", "ctrl+alt+v"),
            "history_hotkey": pp("history_hotkey", "ctrl+alt+d"),
            "search_hotkey": pp("search_hotkey", "ctrl+alt+f"),
            "web_search_hotkey": pp("web_search_hotkey", "ctrl+alt+s"),
            "mode_key": pp("mode_key", "right shift"),
        }

    def get_transcripts(self, limit=50):
        h, favs = self._history(), self._favs()
        out = []
        for e in h.recent(_to_int(limit, 50)):
            t = e.get("text", "")
            out.append({
                "time": e.get("time", ""), "stamp": e.get("stamp", ""),
                "mode": e.get("mode", "text"), "text": t,
                "raw": e.get("raw"), "words": e.get("words", 0),
                "duration": e.get("duration", 0), "fav": favs.is_fav(t),
                "quality": e.get("quality"), "via": e.get("via"),
            })
        return out

    def get_clipboard(self, limit=50):
        c, favs = self._clipboard(), self._favs()
        out = []
        for e in c.recent(_to_int(limit, 50)):
            if e.get("type") == "image":
                out.append({"type": "image", "time": e.get("time", ""),
                            "stamp": e.get("stamp", ""),  # needed for History date grouping
                            "size": e.get("size", ""), "path": e.get("path", ""),
                            "fav": False})
            else:
                t = e.get("text", "")
                out.append({"type": "text", "time": e.get("time", ""),
                            "stamp": e.get("stamp", ""),  # without this, clips all fell into "Older"
                            "text": t, "fav": favs.is_fav(t)})
        return out

    def get_prompts(self):
        p, favs = self._prompts(), self._favs()
        return [
            {"time": e.get("time", ""), "request": e.get("request", ""),
             "prompt": e.get("prompt", ""), "fav": favs.is_fav(e.get("prompt", ""))}
            for e in p.all_prompts()
        ]

    def get_daily_stats(self, days=7):
        return [{"day": d, "words": w, "transcripts": t}
                for (d, w, t) in self._stats().daily_stats(_to_int(days, 7))]

    def get_mode_stats(self):
        return [{"mode": m, "count": c, "words": w}
                for (m, c, w) in self._stats().mode_stats()]

    def get_presets(self):
        out = []
        n_builtin = len(presets_mod.BUILTIN)
        custom = presets_mod.load_custom()
        self._preset_snapshot = {
            slot: dict(value) for slot, value in custom.items()
        }
        for slot, title, desc, instr in presets_mod.all_presets(custom):
            if instr is None:          # empty custom slot — not a real preset
                continue
            out.append([slot, title, desc, slot <= n_builtin, instr])
        return out

    def reveal_setting(self, key):
        """ITEM 7: return the REAL stored value of an API key, only on an explicit
        user request (the eye/reveal toggle in Settings), so a saved key can be
        inspected and verified. Deliberately restricted to *_api_key keys — this is
        a verify-your-own-key affordance, never a general secret-read endpoint."""
        try:
            if isinstance(key, str) and key.endswith("_api_key"):
                return self.settings.get(key, "") or ""
        except Exception as e:
            print("reveal_setting failed:", e)
        return ""

    def local_llm_status(self):
        """Report whether the fully-offline on-device LLM lane is active. Computed
        independently here from the SAME inputs the controller uses at boot (a GGUF
        in the models dir / the local_llm_model setting + the bundled binary), so it
        needs no IPC and always matches what the controller wired. Returns
        {ready, reason, model, models_dir, has_binary, enabled}."""
        try:
            enabled = bool(self.settings.get("local_llm_enabled", True))
            if not enabled:
                return {"ready": False, "reason": "disabled in settings",
                        "model": None, "models_dir": branding.MODELS_DIR,
                        "has_binary": bool(branding.llama_cli_path()),
                        "enabled": False}
            model = _local_engine.discover_model(
                [branding.MODELS_DIR],
                explicit_path=(self.settings.get("local_llm_model", "") or ""))
            backend, reason = _local_engine.build_backend(
                model_path=model, cli_bin=branding.llama_cli_path())
            return {"ready": backend.available(), "reason": reason,
                    "model": model, "models_dir": branding.MODELS_DIR,
                    "has_binary": bool(branding.llama_cli_path()),
                    "enabled": True}
        except Exception as e:
            return {"ready": False, "reason": str(e), "model": None,
                    "models_dir": getattr(branding, "MODELS_DIR", ""),
                    "has_binary": False, "enabled": True}

    def _settings_route_state(self):
        """Project requested and effective privacy routes from runtime inputs."""
        stt_decision = processing_route.snapshot(
            self.settings, feature="dictation", lane="speech_to_text"
        ).public_dict()
        requested_stt = "cloud" if stt_decision["requested_route"] == "hosted" else "local"
        stt_provider = stt_decision["provider"]
        stt_supported = stt_decision["provider_supported"]
        stt_has_key = stt_decision["key_present"]
        stt_effective = "cloud" if stt_decision["effective_route"] == "hosted" else "local"
        stt_reason = {"local_transcription": "selected", "device_only": "local_only", "hosted_processing_off": "pro_off", "missing_key": "no_key", "missing_model": "no_model", "ready": "selected"}.get(stt_decision["reason"], stt_decision["reason"])
        local_only = bool(self.settings.get("local_only_mode", False))

        route_facts = processing_route.settings_state(self.settings)
        plain_decision = route_facts["plain_processing"]
        action_decision = route_facts["action_processing"]
        reason_compat = {
            "device_only": "local_only",
            "hosted_processing_off": "pro_off",
            "missing_key": "no_key",
            "ready": "selected",
        }
        processing_effective = (
            "cloud" if action_decision["effective_route"] == "hosted" else "local"
        )
        feature_routes = {
            key: processing_route.snapshot(
                self.settings, feature=feature, lane=lane
            ).public_dict()
            for key, feature, lane in (
                ("plain_dictation", "dictation", "text"),
                ("prompt", "prompt", "prompt"),
                ("email", "email", "email"),
                ("reply", "reply", "reply"),
                ("deck_actions", "deck", "deck_action"),
                ("meetings_analysis", "meetings", "meeting_analysis"),
                ("reader_actions", "reader", "reader_summary"),
            )
        }
        return {
            "transcription": {
                "requested": requested_stt,
                "provider": stt_provider,
                "provider_supported": stt_supported,
                "has_key": stt_has_key,
                "model": stt_decision["model"],
                "effective": stt_effective,
                "reason": stt_reason,
                "sends_audio": stt_effective == "cloud",
            },
            "plain_processing": {
                "effective": (
                    "cloud"
                    if plain_decision["effective_route"] == "hosted"
                    else "local"
                ),
                "reason": reason_compat.get(
                    plain_decision["reason"], plain_decision["reason"]
                ),
                "sends_text": plain_decision["effective_route"] == "hosted",
                "decision": plain_decision,
            },
            "action_processing": {
                "provider": action_decision["provider"],
                "provider_supported": action_decision["provider_supported"],
                "has_key": action_decision["key_present"],
                "effective": processing_effective,
                "reason": reason_compat.get(
                    action_decision["reason"], action_decision["reason"]
                ),
                "sends_text": processing_effective == "cloud",
                "decision": action_decision,
            },
            "feature_routes": feature_routes,
            "local_only": local_only,
        }

    def get_settings(self):
        try:
            self.settings.load()
        except Exception:
            pass
        keys = [
            "user_name", "hotkey", "quick_paste_hotkey", "history_hotkey",
            "search_hotkey", "web_search_hotkey", "system_search_include_files",
            "system_search_roots", "system_search_max_items",
            "search_engine", "browser", "mode_key", "mode_button_enabled",
            "modes", "prompt_mode_enabled", "auto_format",
            "prompt_prefs", "polish_aggressiveness", "instant_text",
            "pro_mode", "llm_provider",
            "cerebras_api_key", "cerebras_model", "openai_api_key",
            "groq_api_key",
            "openrouter_api_key", "openrouter_model",
            # Cloud transcription (advanced, opt-in)
            "transcription_mode", "cloud_transcription_provider",
            "groq_transcription_model", "openai_transcription_model",
            "openrouter_transcription_model",
            "local_url", "local_model", "model", "language", "mic_device",
            "history_max", "clipboard_enabled", "clipboard_max", "autostart",
            "vocabulary_terms", "vocabulary", "correction_learning_enabled",
            "correction_learning_auto_detect", "ui_effects", "resource_saver",
            # ITEM 4: Foreign is an independent, opt-in island toggle (hidden by
            # default); island_modes lets the user pick which processing modes the
            # island can switch between (Prompt/Email).
            "island_foreign_toggle", "foreign_mode", "foreign_languages",
            "island_modes", "deck_pinned",
            # Reader (document-to-audio) voice selection
            "reader_tts_provider", "reader_tts_model", "reader_voice",
            # Automatic model selection + language scope
            "english_only", "primary_language",
            # Engine configuration
            "compute_type", "device", "cpu_threads",
            # On-device LLM (fully-offline smart-mode shaping)
            "local_only_mode", "local_llm_enabled", "local_llm_model",
            # Cloud sync (Supabase)
            "supabase_url", "supabase_anon_key",
            "sync_settings", "sync_stats", "sync_history", "sync_reader",
            "sync_favorites", "sync_presets",
            # Meetings
            "meeting_processing_mode",
        ]
        result = {k: self.settings.get(k) for k in keys}
        result["_route_state"] = self._settings_route_state()
        # ITEM 7 (owner 2026-06-29): API keys must NOT leak to the JS bridge by
        # default, but the old redaction to the literal "[saved]" loaded that
        # 7-char token straight into the password field — so the key LOOKED
        # truncated, could not be inspected, and re-saving it overwrote the real
        # key with "[saved]". Now we send a non-secret PREVIEW (bullets + last 4)
        # so the user can VERIFY which key is stored; the real bytes are returned
        # only on an explicit reveal_setting() (the eye toggle), and set_setting
        # refuses to persist anything containing a bullet (the mask).
        for k in result:
            if str(k).endswith("_api_key") and result[k]:
                result[k] = _mask_api_key(result[k])
        return result

    def list_microphones(self):
        out = [{"index": -1, "name": "System default"}]
        try:
            import sounddevice as sd
            for i, d in enumerate(sd.query_devices()):
                if (d.get("max_input_channels", 0) or 0) > 0:
                    out.append({"index": i,
                                "name": d.get("name", f"Device {i}")})
        except Exception as e:
            print("mic list failed:", e)
        return out

    # ---- Cloud / Supabase auth bridge -----------------------------------

    def _get_sync_manager(self):
        """Lazy-init the shared SyncManager so background sync state persists
        across Api calls (sign-in starts it, sign-out stops it)."""
        if self._sync_manager is None:
            import cloud_sync
            self._sync_manager = cloud_sync.SyncManager(self.settings)
        return self._sync_manager

    def cloud_sign_up(self, email, password):
        """Register a new Supabase account."""
        try:
            import cloud_sync
            cs = cloud_sync.CloudSync(self.settings)
            return cs.sign_up(email, password)
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def cloud_sign_in(self, email, password):
        """Sign in to an existing Supabase account. On success, auto-start
        background sync so data flows without manual intervention."""
        try:
            import cloud_sync
            cs = cloud_sync.CloudSync(self.settings)
            r = cs.sign_in(email, password)
            if r.get("ok"):
                # Auto-start background sync after sign-in
                try:
                    sm = self._get_sync_manager()
                    sm.start_background_sync()
                except Exception:
                    pass  # bg sync is best-effort; auth mustn't fail
            return r
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def cloud_sign_out(self):
        """Sign out and clear the session. Stops background sync first."""
        try:
            # Stop background sync before sign-out
            if self._sync_manager is not None:
                try:
                    self._sync_manager.stop_background_sync()
                except Exception:
                    pass
            import cloud_sync
            cs = cloud_sync.CloudSync(self.settings)
            r = cs.sign_out()
            # Clean up the shared manager so a fresh one is created on next sign-in
            self._sync_manager = None
            return r
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def cloud_session(self):
        """Return the current session or None."""
        try:
            import cloud_sync
            cs = cloud_sync.CloudSync(self.settings)
            s = cs.get_session()
            return s or {"ok": False, "signed_in": False}
        except Exception as e:
            return {"ok": False, "signed_in": False, "message": str(e)}

    def cloud_configure(self, url, anon_key):
        """Save Supabase project credentials."""
        try:
            import cloud_sync
            cs = cloud_sync.CloudSync(self.settings)
            return cs.configure(url, anon_key)
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # ---- Cloud sync data operations -----------------------------------

    def cloud_sync_status(self):
        """Return per-data-type sync status (last_sync, errors, toggles)."""
        try:
            import cloud_sync
            sm = self._get_sync_manager()
            status = sm.get_sync_status()
            # Add background sync running flag
            status["bg_running"] = (
                sm._bg_thread is not None and sm._bg_thread.is_alive())
            return status
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def cloud_sync_type(self, data_type):
        """Sync one data type (pull + push)."""
        try:
            import cloud_sync
            sm = self._get_sync_manager()
            return sm.sync_type(str(data_type))
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def cloud_sync_all(self):
        """Sync all data types."""
        try:
            import cloud_sync
            sm = self._get_sync_manager()
            return sm.sync_all()
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def cloud_sync_start_bg(self, interval=30):
        """Start background sync (auto-sync every *interval* seconds).
        Returns {"ok": True, "interval": N} or {"ok": False, ...}."""
        try:
            sm = self._get_sync_manager()
            return sm.start_background_sync(interval)
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def cloud_sync_stop_bg(self):
        """Stop the background sync thread if running."""
        try:
            if self._sync_manager is not None:
                return self._sync_manager.stop_background_sync()
            return {"ok": True, "message": "No background sync running."}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def get_favorites(self):
        """Newest-first starred items (the ★ store) — text, source, original
        Smart Mode, when added. `mode` lets a favourite keep its identity."""
        return [
            {"text": e.get("text", ""), "source": e.get("source", ""),
             "mode": e.get("mode", ""),
             "time": e.get("time", ""), "added": e.get("added", ""), "fav": True}
            for e in self._favs().recent()
        ]

    def get_insights(self):
        """Real, derived statistics — every number computed from stats.json.
        Nothing here is invented: no accuracy %, no app attribution, no
        per-word confidence (none of that data exists, by design)."""
        import datetime
        st = self._stats()
        s = st.summary()
        days = (st.data or {}).get("days", {}) or {}
        active = [(d, v[0]) for d, v in days.items()
                  if isinstance(v, (list, tuple)) and len(v) >= 1 and (v[0] or 0) > 0]
        total_words = s.get("total_words", 0)
        busiest = max(active, key=lambda x: x[1]) if active else (None, 0)
        # words aggregated by weekday across ALL recorded days (Mon..Sun)
        wd = [0] * 7
        for d, w in active:
            try:
                wd[datetime.date.fromisoformat(d).weekday()] += int(w or 0)
            except Exception:
                pass
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        peak_wd = names[wd.index(max(wd))] if any(wd) else None
        # 7-day trend: this week's words vs the previous 7 days
        recent = st.daily_stats(14)
        this7 = sum(w for (_, w, _) in recent[-7:])
        prev7 = sum(w for (_, w, _) in recent[:-7])
        # New-user honesty: with no previous period there is no trend — return
        # None and let the UI say "not enough history yet", never "+100%".
        trend = round((this7 - prev7) / prev7 * 100) if prev7 > 0 else None
        # today vs yesterday + 30-day vs previous 30-day (same honesty rule)
        recent60 = st.daily_stats(60)
        yesterday = recent60[-2][1] if len(recent60) >= 2 else 0
        today = recent60[-1][1] if recent60 else 0
        today_delta = (round((today - yesterday) / yesterday * 100)
                       if yesterday > 0 else None)
        month_this = sum(w for (_, w, _) in recent60[-30:])
        month_prev = sum(w for (_, w, _) in recent60[:-30])
        month_trend = (round((month_this - month_prev) / month_prev * 100)
                       if month_prev > 0 else None)
        # "When you dictate" — the time-of-day insight that replaces the streak card.
        try:
            tod = st.time_of_day()
        except Exception:
            tod = {"hours": [0] * 24, "blocks": [0, 0, 0, 0],
                   "peak_hour": None, "peak_label": "", "peak_share": 0}
        return {
            "tod_hours": tod.get("hours", [0] * 24),
            "tod_blocks": tod.get("blocks", [0, 0, 0, 0]),
            "tod_peak_hour": tod.get("peak_hour"),
            "tod_peak_label": tod.get("peak_label", ""),
            "tod_peak_share": tod.get("peak_share", 0),
            "active_days": len(active),
            "avg_per_active_day": (round(total_words / len(active))
                                   if active else 0),
            "busiest_day": busiest[0], "busiest_words": busiest[1],
            "weekday_words": wd, "weekday_names": names, "peak_weekday": peak_wd,
            "this_week": this7, "prev_week": prev7, "trend_pct": trend,
            "yesterday_words": yesterday, "today_delta_pct": today_delta,
            "month_this": month_this, "month_prev": month_prev,
            "month_trend_pct": month_trend,
            "avg_wpm": s.get("avg_wpm", 0), "best_wpm": s.get("best_wpm", 0),
            "spoken_minutes": round(s.get("spoken_minutes", 0.0), 1),
            "today_words": s.get("today_words", 0),
        }

    def get_stats_dashboard(self):
        """One availability-aware snapshot for Stats, Reader, and Meetings."""
        try:
            store = self._stats()
            if store.load_health == "corrupt":
                dictation = {"available": False, "health": "corrupt",
                              "has_activity": None}
                reader = {"available": False, "health": "corrupt",
                          "has_activity": None}
            else:
                snapshot = store.dashboard_snapshot(98)
                summary = snapshot.get("summary") or {}
                dictation = {
                    "available": True,
                    "health": snapshot.get("health", "ok"),
                    "has_activity": bool(summary.get("total_transcripts", 0)),
                    "summary": summary,
                    "current_streak": snapshot.get("current_streak", 0),
                    "best_streak": snapshot.get("best_streak", 0),
                    "daily": [{"day": day, "words": words,
                               "transcripts": count}
                              for day, words, count in snapshot.get("daily", [])],
                    "modes": [{"mode": mode, "count": count, "words": words}
                              for mode, count, words in snapshot.get("modes", [])],
                }
                reader_summary = snapshot.get("reader") or {}
                reader = {"available": True,
                          "health": snapshot.get("health", "ok"),
                          "has_activity": bool(reader_summary.get(
                              "total_sessions", 0)),
                          "scope": "tracked_playback", **reader_summary}
        except Exception as exc:
            print("get_stats_dashboard stats failed:", exc)
            dictation = {"available": False, "health": "error",
                          "has_activity": None}
            reader = {"available": False, "health": "error",
                      "has_activity": None}
        try:
            import meeting_store
            meetings = meeting_store.activity_summary()
        except Exception as exc:
            print("get_stats_dashboard meetings failed:", exc)
            meetings = {"available": False, "health": "error",
                        "has_activity": None}
        return {"ok": True, "generated_at": time.time(),
                "dictation": dictation, "reader": reader,
                "meetings": meetings}

    def get_clip_thumb(self, path):
        """Tiny base64 PNG for a clipboard image row (the webview can't read
        %APPDATA% file paths directly). Returns '' on any failure."""
        try:
            # Only images managed by Clipboard may cross the JS bridge. Resolve
            # symlinks before containment checks so a link below DATA_DIR cannot
            # expose an arbitrary image elsewhere on disk.
            root = os.path.normcase(os.path.realpath(os.path.join(
                branding.DATA_DIR, "clip_images")))
            resolved = (os.path.normcase(os.path.realpath(path))
                        if isinstance(path, str) else "")
            try:
                contained = os.path.commonpath([root, resolved]) == root
            except (ValueError, TypeError):
                contained = False
            if not (contained and os.path.isfile(resolved)):
                return ""
            import base64
            import io as _io
            from PIL import Image
            with Image.open(resolved) as img:
                img.thumbnail((112, 112))
                buf = _io.BytesIO()
                img.convert("RGB").save(buf, "PNG")
            return ("data:image/png;base64,"
                    + base64.b64encode(buf.getvalue()).decode("ascii"))
        except Exception as e:
            print("thumb failed:", e)
            return ""

    def check_updates(self):
        """Honest update check via update.check_for_update — distinguishes
        'up to date' from 'couldn't check' (the feed may not be live yet)."""
        try:
            import update
            if not update.update_channel_enabled():
                return {"status": "disabled",
                        "message": ("Signed updates are unavailable until the "
                                    "publisher feed is configured.")}
            available, manifest = update.check_for_update()
            if available:
                return {"status": "available",
                        "version": manifest.get("version", "?"),
                        "notes": manifest.get("notes", "")}
            if manifest is None:
                return {"status": "failed",
                        "message": ("Couldn't check for updates right now — "
                                    "you're on v" + branding.VERSION + ".")}
            return {"status": "uptodate",
                    "message": (("Signed update checks are not available yet. "
                                 "You're on v" + branding.VERSION + ".")
                                if not update.update_channel_enabled() else
                                "You're on the latest version (v"
                                + branding.VERSION + ").")}
        except Exception as e:
            return {"status": "failed", "message": f"Update check failed: {e}"}

    def reset_window_size(self):
        """Snap the window back to the design-standard size (scaled to fit)."""
        try:
            win = getattr(self, "_window", None)
            if win is not None:
                win.resize(self._design_size[0], self._design_size[1])
                return True
        except Exception as e:
            print("reset_window_size failed:", e)
        return False

    def copy_text(self, text):
        """Reliable clipboard fallback for WebViews without Clipboard API access."""
        try:
            import pyperclip
            pyperclip.copy(str(text or ""))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # ======================================================================
    #  WRITE
    # ======================================================================
    def save_vocabulary(self, terms, pairs, baseline_terms=None,
                        baseline_pairs=None):
        """Save both vocabulary collections as one conflict-aware transaction."""
        try:
            desired_terms = [
                str(term).strip() for term in (
                    terms if isinstance(terms, (list, tuple)) else [])
                if str(term).strip()
            ]
            desired_pairs = {
                str(source).strip(): str(target).strip()
                for source, target in (
                    pairs.items() if isinstance(pairs, dict) else ())
                if str(source).strip() and str(target).strip()
            }
            has_baseline = (
                isinstance(baseline_terms, (list, tuple))
                and isinstance(baseline_pairs, dict)
            )
            old_terms = [
                str(term).strip() for term in (baseline_terms or [])
                if str(term).strip()
            ]
            old_pairs = {
                str(source).strip(): str(target).strip()
                for source, target in (baseline_pairs or {}).items()
                if str(source).strip() and str(target).strip()
            }

            def merge(latest_pairs, latest_terms):
                if not has_baseline:
                    latest_pairs.clear()
                    latest_pairs.update(desired_pairs)
                    latest_terms[:] = desired_terms
                else:
                    old_term_set = set(old_terms)
                    latest_term_set = set(latest_terms)
                    desired_term_set = set(desired_terms)
                    merged_terms = [
                        term for term in desired_terms
                        if term not in old_term_set or term in latest_term_set
                    ]
                    for term in latest_terms:
                        if (term not in old_term_set
                                and term not in desired_term_set
                                and term not in merged_terms):
                            merged_terms.append(term)

                    merged_pairs = {}
                    keys = list(dict.fromkeys([
                        *old_pairs.keys(), *desired_pairs.keys(),
                        *latest_pairs.keys(),
                    ]))
                    for item_key in keys:
                        old_has = item_key in old_pairs
                        desired_has = item_key in desired_pairs
                        user_changed = (
                            old_has != desired_has
                            or (old_has and desired_has
                                and old_pairs[item_key] != desired_pairs[item_key])
                        )
                        if user_changed:
                            if desired_has:
                                merged_pairs[item_key] = desired_pairs[item_key]
                        elif item_key in latest_pairs:
                            merged_pairs[item_key] = latest_pairs[item_key]
                    latest_pairs.clear()
                    latest_pairs.update(merged_pairs)
                    latest_terms[:] = merged_terms
                return {
                    "vocabulary_terms": list(latest_terms),
                    "vocabulary": dict(latest_pairs),
                }

            saved = self.settings.atomic_vocabulary_update(merge)
            reply = _ctrl_send(
                {"cmd": "reload", "key": "vocabulary"}, timeout=0.8)
            return {
                "ok": True,
                "vocabulary_terms": list(saved["vocabulary_terms"]),
                "vocabulary": dict(saved["vocabulary"]),
                "applied": bool(reply and reply.get("ok")),
            }
        except Exception as e:
            print("save_vocabulary failed:", e)
            return {"ok": False, "message": str(e)}

    def set_setting(self, key, value):
        """Persist a setting, then tell the running controller to re-read the
        file and live-apply it (hotkeys re-register, a model change hot-swaps).
        Dotted keys (modes.prompt, prompt_prefs.tone) update one level inside
        the parent dict. Returns applied=True when a live controller took the
        change immediately — the UI words its confirmation off this."""
        try:
            root_key = str(key).split(".", 1)[0]
            if root_key not in DEFAULTS or root_key in REMOVED_SETTINGS:
                return {"ok": False, "message": "Unknown or retired setting."}
            if str(key) == "llm_provider":
                return self.activate_model_provider(value)
            # Validate a binding BEFORE persisting it (capture_binding returns a
            # raw spec without validating, so this is the real gate). An invalid
            # hotkey otherwise saves, fails to register in the controller, and the
            # UI still shows "active now" — a silent dead key.
            press_bindings = (
                "hotkey", "quick_paste_hotkey", "history_hotkey",
                "search_hotkey", "web_search_hotkey",
            )
            if key in press_bindings or key == "mode_key":
                try:
                    import bindings
                    value = bindings.normalize(value)
                    ok, msg = bindings.validate(value, hold=(key == "mode_key"))
                    if not ok:
                        return {"ok": False, "message": msg or "Invalid binding"}
                    if key in press_bindings:
                        self.settings.load()
                        current = {
                            "hotkey": self.settings.get("hotkey", "ctrl+windows"),
                            "quick_paste_hotkey": self.settings.get(
                                "quick_paste_hotkey", "ctrl+alt+v"),
                            "history_hotkey": self.settings.get(
                                "history_hotkey", "ctrl+alt+d"),
                            "search_hotkey": self.settings.get(
                                "search_hotkey", "ctrl+alt+f"),
                            "web_search_hotkey": self.settings.get(
                                "web_search_hotkey", "ctrl+alt+s"),
                        }
                        labels = {
                            "hotkey": "Dictate",
                            "quick_paste_hotkey": "Paste latest",
                            "history_hotkey": "Open Deck",
                            "search_hotkey": "Mumble Find",
                            "web_search_hotkey": "Web Search",
                        }
                        for other_key, other_value in current.items():
                            if (other_key != key and
                                    bindings.conflicts(value, other_value)):
                                return {"ok": False, "applied": False,
                                        "message": (
                                            "That shortcut is already used by "
                                            f"{labels[other_key]}. Mumble kept "
                                            "your previous shortcut."
                                        )}
                        live = _ctrl_send({
                            "cmd": "rebind", "key": str(key), "value": value,
                        }, timeout=2.0)
                        if live is None:
                            return {"ok": False, "applied": False,
                                    "message": (
                                        "Mumble couldn't confirm that shortcut. "
                                        "Your previous shortcut is still active."
                                    )}
                        if not live.get("ok"):
                            return {"ok": False, "applied": False,
                                    "message": live.get("message") or
                                    "The shortcut could not be registered."}
                        self.settings.load()
                        return {"ok": True, "value": value, "applied": True,
                                "message": live.get("message", "")}
                except Exception as exc:
                    return {"ok": False, "applied": False, "message": (
                        "Shortcut validation is unavailable. Your existing "
                        f"shortcut was kept. ({exc})")}
            # Trim API keys on save: a pasted key often carries a trailing newline/
            # space. openrouter_tts strips before use, but get_openrouter_credits and
            # the has_key check do not — so an untrimmed key would show "connected"
            # and let TTS work while the credits readout fails with a confusing auth
            # error. Normalize here so every consumer agrees.
            if isinstance(value, str) and str(key).endswith("_api_key"):
                # ITEM 7: never overwrite a real key with the masked PREVIEW the UI
                # shows for an already-saved key (it contains bullets), nor with the
                # legacy "[saved]" sentinel. An unedited masked field saving itself
                # back was the silent key-corruption path — treat it as a no-op.
                if _is_masked_api_key(value):
                    return {"ok": True, "value": "[saved]",
                            "applied": False, "masked": True}
                value = value.strip()
            if "." in str(key):
                parent, child = str(key).split(".", 1)
                d = dict(self.settings.get(parent, {}) or {})
                d[child] = value
                saved = self.settings.set(parent, d)
            else:
                saved = self.settings.set(key, value)
            if saved is False:
                return {"ok": False, "message": "Couldn't save settings."}
            r = _ctrl_send({"cmd": "reload", "key": str(key)}, timeout=0.8)
            return {"ok": True, "value": value,
                    "applied": bool(r and r.get("ok"))}
        except Exception as e:
            print("set_setting failed:", e)
            return {"ok": False, "message": str(e)}

    def toggle_favorite(self, text, source="clipboard", when="", mode=""):
        try:
            store = self._favs()
            now = store.toggle(text, source, when, mode)
            if now is None:
                return {"ok": False, "fav": store.is_fav(text),
                        "message": "Couldn't save favourites."}
            return {"ok": True, "fav": bool(now)}
        except Exception as e:
            print("toggle_favorite failed:", e)
            return {"ok": False, "fav": False, "message": str(e)}

    def clear_transcripts(self):
        try:
            ok = self._history().clear()
            return {"ok": bool(ok), "message": (
                "" if ok else "Couldn't clear all transcript files.")}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def delete_transcript(self, stamp, text=""):
        # Delete by STABLE identity (stamp [+ text]), not array index — a stale
        # index removed the wrong / a resurrected entry when the list shifted.
        try:
            return {"ok": bool(
                self._history().delete_match(stamp, text or None))}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def clear_clipboard(self):
        try:
            ok = self._clipboard().clear()
            return {"ok": bool(ok), "message": (
                "" if ok else "Couldn't clear clipboard history.")}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def delete_clip(self, stamp, text=""):
        # Identity-based (stamp [+ text]); images pass text="" → matched by stamp.
        try:
            return {"ok": bool(
                self._clipboard().delete_match(stamp, text or None))}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def clear_prompts(self):
        try:
            # Prefer the controller so every open view refreshes together.
            reply = _ctrl_send({"cmd": "clear_prompts"}, timeout=2.0)
            if isinstance(reply, dict):
                return reply
            ok = self._prompts().clear_history()
            return {"ok": bool(ok), "message": (
                "" if ok else "Couldn't clear prompt history.")}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def save_presets(self, rows):
        try:
            base = len(presets_mod.BUILTIN)
            valid_slots = set(
                getattr(presets_mod, "CUSTOM_SLOTS", range(base + 1, base + 6)))
            custom = {}
            for i, r in enumerate(rows or []):
                title = (r.get("title") or "").strip()
                instr = (r.get("instruction") or "").strip()
                if not (title and instr):
                    continue
                # Honour the slot the client assigned to each row, so a preset
                # typed into "Custom 3" while 1-2 are empty stays in slot 3
                # instead of being re-packed to "Custom 1". (The client filters
                # out empty rows but keeps each survivor's real slot; re-deriving
                # the slot from the enumerate index here collapsed the gaps.)
                try:
                    slot = int(r.get("slot"))
                except (TypeError, ValueError):
                    slot = base + 1 + i
                if slot not in valid_slots:
                    slot = base + 1 + i
                custom[slot] = {
                    "title": title,
                    "description": (r.get("description") or "").strip(),
                    "instruction": instr,
                }
            baseline = self._preset_snapshot
            if baseline is None:
                # Non-UI callers may save without rendering the form first.
                baseline = presets_mod.load_custom()
            upserts = {
                slot: value for slot, value in custom.items()
                if baseline.get(slot) != value
            }
            remove_slots = set(baseline).difference(custom)
            saved = presets_mod.mutate_custom(upserts, remove_slots)
            if saved:
                # Keep the baseline aligned with what this browser form has
                # actually seen. Reloading here could include an unseen cloud
                # addition; a second save from the still-stale form would then
                # misinterpret that addition as a user-requested deletion.
                self._preset_snapshot = {
                    slot: dict(value)
                    for slot, value in custom.items()
                }
            return {"ok": bool(saved), "count": len(custom), "message": (
                "" if saved else "Couldn't save custom presets.")}
        except Exception as e:
            print("save_presets failed:", e)
            return {"ok": False, "message": str(e)}

    def test_key(self, provider, key):
        """Save the key to the right provider setting, then validate it."""
        import ai
        provider = (provider or "cerebras").lower()
        info = ai.PROVIDERS.get(provider)
        key = (key or "").strip()
        # get_settings() deliberately exposes only a masked preview. The
        # "Save & test" buttons pass the visible field value, so an unchanged
        # saved key arrives here as bullets. Resolve that preview back to the
        # stored secret instead of overwriting the secret with its own mask.
        if _is_masked_api_key(key):
            key = ((self.settings.get(info.get("key_setting"), "") or "").strip()
                   if info and info.get("key_setting") else "")
            if not key or _is_masked_api_key(key):
                return {"ok": False, "message": "Enter an API key to test."}
        try:
            if info and key:
                self.settings.set(info["key_setting"], key)
        except Exception:
            pass
        try:
            if provider == "cerebras":
                ok, msg = ai.cerebras_test(key)
                return {"ok": bool(ok), "message": msg}
            if provider == "local":
                return {"ok": True,
                        "message": "Saved — local server endpoint set."}
            if provider == "anthropic":
                res = ai.key_ok(key, models_url=ai.ANTHROPIC_MODELS_URL)
            elif info:
                murl = info["url"].replace("/chat/completions", "/models")
                res = ai.key_ok(key, models_url=murl)
            else:
                res = None
            if res is True:
                return {"ok": True, "message": "Connected — key is valid."}
            if res is False:
                return {"ok": False, "message": "That key was rejected."}
            return {"ok": False,
                    "message": "Couldn't reach the provider — check your connection."}
        except Exception as e:
            return {"ok": False, "message": f"Test failed: {e}"}

    def test_mic(self, index):
        try:
            import numpy as np
            import sounddevice as sd
            idx = None if index in (None, "", -1, "-1") else int(index)
            dur, sr = 0.7, 16000
            rec = sd.rec(int(dur * sr), samplerate=sr, channels=1,
                         dtype="float32", device=idx)
            sd.wait()
            level = float(np.sqrt(np.mean(np.square(rec))) or 0.0)
            if level > 0.0008:
                return {"ok": True,
                        "message": f"Microphone OK — level detected ({level:.3f})."}
            return {"ok": True,
                    "message": "Mic opened — speak to see the level move."}
        except Exception as e:
            return {"ok": False,
                    "message": f"Couldn't open that microphone ({e})."}

    def test_transcription(self, provider=None, seconds=3):
        """Record a short clip from the chosen mic and run it through CLOUD
        transcription, returning the text + round-trip latency. Proves the key,
        provider and model all work — and shows how fast it is. Persists the
        provider choice first so the test uses exactly what will be saved."""
        try:
            import numpy as np
            import sounddevice as sd
            import transcription as tx
            if provider:
                self.settings.set("cloud_transcription_provider",
                                  str(provider).strip().lower())
            prov = self.settings.get("cloud_transcription_provider",
                                     tx.DEFAULT_PROVIDER)
            if prov not in tx.PROVIDERS:
                return {"ok": False,
                        "message": "The saved transcription provider is unavailable."}
            info = tx.PROVIDERS[prov]
            if not (self.settings.get(info["key_setting"], "") or "").strip():
                return {"ok": False,
                        "message": f"Add your {prov} API key first, then test."}
            invocation_snapshot = processing_route.snapshot_inputs(
                self.settings, feature="dictation", lane="speech_to_text"
            )
            if not invocation_snapshot.route.ready:
                return {"ok": False,
                        "message": "Cloud transcription is not authorized by the current processing route."}
            idx = self.settings.get("mic_device", None)
            idx = None if idx in (None, "", -1, "-1") else int(idx)
            sr = 16000
            dur = max(1.0, min(8.0, float(seconds or 3)))
            rec = sd.rec(int(dur * sr), samplerate=sr, channels=1,
                         dtype="float32", device=idx)
            sd.wait()
            audio = np.asarray(rec, dtype="float32").flatten()
            import time as _t
            t0 = _t.time()
            text = tx.transcribe(audio, invocation_snapshot)
            dt = _t.time() - t0
            if not text:
                return {"ok": True,
                        "message": f"Connected to {prov} in {dt:.2f}s — but heard "
                                   f"nothing. Speak during the countdown and retry."}
            return {"ok": True,
                    "message": f"{prov} transcribed in {dt:.2f}s: “{text}”"}
        except Exception as e:
            return {"ok": False, "message": f"Cloud transcription failed: {e}"}

    def capture_binding(self, timeout=15):
        try:
            import bindings
            spec = bindings.capture(float(timeout))
            if not spec:
                return {"spec": "", "pretty": ""}
            return {"spec": spec, "pretty": bindings.pretty(spec)}
        except Exception as e:
            return {"spec": "", "pretty": "", "message": str(e)}

    def validate_binding(self, spec, hold=False):
        try:
            import bindings
            ok, msg = bindings.validate(spec, hold)
            return {"ok": bool(ok), "message": msg}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def pretty_binding(self, spec):
        try:
            import bindings
            return {"pretty": bindings.pretty(spec or "")}
        except Exception:
            return {"pretty": spec or ""}

    def run_deck_job(self, preset_slot=None, mode=None, items=None):
        """Run a Deck AI job FOR REAL through the controller: minimize this
        window (focus returns to the user's app), send the job over the
        command channel, and the controller's _run_deck_job does the rest —
        island building animation, retries, paste, history/stats. Falls back
        to an honest notice only when no controller is running."""
        kind_to_source = {"TEXT": "transcript", "CLIP": "clipboard",
                          "IMG": "clipboard", "PROMPT": "prompt"}
        payload = [
            {"source": kind_to_source.get((i or {}).get("kind", ""),
                                          "clipboard"),
             "time": (i or {}).get("time", ""),
             "text": (i or {}).get("text", "")}
            for i in (items or [])
        ]
        self._dismiss_for_paste()
        r = _ctrl_send({"cmd": "deck_job", "slot": preset_slot,
                        "mode": mode, "items": payload})
        if r and r.get("ok"):
            return {"ok": True, "live": True,
                    "message": "Working — the result will paste at your cursor."}
        return {
            "ok": False, "live": False,
            "message": ("Start Mumble (the tray app) to run Deck jobs — "
                        "this window alone can't paste at your cursor."),
        }

    def finish_onboarding(self, data=None):
        try:
            self.settings.set("first_run", False)
            if isinstance(data, dict):
                if "autostart" in data:
                    self.settings.set("autostart", bool(data["autostart"]))
                # Persist the chosen visual tier — MUST include "enhanced" (the
                # default the wizard pre-selects). The old list omitted it, so a
                # user who finished onboarding on Enhanced had it silently dropped
                # and fell back to whatever was stored before.
                if data.get("ui_effects") in ("enhanced", "standard", "lite"):
                    self.settings.set("ui_effects", data["ui_effects"])
                if "english_only" in data:
                    self.settings.set("english_only", bool(data["english_only"]))
            # ITEM 16: onboarding is over — restore the always-on-top Deck pin that
            # we deliberately suppressed during first-run (so API-key sites weren't
            # blocked). Honour the saved preference; default pinned (the Big Shift).
            try:
                if self.settings.get("deck_pinned", True):
                    _set_topmost(getattr(self, "_title", None), True)
            except Exception as e:
                print("post-onboarding pin restore skipped:", e)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def set_onboarding_mode(self, active=True):
        """ITEM 16 (owner 2026-06-29): while the onboarding wizard is on screen —
        first run OR a later 'Replay tour' — the window must NOT be always-on-top,
        so the user can freely switch to a browser, sign up for a provider, and
        copy an API key without Mumble blocking the page. The web UI calls this with
        active=True when the wizard opens and active=False when it closes; on close
        we re-pin honouring the saved Deck preference. finish_onboarding re-pins too
        as a backstop, so a wizard that ends via 'Finish' is always covered."""
        active = bool(active)
        try:
            if active:
                _set_topmost(getattr(self, "_title", None), False)
                _set_noactivate(getattr(self, "_title", None), False)
            elif self.settings.get("deck_pinned", True):
                _set_topmost(getattr(self, "_title", None), True)
        except Exception as e:
            print("set_onboarding_mode skipped:", e)
        return True

    def open_url(self, url):
        """Open an external link in the user's REAL browser (not the webview)."""
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            try:
                subprocess.Popen(
                    ["xdg-open", url], env=_external_app_env(),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception as e:
                # Run webbrowser in a child so its environment can be cleaned
                # without racing other threads by mutating os.environ globally.
                try:
                    subprocess.Popen(
                        [sys.executable, "-m", "webbrowser", "-t", url],
                        env=_external_app_env(), stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
                    return True
                except Exception:
                    print("open_url failed:", e)
        return False

    def request_web_search(self, text):
        """Ask the controller to prepare a local, consent-gated Web Search."""
        result = _ctrl_send({
            "cmd": "web_search_request", "text": str(text or ""),
        }, timeout=2.0)
        return result or {
            "ok": False,
            "message": "Mumble could not prepare Web Search.",
        }

    def confirm_web_search(self, request_id):
        result = _ctrl_send({
            "cmd": "web_search_confirm",
            "request_id": str(request_id or ""),
        }, timeout=2.0)
        return result or {
            "ok": False,
            "message": "Mumble could not confirm Web Search.",
        }

    def cancel_web_search(self, request_id):
        result = _ctrl_send({
            "cmd": "web_search_cancel",
            "request_id": str(request_id or ""),
        }, timeout=0.8)
        return result or {"ok": False}

    def open_data_folder(self):
        try:
            branding.ensure_dirs()
            subprocess.Popen(
                ["xdg-open", branding.DATA_DIR], env=_external_app_env(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print("open_data_folder failed:", e)
            return False

    def switch_to_lite(self):
        """A real SWITCH: Lite binds the same single-instance lock as the main
        controller, so launching it while Mumble runs used to die instantly
        (the 'Launch Lite does not behave correctly' bug). Ask the controller
        to quit, give the lock a beat to free, then start Lite."""
        try:
            bat = self._lite_bat()
            if not os.path.exists(bat):
                return False
            if self.controller_alive():
                _ctrl_send({"cmd": "quit"}, timeout=1.0)
                import time as _time
                _time.sleep(1.4)
            subprocess.Popen(
                ["xdg-open", bat], env=_external_app_env(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print("switch_to_lite failed:", e)
        return False

    def toggle_fullscreen(self):
        """F11 — pywebview's native fullscreen toggle on the main window."""
        try:
            win = getattr(self, "_window", None)
            if win is not None:
                win.toggle_fullscreen()
                return True
        except Exception as e:
            print("toggle_fullscreen failed:", e)
        return False

    def set_pinned(self, on=True):
        """Pin/unpin the Deck ON TOP of every other app — the always-open Deck
        workflow (the Big Shift makes this the DEFAULT). Pin = HWND_TOPMOST so the
        Deck floats above your other windows on every page. The *non-activating
        palette* behaviour (clicking the Deck never steals focus, so a highlighted
        selection survives for Capture, and the window never "takes over" when you
        click a text field elsewhere) is layered on top only while the Deck page is
        showing — see set_deck_palette, which the web UI calls as you move between
        pages / toggle the pin. Keeping NOACTIVATE off on other pages is what lets
        onboarding, Settings, and the Deck search still accept typing. PERSISTED so
        the Deck opens pinned on every launch. Returns True when applied."""
        on = bool(on)
        try:
            self.settings.set("deck_pinned", on)
        except Exception:
            pass
        ok = _set_topmost(getattr(self, "_title", None), on)
        if not on:
            # Un-pinning must also drop any palette (NOACTIVATE) state so the user
            # can immediately click into and type/search in the Deck again.
            _set_noactivate(getattr(self, "_title", None), False)
        return ok

    def set_deck_palette(self, on=True):
        """Enable/disable the Deck's non-activating *palette* behaviour. The web UI
        calls this with on = (pinned AND the Deck page is showing): only then should
        clicking the window be focus-neutral so a highlighted selection survives a
        Capture. On every other page (onboarding, Settings, Home) — and whenever the
        Deck is un-pinned — it's called with on=False so normal typing works. Pure
        window-state; the pin/topmost itself is handled by set_pinned. Returns True
        when applied."""
        return _set_noactivate(getattr(self, "_title", None), bool(on))

    def capture_selection(self):
        """Grab whatever text is highlighted in the user's CURRENT foreground app
        and hand it back so the Deck can drop it in as a captured selection. This is
        the "pick up highlighted text while the Deck is already open" path (the
        Capture button / re-pressing the Deck hotkey). It works precisely because a
        pinned Deck on its own page is non-activating (set_deck_palette): clicking
        Capture never steals focus, so the highlight stays live in Word/Chrome/etc.
        The controller owns the keyboard + clipboard, so the actual Ctrl+C grab runs
        THERE (cmd 'grab_selection'). Returns {'ok','selection','message'}; an empty
        selection is a normal, honest result (nothing was highlighted)."""
        r = _ctrl_send({"cmd": "grab_selection"}, timeout=2.0)
        if r is not None and r.get("ok"):
            return {"ok": True, "selection": r.get("selection") or ""}
        return {
            "ok": False, "selection": "",
            "message": ("Start Mumble (the tray app) to capture highlighted "
                        "text — this window alone can't read your selection."),
        }

    def capture_conversation(self):
        """ITEM 5: capture the WHOLE AI conversation in the user's focused chat
        (Select-All → Copy reaches the entire thread, even messages scrolled out of
        view) and store it as prompting context. The controller owns the keyboard +
        clipboard so the grab runs THERE (cmd 'capture_conversation'). Returns
        {'ok','turns','chars','message'}."""
        r = _ctrl_send({"cmd": "capture_conversation"}, timeout=4.0)
        if r is not None and r.get("ok"):
            return {"ok": True, "turns": r.get("turns", 0),
                    "chars": r.get("chars", 0),
                    "ui": r.get("ui", "")}
        return {
            "ok": False,
            "message": (r or {}).get("message")
            or ("Start Mumble (the tray app) to capture a conversation — this "
                "window alone can't read another app."),
        }

    def minimize_window(self):
        """Hide the window (re-using the ctrl+alt+d shortcut toggles History
        away again — owner §6a)."""
        try:
            win = getattr(self, "_window", None)
            if win is not None:
                win.minimize()
                return True
        except Exception as e:
            print("minimize_window failed:", e)
        return False

    def apply_shortcuts(self, opts=None):
        """Onboarding's discoverability step: create real, icon-bearing
        shortcuts. opts = {desktop: bool, start_menu: bool, autostart: bool}.
        (Taskbar pinning is intentionally absent — Windows blocks programmatic
        pinning; the UI shows a drag-to-pin hint instead.)"""
        opts = opts or {}
        done, failed = [], []
        try:
            import autostart
            if opts.get("desktop"):
                try:
                    if autostart.install_desktop_shortcut():
                        done.append("desktop")
                    else:
                        failed.append("desktop")
                except Exception as e:
                    print("desktop shortcut failed:", e); failed.append("desktop")
            if opts.get("start_menu"):
                try:
                    if autostart.install_start_menu():
                        done.append("start_menu")
                    else:
                        failed.append("start_menu")
                except Exception as e:
                    print("start menu failed:", e); failed.append("start_menu")
            if "autostart" in opts:
                try:
                    ok = (autostart.enable if opts.get("autostart")
                          else autostart.disable)()
                    (done if ok else failed).append("autostart")
                except Exception as e:
                    print("autostart failed:", e); failed.append("autostart")
        except Exception as e:
            return {"ok": False, "message": str(e)}
        return {"ok": not failed, "done": done, "failed": failed}


def _acquire_webui_lock():
    """Bind WEBUI_PORT as this window's single-instance lock + command
    listener socket. Returns the bound socket, or None if another web window
    already owns it (we tell IT to come to front and exit — ONE window,
    always)."""
    try:
        return branding.ipc_bind_server("webui", WEBUI_PORT, backlog=4)
    except OSError:
        return None
    except Exception as e:
        print("webui IPC lock unavailable:", e)
        return None


def _hwnd_for_title(title):
    if not sys.platform.startswith("win"):
        return 0
    try:
        import ctypes
        return ctypes.windll.user32.FindWindowW(None, title) or 0
    except Exception:
        return 0


def _webview_window_for_title(title):
    """Find the live pywebview window without depending on a Win32 HWND."""
    try:
        import webview
        for window in list(getattr(webview, "windows", ())):
            window_title = getattr(window, "title", None)
            if window_title is None:
                window_title = getattr(window, "_title", None)
            if not title or window_title == title:
                return window
    except Exception:
        pass
    return None


def _schedule_native_window(title, callback):
    """Run a native GTK window mutation on pywebview's GUI thread."""
    window = _webview_window_for_title(title)
    native = getattr(window, "native", None) if window is not None else None
    if native is None:
        return False

    def _apply():
        try:
            callback(native)
        except Exception as e:
            print("native window operation failed:", e)
        return False

    gui = getattr(window, "gui", None)
    glib = getattr(gui, "glib", None)
    if glib is not None and hasattr(glib, "idle_add"):
        glib.idle_add(_apply)
    else:
        _apply()
    return True


def _bring_to_front(title):
    """Front the window, guaranteed UN-maximized AND guaranteed focused. Every
    way of re-opening Mumble (taskbar, tray, second launch, History hotkey) must
    present the normal tall-column window — never a surprise fullscreen-feeling
    maximize left over from an earlier session (owner contract 2026-06-12), and
    never the "Windows just flashes the taskbar button" non-event.

    THE INTERMITTENT-FOCUS ROOT CAUSE (owner 2026-06-15). `SetForegroundWindow`
    is REFUSED by Windows when the calling process (this webui_shell host) does
    not own the foreground lock — and it usually doesn't, because the user was
    typing in some OTHER app when they hit ctrl+alt+d. A refused call doesn't
    raise; Windows silently flashes the taskbar button instead of activating the
    window (exactly the reported symptom). The previous build relied on a
    topmost-toggle + a bare SetForegroundWindow, which works only when the lock
    happens to be free — hence "usually restores, occasionally just flashes".

    The robust, deadlock-free fix used here, in order:
      1. SW_RESTORE / drop a stale maximize (un-minimize to the normal layout).
      2. Zero SPI_SETFOREGROUNDLOCKTIMEOUT for the duration — this lifts the
         foreground lock so SetForegroundWindow is HONOURED no matter which app
         the user was in (restored afterwards so we don't change their system).
      3. AllowSetForegroundWindow(ASFW_ANY) + topmost-toggle to jump the z-order
         (no AttachThreadInput — that can DEADLOCK on a non-pumping foreground
         app, which used to wedge this very command thread).
      4. SetForegroundWindow; if it STILL reports failure, SwitchToThisWindow
         (the Alt+Tab activator Explorer itself uses) then a single synthetic
         key-tap to register input ownership and one more SetForegroundWindow.
    The result: restore-if-min / show-if-hidden / raise-if-behind / focus-if-
    unfocused — every single press, no manual taskbar click ever needed."""
    if not sys.platform.startswith("win"):
        window = _webview_window_for_title(title)
        if window is None:
            return
        try:
            # pywebview's GTK restore path deiconifies and calls present(), the
            # portable foreground action wanted by this path.
            window.show()
            window.restore()
        except Exception as e:
            print("bring_to_front failed:", e)
        return
    try:
        import ctypes
        h = _hwnd_for_title(title)
        if not h:
            return
        u = ctypes.windll.user32
        u.ShowWindow(h, 9)   # SW_RESTORE — un-minimize first

        class _WP(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint),
                        ("flags", ctypes.c_uint),
                        ("showCmd", ctypes.c_uint),
                        ("ptMin", ctypes.c_long * 2),
                        ("ptMax", ctypes.c_long * 2),
                        ("rcNormal", ctypes.c_long * 4)]
        wp = _WP()
        wp.length = ctypes.sizeof(_WP)
        # SW_RESTORE on a window minimized WHILE maximized returns it to
        # maximized — drop that state explicitly (showCmd 3 = maximized).
        if u.GetWindowPlacement(h, ctypes.byref(wp)) and wp.showCmd == 3:
            u.ShowWindow(h, 1)   # SW_SHOWNORMAL

        # --- lift the foreground lock (the fix for the intermittent flash) -----
        SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
        SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
        SPIF_SENDCHANGE = 0x0002
        prev_timeout = ctypes.c_uint(0)
        got_timeout = False
        try:
            got_timeout = bool(u.SystemParametersInfoW(
                SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(prev_timeout), 0))
            # pvParam carries the new value as (PVOID)(UINT_PTR)0 → no lock delay.
            u.SystemParametersInfoW(
                SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0),
                SPIF_SENDCHANGE)
        except Exception:
            pass

        try:
            u.AllowSetForegroundWindow(-1)   # ASFW_ANY (best-effort)
        except Exception:
            pass
        from ctypes import wintypes
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.INT,
                                   wintypes.INT, wintypes.INT, wintypes.INT,
                                   wintypes.UINT]
        u.SetWindowPos.restype = wintypes.BOOL
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0040
        flags = SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW
        u.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, flags)
        u.SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        u.BringWindowToTop(h)

        ok = bool(u.SetForegroundWindow(h))
        if not ok or u.GetForegroundWindow() != h:
            # Last-resort activators. SwitchToThisWindow is the same call Alt+Tab
            # uses and succeeds where SetForegroundWindow is refused; the synthetic
            # key-tap makes THIS thread the last input source so the retried
            # SetForegroundWindow is granted. VK 0 (no real key) avoids side
            # effects like activating the foreground app's menu bar (plain ALT).
            try:
                u.SwitchToThisWindow(h, True)
            except Exception:
                pass
            try:
                KEYEVENTF_KEYUP = 0x0002
                u.keybd_event(0, 0, 0, 0)
                u.keybd_event(0, 0, KEYEVENTF_KEYUP, 0)
            except Exception:
                pass
            u.SetForegroundWindow(h)
        u.SetActiveWindow(h)

        # Restore the user's foreground-lock timeout so we leave the system as we
        # found it (we only needed it zeroed for the activation above).
        if got_timeout:
            try:
                u.SystemParametersInfoW(
                    SPI_SETFOREGROUNDLOCKTIMEOUT, 0,
                    ctypes.c_void_p(prev_timeout.value), SPIF_SENDCHANGE)
            except Exception:
                pass
    except Exception as e:
        print("bring_to_front failed:", e)


def _set_topmost(title, on):
    """SetWindowPos HWND_TOPMOST/NOTOPMOST on the window (found by title) — the
    'pin History on top' mechanism (owner v4). No thread-input attach, so it
    can't deadlock; the pin persists until explicitly toggled off."""
    if not sys.platform.startswith("win"):
        window = _webview_window_for_title(title)
        if window is None:
            return False
        try:
            # Public pywebview API; GTK dispatches this to set_keep_above.
            window.on_top = bool(on)
            return True
        except Exception as e:
            print("set_topmost failed:", e)
            return False
    try:
        import ctypes
        h = _hwnd_for_title(title) if title else 0
        if not h:
            return False
        u = ctypes.windll.user32
        from ctypes import wintypes
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.INT,
                                   wintypes.INT, wintypes.INT, wintypes.INT,
                                   wintypes.UINT]
        u.SetWindowPos.restype = wintypes.BOOL
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        u.SetWindowPos(h, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
                       SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        return True
    except Exception as e:
        print("set_topmost failed:", e)
        return False


_NOACTIVATE_STATE = {}


def _set_noactivate(title, on):
    """Toggle WS_EX_NOACTIVATE on the window — the Deck's *palette* behaviour. ON:
    clicking the window (tick an item, run a preset, Capture a selection) NEVER
    activates it, so keyboard focus + the live text selection stay in whatever app
    you were using. That is what makes "highlight → Capture" work and stops the Deck
    from taking over when you click a text field elsewhere. OFF: a normal window
    that activates on click (so you can type into onboarding, Settings, or the Deck
    search) — we activate it once on the way out. Applied ONLY while the Deck page is
    showing AND pinned (driven by the web UI via set_deck_palette), so it never
    blocks typing on other pages. Mirrors the island bar's NOACTIVATE setup
    (overlay.py); no thread-input attach, so it can't deadlock. Returns True when
    applied."""
    if not sys.platform.startswith("win"):
        on = bool(on)
        window = _webview_window_for_title(title)
        native = getattr(window, "native", None) if window is not None else None
        if (native is None or not hasattr(native, "set_accept_focus")
                or not hasattr(native, "set_focus_on_map")):
            return False
        was_on = bool(_NOACTIVATE_STATE.get(title, False))
        _NOACTIVATE_STATE[title] = on

        def _apply(native_window):
            native_window.set_accept_focus(not on)
            native_window.set_focus_on_map(not on)
            if was_on and not on and hasattr(native_window, "present"):
                native_window.present()

        return _schedule_native_window(title, _apply)
    try:
        import ctypes
        h = _hwnd_for_title(title) if title else 0
        if not h:
            return False
        u = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        ex = u.GetWindowLongW(h, GWL_EXSTYLE)
        was_set = bool(ex & WS_EX_NOACTIVATE)
        new_ex = (ex | WS_EX_NOACTIVATE) if on else (ex & ~WS_EX_NOACTIVATE)
        if new_ex != ex:
            u.SetWindowLongW(h, GWL_EXSTYLE, new_ex)
        # Activate ONLY when we actually LEFT palette mode (the flag was set and is
        # now cleared) so the window can take typing again. The web UI calls this
        # with on=False on every ordinary page change, so a blanket
        # SetForegroundWindow here would needlessly re-assert foreground each time
        # you switch tabs — guard it on the real transition.
        if (not on) and was_set:
            try:
                u.SetForegroundWindow(h)
            except Exception:
                pass
        return True
    except Exception as e:
        print("set_noactivate failed:", e)
        return False


def _show_palette_front(title):
    """Surface the Deck WITHOUT stealing focus: un-minimize/show no-activate and
    re-assert HWND_TOPMOST, so the Deck comes up OVER your work but the app you're in
    keeps keyboard focus + its live text selection (and a highlighted selection is
    still grabbable by Capture / the hotkey). Used instead of the foreground-stealing
    _bring_to_front whenever the Deck is pinned. Whether clicking the window is
    focus-neutral (WS_EX_NOACTIVATE) is layered on separately by the Deck view via
    set_deck_palette."""
    if not sys.platform.startswith("win"):
        # GTK restore() calls present() and steals focus. Deiconify the native
        # window with focus-on-map disabled so another app's selection survives.
        def _show_without_focus(native_window):
            if hasattr(native_window, "set_focus_on_map"):
                native_window.set_focus_on_map(False)
            if hasattr(native_window, "unmaximize"):
                native_window.unmaximize()
            if hasattr(native_window, "deiconify"):
                native_window.deiconify()
            if hasattr(native_window, "show_all"):
                native_window.show_all()

        _schedule_native_window(title, _show_without_focus)
        _set_topmost(title, True)
        return
    try:
        import ctypes
        h = _hwnd_for_title(title) if title else 0
        if not h:
            return
        u = ctypes.windll.user32
        SW_SHOWNOACTIVATE = 4
        # Drop a stale MAXIMIZED/fullscreen state WITHOUT stealing focus. A window
        # minimized while maximized — or surfaced from the taskbar — otherwise
        # restores straight back to maximized (the "Mumble opens full-screen" bug).
        # _bring_to_front already guards the unpinned path; the pinned palette path
        # (the default) needs the same guard. SetWindowPlacement with
        # showCmd=SW_SHOWNOACTIVATE returns the window to its normal (design) size
        # and never activates, so the palette stays focus-neutral.
        class _WP(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint),
                        ("showCmd", ctypes.c_uint), ("ptMin", ctypes.c_long * 2),
                        ("ptMax", ctypes.c_long * 2), ("rcNormal", ctypes.c_long * 4)]
        wp = _WP()
        wp.length = ctypes.sizeof(_WP)
        if u.GetWindowPlacement(h, ctypes.byref(wp)) and wp.showCmd == 3:  # maximized
            wp.showCmd = SW_SHOWNOACTIVATE
            u.SetWindowPlacement(h, ctypes.byref(wp))
        else:
            u.ShowWindow(h, SW_SHOWNOACTIVATE)   # restore/show, never steal focus
        _set_topmost(title, True)            # keep it above other apps
    except Exception as e:
        print("show_palette_front failed:", e)


def _apply_mumble_icon(title):
    """Replace the default Python window/taskbar icon with the real Mumble
    icon (pywebview has no icon API on Windows — set it via WM_SETICON)."""
    if not sys.platform.startswith("win"):
        icon = getattr(branding, "ICON_PNG", "")
        if not icon or not os.path.isfile(icon):
            return

        def _set_icon(native_window):
            if hasattr(native_window, "set_icon_from_file"):
                native_window.set_icon_from_file(icon)

        _schedule_native_window(title, _set_icon)
        return
    try:
        import ctypes
        h = _hwnd_for_title(title)
        if not h or not os.path.exists(branding.ICON_ICO):
            return
        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
        for size, which in ((16, 0), (32, 1)):  # ICON_SMALL, ICON_BIG
            hicon = ctypes.windll.user32.LoadImageW(
                None, branding.ICON_ICO, IMAGE_ICON, size, size,
                LR_LOADFROMFILE)
            if hicon:
                ctypes.windll.user32.SendMessageW(h, 0x0080, which, hicon)
    except Exception as e:
        print("icon apply failed:", e)


def _serve_webui_commands(srv, H, ensure_main, ensure_search, title):
    """Handle controller→webui commands on the lock socket.
      'show' → front the main app window; 'history'/'deck' → main window History
          tab (in-app browsing); 'refresh' → silently re-render the open page.

    `H` is the shared window holder ({"main","main_min",...}) so this loop always
    sees the CURRENT (possibly lazily-created) main window, and routes a refresh
    ONLY when it is actually on screen — a minimized main window does no work
    (saves the render; owner v6)."""

    def _loop():
        while True:
            try:
                conn, _ = srv.accept()
            except Exception:
                return  # listening socket closed / unusable — end the listener
            try:
                with conn:
                    if not branding.ipc_peer_is_current_user(conn):
                        print("webui command rejected (foreign peer)")
                        continue
                    conn.settimeout(2)
                    # Read until the newline frame (senders append "\n"). A single
                    # recv(4096) assumed one packet == one whole message; a split
                    # or >4KB command was truncated → a parse error that the old
                    # `except: return` turned into "the whole listener dies", so
                    # refresh/show/flyout stopped working for the rest of the run.
                    data = b""
                    while not data.endswith(b"\n") and len(data) < 65536:
                        chunk = conn.recv(8192)
                        if not chunk:
                            break
                        data += chunk
                req = json.loads(data.decode("utf-8", "replace") or "{}")
                if not _webui_token_ok(req):
                    print("webui command rejected (bad token)")
                    continue
                cmd = req.get("cmd", "")
                if cmd == "refresh":
                    what = json.dumps(str(req.get("what") or ""))  # always a JS string literal
                    w = H.get("main")
                    if w is not None and not H.get("main_min"):
                        try:
                            w.evaluate_js(
                                f"window.pyRefresh && window.pyRefresh({what})")
                        except Exception:
                            pass
                elif cmd == "web_search_consent":
                    win = ensure_main()
                    if win is not None:
                        payload = json.dumps({
                            key: req.get(key) for key in (
                                "request_id", "provider", "query", "privacy",
                            )
                        })
                        try:
                            win.evaluate_js(
                                "window.pyWebSearchConsent && "
                                f"window.pyWebSearchConsent({payload})"
                            )
                        except Exception as exc:
                            print("Web Search consent eval failed:", exc)
                elif cmd == "system_search":
                    ensure_search(req.get("operation_id"))
                elif cmd in ("history", "deck", "show"):
                    win = ensure_main()   # lazily create main if needed
                    if win is not None and cmd in ("history", "deck"):
                        # A selection the controller grabbed (text highlighted
                        # when the Deck hotkey fired) rides along so it can land
                        # as a top Deck item. json.dumps → a safe JS string
                        # literal (handles quotes/newlines/unicode); empty when
                        # there was no selection.
                        sel = json.dumps(req.get("selection") or "")
                        try:
                            win.evaluate_js(f"openHistory(true, {sel})")
                        except Exception as e:
                            print("openHistory eval failed:", e)
            except Exception as e:
                # A bad/partial message must NOT kill the listener — skip it and
                # keep serving (the old `except: return` stopped ALL later commands).
                print("webui command error:", e)
                continue
    threading.Thread(target=_loop, daemon=True).start()


def main():
    # ONE web process, ever: a second launch (tray double-click race, second
    # shortcut press) just brings the existing window forward and exits.
    lock = _acquire_webui_lock()
    if lock is None:
        # Already running — bring the existing main window forward, then exit.
        try:
            with branding.ipc_connect("webui", WEBUI_PORT, timeout=1.0) as s:
                payload = _webui_payload({"cmd": "show"})
                s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        except Exception:
            pass
        return

    # Group with the controller under one taskbar identity ("Mumble", the
    # Mumble icon) instead of a generic Python entry.
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Mumble.App")
        except Exception:
            pass

    try:
        import webview
    except Exception as e:
        # The launcher runs under pythonw (no console), so a missing pywebview
        # or WebView2 runtime made double-click do NOTHING. Tell the user.
        if sys.platform.startswith("win"):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "The new UI needs the 'pywebview' package (and the Microsoft "
                    "Edge WebView2 runtime).\n\nRe-run Install Mumble.bat to set "
                    f"it up.\n\nDetail: {e}",
                    "Mumble — new UI", 0x10)
            except Exception:
                pass
        else:
            print(f"ERROR: pywebview unavailable — {e}", file=sys.stderr)
        raise

    html = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "webui", "index.html")
    # Window contract — the TALL COLUMN (restored 2026-06-29 after a square
    # 1000×840 regression). Mumble is a tall desktop window: ~960 wide, FILLING the
    # work-area height minus a breathing margin (desktop visible top + bottom, title
    # bar always grabbable), capped so it never exceeds a sane ceiling. This is the
    # intended portrait-ish proportion — NOT a near-square window — and it is what
    # the Start page, Stats and every view are laid out for. It never maximises
    # (the bring-to-front / palette paths drop a stale maximize); F11 is the only
    # fullscreen. Settings → System → Reset window size snaps back to this.
    # Default height bumped (refinement pass §10): the old default felt vertically
    # compressed. ~960×1180 with a slimmer breathing margin gives a more comfortable
    # tall column without growing the window unnecessarily; proportions are unchanged.
    DESIGN_W, DESIGN_H = 960, 1180
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            # SPI_GETWORKAREA (0x0030): the desktop minus the taskbar.
            if ctypes.windll.user32.SystemParametersInfoW(
                    0x0030, 0, ctypes.byref(rect), 0):
                work_w = rect.right - rect.left
                work_h = rect.bottom - rect.top
                # Tall column: fill the work-area height minus a slim breathing margin
                # (64px, was 92 — reclaims ~28px of comfort), shrink on short laptops,
                # never exceed a sane ceiling. Width stays ~960 (fits all six stat tiles
                # on one row), only trimmed on tiny screens.
                DESIGN_H = max(640, min(1560, work_h - 64))
                DESIGN_W = min(DESIGN_W, max(640, work_w - 32))
        except Exception:
            pass

    title = f"Mumble {branding.VERSION}"

    # ---- WINDOW LIFECYCLE (owner v6 resource audit) ---------------------------
    # The single main app window is created on demand; a shared holder keeps every
    # closure pointing at the current window (and whether it is minimized, so the
    # refresh router skips a window that's off-screen).
    H = {"main": None, "search": None, "main_min": False,
         "cmd_started": False}

    def _start_cmd_server():
        if H["cmd_started"]:
            return
        H["cmd_started"] = True
        _serve_webui_commands(
            lock, H, _ensure_main_front, _toggle_search, title)

    def _make_main(hidden):
        if H["main"] is not None:
            return H["main"]
        a = Api()
        a._design_size = (DESIGN_W, DESIGN_H)
        a._title = title
        # RESIZABLE: users may grow the window freely; the layout is fluid and the
        # default is one click away (Settings → Reset window size).
        w = webview.create_window(
            title, html, js_api=a, width=DESIGN_W, height=DESIGN_H,
            resizable=True, min_size=(640, 480), hidden=hidden,
            background_color="#0A0A0B")
        a._window = w
        a._show_system_search = _ensure_search_front
        a._hide_system_search = _hide_search
        a._toggle_system_search = _toggle_search
        H["main"] = w
        H["api"] = a   # closures read the live pin state via H["api"].settings
        # Reflect creation visibility so the refresh router doesn't treat a window
        # created hidden as on-screen (owner v6 audit: `hidden` was plumbed but the
        # holder never mirrored it).
        H["main_min"] = bool(hidden)

        def _ml():
            _apply_mumble_icon(title)
            _start_cmd_server()
            # STARTUP SMOOTHNESS: if the window was created hidden (to avoid a
            # white flash / half-drawn frame before the WebView2 content renders),
            # show it NOW — the content is fully loaded and ready. Then apply the
            # same foreground/deck-pin logic as the non-hidden path.
            if hidden:
                try:
                    w.show()
                except Exception:
                    pass
                H["main_min"] = False
            # ITEM 1 (owner 2026-06-29): a cold launch is spawned by the background
            # controller (a windowless pythonw), so Windows won't auto-foreground
            # the new window — it felt like a hidden background process you had to
            # hunt for. Assert foreground so Mumble ALWAYS opens active, accessible,
            # and on the taskbar. _bring_to_front ends NON-topmost + focused.
            _bring_to_front(title)
            # ITEM 16: during FIRST-RUN ONBOARDING the window must NOT be
            # always-on-top — the user has to reach API-key websites and copy
            # credentials, which a topmost Mumble blocks. So while first_run is set
            # we skip the Deck pin entirely and stay a normal focusable window;
            # finish_onboarding re-applies the pin the moment setup completes.
            try:
                onboarding = bool(a.settings.get("first_run", True))
            except Exception:
                onboarding = False
            # THE BIG SHIFT (returning user): the Deck is permanently available —
            # pin it on top by default so it never falls behind other apps (one-click
            # un-pin in the Deck persists). The non-activating palette behaviour is
            # layered on by the Deck view (set_deck_palette). Honour a saved opt-out.
            try:
                if not onboarding and a.settings.get("deck_pinned", True):
                    _set_topmost(title, True)
            except Exception as e:
                print("deck pin-on-launch skipped:", e)
        w.events.loaded += _ml

        # Hidden/minimized main = zero ambient cost: pause Enhanced animation and
        # skip refreshes while down, then catch up the instant it returns.
        def _pause(*_a):
            H["main_min"] = True
            try:
                w.evaluate_js("document.body.classList.add('paused')")
            except Exception:
                pass

        def _resume(*_a):
            H["main_min"] = False
            try:
                w.evaluate_js("document.body.classList.remove('paused');"
                              "window.pyRefresh && window.pyRefresh()")
            except Exception:
                pass

        for nm, fn in (("minimized", _pause), ("restored", _resume),
                       ("shown", _resume)):
            try:
                getattr(w.events, nm).__iadd__(fn)
            except Exception:
                pass  # this pywebview build/backend doesn't expose the event

        # Closing the main window ends the shell process: webview.start() returns
        # when the live-window count hits zero, and there is only this one window now.
        def _on_main_closed(*_a):
            H["main"] = None
        try:
            w.events.closed += _on_main_closed
        except Exception:
            pass
        return w

    def _make_search(hidden=True):
        existing = H.get("search")
        if existing is not None:
            return existing
        api = Api()
        search_title = "Mumble Find"
        window = webview.create_window(
            search_title, html, js_api=api, width=720, height=560,
            resizable=True, min_size=(520, 360), hidden=hidden,
            background_color="#0A0A0B")
        api._window = window
        api._show_system_search = _ensure_search_front
        api._hide_system_search = _hide_search
        api._toggle_system_search = _toggle_search
        H["search"] = window

        def _loaded(*_args):
            _apply_mumble_icon(search_title)
            try:
                window.evaluate_js(
                    "window.openSystemSearch && window.openSystemSearch()")
            except Exception as exc:
                print("Mumble Find initial display failed:", exc)
            if not hidden:
                _ensure_search_front()

        def _closing(*_args):
            try:
                window.hide()
                return False
            except Exception:
                return True

        try:
            window.events.loaded += _loaded
            window.events.closing += _closing
        except Exception:
            pass
        return window

    def _ensure_search_front(*_args):
        window = _make_search(hidden=False)
        try:
            window.show()
            window.restore()
            window.evaluate_js(
                "window.openSystemSearch && window.openSystemSearch()")
        except Exception as exc:
            return {"ok": False, "state": "unknown",
                    "message": str(exc)[:160]}
        H["search_visible"] = True
        return {"ok": True, "state": "visible"}

    def _hide_search(*_args):
        window = H.get("search")
        if window is None:
            H["search_visible"] = False
            return {"ok": True, "state": "hidden"}
        try:
            window.hide()
            H["search_visible"] = False
            return {"ok": True, "state": "hidden"}
        except Exception as exc:
            return {"ok": False, "state": "unknown",
                    "message": str(exc)[:160]}

    def _toggle_search(_operation_id=None):
        operation_id = str(_operation_id or "")
        outcomes = H.setdefault("search_operations", {})
        if operation_id and operation_id in outcomes:
            return dict(outcomes[operation_id])
        window = H.get("search")
        if window is None:
            result = _ensure_search_front()
        else:
            # pywebview does not expose a portable visibility property. Keep an
            # explicit process-owned state so duplicate shortcut delivery cannot
            # apply a second toggle.
            visible = bool(H.get("search_visible", False))
            result = _hide_search() if visible else _ensure_search_front()
        if operation_id:
            outcomes[operation_id] = dict(result)
            while len(outcomes) > 128:
                outcomes.pop(next(iter(outcomes)))
        return result

    def _ensure_main_front(*_a):
        mw = _make_main(hidden=False)
        # Pinned = palette mode: surface the Deck WITHOUT stealing focus, so the
        # text you just highlighted stays live and the window never "takes over"
        # the app you're working in. Default to pinned (the Big Shift default) if
        # the api/settings can't be read.
        try:
            pinned = bool(H.get("api") is None
                          or H["api"].settings.get("deck_pinned", True))
        except Exception:
            pinned = True
        if pinned:
            # On GTK, show() can map the window before focus-on-map is disabled.
            # The native palette path below performs the safe deiconify itself.
            if sys.platform.startswith("win") or getattr(mw, "native", None) is None:
                try:
                    mw.show()
                except Exception:
                    pass
            _show_palette_front(title)   # no-activate restore + raise + re-pin
            H["main_min"] = False
            return mw
        try:
            mw.show()
            mw.restore()
        except Exception:
            pass
        # RELIABILITY (owner v9 — "the History hotkey sometimes silently fails to
        # bring a minimized window back"). pywebview's show()/restore() alone do
        # NOT reliably un-minimize and foreground a window on Windows — the result
        # was the intermittent "press hotkey, nothing happens" bug. _bring_to_front
        # does the real Win32 dance (SW_RESTORE, drop a stale maximized state,
        # topmost-toggle to jump the z-order, SetForegroundWindow) so the window
        # ALWAYS restores, focuses, and sits above the other Mumble windows.
        _bring_to_front(title)
        H["main_min"] = False
        _bring_to_front(title)
        return mw

    # STARTUP SMOOTHNESS: create the window hidden so the WebView2 content
    # renders off-screen first. The loaded event handler (_ml) will call
    # w.show() once the golden-black theme is painted — no white flash.
    _make_main(hidden=True)

    webview.start()


if __name__ == "__main__":
    main()
