#!/usr/bin/env python3
"""Mumble Cloud Sync — Supabase auth + sync client.

Provides email/password registration, login, and logout backed by supabase-py.
Session tokens are stored in a separate ``.session`` file in the Mumble data
directory (NOT in plaintext settings.json). The anon/publishable key is read
from settings.json — users enter it on the Account card in Settings.

When Supabase credentials (URL + anon key) are not yet configured, all
methods return clear error dicts without raising exceptions.

Usage:
    from cloud_sync import CloudSync
    cs = CloudSync(settings)          # settings is a Settings instance
    cs.sign_up("u@ex.com", "pass")
    cs.sign_in("u@ex.com", "pass")
    cs.sign_out()
    cs.is_signed_in() → bool
    cs.get_session() → {user, ...} or None

    from cloud_sync import SyncManager
    sm = SyncManager(settings)         # full sync orchestration
    sm.sync_all()                       # push + pull all data types
    sm.start_background_sync(30)        # auto-sync every 30s
"""

import json
import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import branding
from storage_lock import exclusive_file_lock

# Session file lives in the Mumble data directory alongside settings.json.
SESSION_PATH = os.path.join(branding.DATA_DIR, ".session")


def _ensure_session_dir():
    try:
        os.makedirs(branding.DATA_DIR, exist_ok=True)
    except OSError:
        pass


class _FileStorage:
    """Custom supabase-py session storage adapter.

    supabase-py stores the session (access_token, refresh_token, user,
    expires_at, etc.) via a storage object with get_item / set_item /
    remove_item. The default is in-memory; this adapter persists to
    ``.session`` on disk so the session survives restarts.

    The file is a JSON object keyed by the supabase auth storage keys:
      - "supabase.auth.token"
      - "supabase.auth.expires_at"
      - etc.
    """

    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()

    def _read(self):
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _write(self, data):
        _ensure_session_dir()
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._path)
            branding.protect_private_path(self._path)
        except OSError:
            pass

    def get_item(self, key):
        with self._lock:
            return self._read().get(key)

    def set_item(self, key, value):
        with self._lock:
            d = self._read()
            d[key] = value
            self._write(d)

    def remove_item(self, key):
        with self._lock:
            d = self._read()
            d.pop(key, None)
            self._write(d)


class CloudSync:
    """Thin wrapper around supabase-py for the Mumble desktop app.

    Manages the supabase Client lifecycle, auth flows, and secure session
    persistence. All public methods return dicts that are safe to pass
    across the webview bridge (no exceptions, no non-serialisable types).
    """

    def __init__(self, settings):
        """Create a CloudSync backed by a Settings instance.

        ``settings`` must be a ``settings.Settings`` object (or a compatible
        object with ``get`` and durable bulk ``update`` methods).
        """
        self._settings = settings
        self._client = None
        self._storage = _FileStorage(SESSION_PATH)

    # ---- helpers -------------------------------------------------------

    def _url(self):
        return str(self._settings.get("supabase_url") or "").strip()

    def _anon_key(self):
        return str(self._settings.get("supabase_anon_key") or "").strip()

    def _has_credentials(self):
        return bool(self._url() and self._anon_key())

    def _get_client(self):
        """Lazily build or return the supabase Client.

        Returns (client, error_dict). The client is None when credentials
        are missing.
        """
        if self._client is not None:
            return self._client, None

        if not self._has_credentials():
            return None, {
                "ok": False,
                "message": (
                    "Supabase not configured. Enter your project URL and "
                    "anon key in the Account card above."
                ),
            }

        try:
            from supabase import ClientOptions, create_client

            self._client = create_client(
                self._url(),
                self._anon_key(),
                options=ClientOptions(
                    auto_refresh_token=True,
                    persist_session=True,
                    storage=self._storage,
                ),
            )
            return self._client, None
        except Exception as e:
            return None, {"ok": False, "message": f"Failed to connect: {e}"}

    def _reset_client(self):
        """Discard the cached client so the next call re-creates it."""
        self._client = None

    def _clear_session_file(self):
        """Securely remove the session file from disk."""
        try:
            os.remove(self._storage._path)
        except (FileNotFoundError, OSError):
            pass

    # ---- public auth API -----------------------------------------------

    def sign_up(self, email, password):
        """Register a new account.

        Returns:
            {"ok": True, "user": {...}, "session": {...} | null}
            {"ok": False, "message": "..."}
        """
        email = str(email or "").strip()
        # Whitespace is a valid password character and must not be silently
        # changed before it reaches the authentication provider.
        password = str(password or "")

        if not email or not password:
            return {"ok": False, "message": "Email and password are required."}
        if len(password) < 6:
            return {"ok": False, "message": "Password must be at least 6 characters."}

        client, err = self._get_client()
        if err:
            return err

        try:
            resp = client.auth.sign_up({"email": email, "password": password})
            user = None
            session = None
            if resp is not None:
                user = getattr(resp, "user", None)
                session = getattr(resp, "session", None)
                # Convert to plain dicts for the bridge
                if user is not None:
                    user = getattr(user, "model_dump", lambda: None)() or str(user)
                if session is not None:
                    session = getattr(session, "model_dump", lambda: None)() or str(session)

            if user is None and session is None:
                # Confirm email is likely enabled — registration succeeded
                # but the user must verify before a session is issued.
                return {
                    "ok": True,
                    "user": None,
                    "session": None,
                    "message": "Confirmation email sent. Please check your inbox.",
                }
            return {"ok": True, "user": user, "session": session}
        except Exception as e:
            msg = str(e)
            # Common user-friendly rewrites
            if "already registered" in msg.lower() or "already exists" in msg.lower():
                msg = "An account with this email already exists."
            elif "password" in msg.lower():
                msg = "Password is too weak. Use at least 6 characters with letters and numbers."
            return {"ok": False, "message": msg}

    def sign_in(self, email, password):
        """Sign in with email and password.

        On success the session is persisted to the ``.session`` file
        automatically by supabase-py's ClientOptions(persist_session=True).

        Returns:
            {"ok": True, "user": {...}, "session": {...}}
            {"ok": False, "message": "..."}
        """
        email = str(email or "").strip()
        password = str(password or "")

        if not email or not password:
            return {"ok": False, "message": "Email and password are required."}

        client, err = self._get_client()
        if err:
            return err

        try:
            resp = client.auth.sign_in_with_password(
                {"email": email, "password": password}
            )
            user = getattr(resp, "user", None)
            session = getattr(resp, "session", None)
            if user is not None:
                user = getattr(user, "model_dump", lambda: None)() or str(user)
            if session is not None:
                session = (getattr(session, "model_dump", lambda: None)()
                           or str(session))
            return {"ok": True, "user": user, "session": session}
        except Exception as e:
            msg = str(e)
            if "invalid login" in msg.lower() or "invalid credentials" in msg.lower():
                msg = "Invalid email or password."
            elif "email not confirmed" in msg.lower():
                msg = "Please confirm your email address first."
            return {"ok": False, "message": msg}

    def sign_out(self):
        """Sign out and clear the local session.

        Returns {"ok": True} or {"ok": False, "message": "..."}.
        """
        client, err = self._get_client()
        if err:
            # Even without a client, clear local session
            self._clear_session_file()
            self._reset_client()
            return {"ok": True, "message": "Signed out (offline)."}

        try:
            client.auth.sign_out()
        except Exception:
            # Best-effort server-side sign-out; always clear local
            pass

        self._clear_session_file()
        self._reset_client()
        return {"ok": True}

    def get_session(self):
        """Return the current session if signed in, else None.

        Returns a dict with "user" and "session" keys (or None).
        Safe to call at any time — never raises.
        """
        client, err = self._get_client()
        if err or client is None:
            return None
        try:
            s = client.auth.get_session()
            if s is None:
                return None
            user = getattr(s, "user", None)
            session = getattr(s, "session", None) if hasattr(s, "session") else s
            return {
                "user": getattr(user, "model_dump", lambda: None)() if user else None,
                "session": (
                    getattr(session, "model_dump", lambda: None)()
                    if hasattr(session, "model_dump")
                    else str(session)
                ),
            }
        except Exception:
            return None

    def is_signed_in(self):
        """Quick check: do we have a valid local session?

        Does NOT hit the network — reads the stored session only.
        """
        return self.get_session() is not None

    def has_credentials_configured(self):
        """Return True when supabase_url + supabase_anon_key are both set."""
        return self._has_credentials()

    def configure(self, url, anon_key):
        """Set or update the Supabase project credentials.

        Saves to settings.json and resets the client so the next auth
        call uses the new credentials.
        """
        new_url = str(url or "").strip()
        new_key = str(anon_key or "").strip()
        changed_project = (new_url != self._url() or new_key != self._anon_key())
        saved = self._settings.update(
            supabase_url=new_url, supabase_anon_key=new_key
        )
        if saved is False:
            return {"ok": False, "persisted": False,
                    "message": "Could not save cloud account credentials."}
        if changed_project:
            # Persisted refresh/access tokens are scoped to one Supabase
            # project. Clear them only after the replacement project reached
            # disk; a save failure must not sign the user out of the old one.
            self._clear_session_file()
        self._reset_client()
        return {"ok": True}


# ── Settings keys that must NEVER be synced to the cloud ──────────────
# API keys stay local only (Invariant 6). supabase_anon_key is also
# device-specific configuration, not user data.
_SETTINGS_SYNC_EXCLUDE = frozenset({
    "cerebras_api_key", "openai_api_key", "anthropic_api_key",
    "openrouter_api_key", "deepseek_api_key", "groq_api_key",
    "local_api_key", "supabase_anon_key",
    # Global shortcuts are platform/device configuration. Syncing macOS
    # Option-based bindings onto Windows creates shortcuts which can never be
    # pressed (and syncing Windows-key bindings to macOS is equally broken).
    "hotkey", "quick_paste_hotkey", "history_hotkey", "search_hotkey",
    "web_search_hotkey",
    "mode_key",
    # Device-local sync bookkeeping must never be imported from another
    # device; doing so can make genuinely newer remote rows look stale.
    "sync_last_run",
})


def _is_sensitive_setting(key):
    """Fail closed for current and future credential-like setting names."""
    name = str(key or "").strip().lower()
    if name in _SETTINGS_SYNC_EXCLUDE:
        return True
    return (name.endswith("_api_key") or name.endswith("_token")
            or name.endswith("_secret") or name.endswith("_password")
            or name.endswith("_credential") or name.endswith("_credentials"))

# ── Per-data-type table mapping ───────────────────────────────────────
_SYNC_TABLES = {
    "settings": "user_settings",
    "stats": "user_stats",
    "history": "user_history",
    "reader": "user_reader_library",
    "favorites": "user_favorites",
    "presets": "user_presets",
}

# All syncable data types
_SYNC_TYPES = tuple(_SYNC_TABLES.keys())

# Default sync interval (seconds) for background sync
_DEFAULT_SYNC_INTERVAL = 30


def _iso_now():
    """UTC ISO-8601 timestamp with microseconds, for updated_at."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso_stamp():
    """UTC ISO-8601 timestamp without microseconds for record stamps."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(ts):
    """Parse an ISO timestamp string to a timezone-aware datetime.
    Returns the minimum datetime on any parse failure."""
    if not ts:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        # Handle both "2026-01-01T00:00:00Z" and "2026-01-01T00:00:00.000Z"
        ts = str(ts).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        try:
            return datetime.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return datetime.min.replace(tzinfo=timezone.utc)


class SyncManager:
    """Orchestrates push/pull of all syncable data types to Supabase.

    Local-first: all methods handle network errors gracefully and never
    block the app. Last-write-wins by ``updated_at`` timestamp on every
    row. Sync can be toggled per data type via ``sync_<type>`` settings.

    Usage:
        sm = SyncManager(settings)       # settings is a Settings instance
        result = sm.sync_type("settings") # push + pull settings
        status = sm.get_sync_status()     # per-type timestamps + errors
        sm.start_background_sync(30)      # auto-sync every 30s
        sm.stop_background_sync()
    """

    def __init__(self, settings):
        """Create a SyncManager backed by a Settings instance.

        ``settings`` must be a ``settings.Settings`` object (or any
        object with a dict-like ``.get(key)`` and ``.set(key, value)``).
        """
        self._settings = settings
        self._cs = CloudSync(settings)
        self._sync_lock = threading.RLock()
        # Per-type sync state
        self._last_sync = {}    # data_type -> ISO timestamp of last sync
        self._last_error = {}   # data_type -> error message or None
        self._pending = {}      # data_type -> approximate pending count
        self._load_sync_state()
        # Background sync
        self._bg_thread = None
        self._bg_stop = threading.Event()

    # ── helpers ───────────────────────────────────────────────────────

    def _is_signed_in(self):
        return self._cs.is_signed_in()

    def _has_credentials(self):
        return self._cs.has_credentials_configured()

    def _get_client(self):
        return self._cs._get_client()

    def _is_enabled(self, data_type):
        """Check master toggle + per-type toggle."""
        if not self._settings.get("sync_enabled", True):
            return False
        return self._settings.get("sync_" + data_type, True)

    def _load_sync_state(self):
        """Restore last-sync timestamps from settings."""
        saved = self._settings.get("sync_last_run")
        if isinstance(saved, dict):
            self._last_sync = {k: v for k, v in saved.items()
                               if k in _SYNC_TABLES}

    def _save_sync_state(self):
        """Persist last-sync timestamps to settings."""
        self._settings.set("sync_last_run", dict(self._last_sync))

    def _mark_synced(self, data_type):
        """Record a successful sync for a data type."""
        now = _iso_now()
        self._last_sync[data_type] = now
        self._last_error[data_type] = None
        self._pending[data_type] = 0
        self._save_sync_state()

    def _mark_error(self, data_type, message):
        """Record a sync error for a data type."""
        self._last_error[data_type] = str(message)[:200]
        # Keep the last successful timestamp; don't bump it on error

    # ── settings helpers ──────────────────────────────────────────────

    @staticmethod
    def _syncable_settings(settings_data):
        """Return a copy of settings_data with API keys removed."""
        out = {}
        for k, v in settings_data.items():
            if not _is_sensitive_setting(k):
                out[k] = v
        return out

    # ── pull helpers ──────────────────────────────────────────────────

    def _pull_single_row(self, table, uid):
        """Pull the single row for this user from *table* (settings/stats/presets).
        Returns (payload, updated_at) or (None, None)."""
        client, err = self._get_client()
        if err or client is None:
            return None, None
        try:
            resp = client.table(table).select("*").eq("user_id", uid).maybe_single().execute()
            row = resp.data if hasattr(resp, "data") else None
            if row and isinstance(row, dict):
                return row.get("payload"), row.get("updated_at")
            return None, None
        except Exception as e:
            raise e

    def _pull_rows(self, table, uid, limit=200):
        """Pull all rows for this user from *table* (history/reader/favorites).
        Returns list of {payload, updated_at, ...}."""
        client, err = self._get_client()
        if err or client is None:
            return []
        try:
            resp = (client.table(table)
                    .select("*")
                    .eq("user_id", uid)
                    .order("updated_at", desc=True)
                    .limit(limit)
                    .execute())
            data = resp.data if hasattr(resp, "data") else []
            return data if isinstance(data, list) else []
        except Exception as e:
            raise e

    # ── push helpers ──────────────────────────────────────────────────

    def _upsert_single_row(self, table, uid, row_id, payload):
        """Upsert a single-row-per-user table (settings/stats/presets)."""
        client, err = self._get_client()
        if err or client is None:
            return err
        ts = _iso_now()
        try:
            row = {
                "user_id": uid,
                "payload": payload,
                "updated_at": ts,
            }
            # Omitting an absent id lets Postgres apply its UUID default;
            # explicitly sending null violates a NOT NULL primary key.
            if row_id:
                row["id"] = row_id
            client.table(table).upsert(row, on_conflict="id").execute()
            return None
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _upsert_row(self, table, row):
        """Upsert a row dict directly (for history/reader/favorites)."""
        client, err = self._get_client()
        if err or client is None:
            return err
        try:
            client.table(table).upsert(row, on_conflict="id").execute()
            return None
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _delete_row(self, table, row_id, uid):
        """Delete a row by id (soft-delete by removing from server)."""
        client, err = self._get_client()
        if err or client is None:
            return err
        try:
            client.table(table).delete().eq("id", row_id).eq("user_id", uid).execute()
            return None
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _delete_known_remote_absences(self, data_type, table, uid,
                                      local_keys, key_from_row, limit=500):
        """Propagate local deletions without erasing unseen remote additions.

        A remote row missing locally is a deletion only when it predates the
        last successful sync.  Newer missing rows were created on another
        device and must survive for the following pull.
        """
        cutoff_text = self._last_sync.get(data_type, "")
        if not cutoff_text:
            return 0, 0
        cutoff = _parse_ts(cutoff_text)
        deleted = errors = 0
        for row in self._pull_rows(table, uid, limit=limit):
            if not isinstance(row, dict):
                continue
            key = key_from_row(row)
            if key is None or key in local_keys:
                continue
            if _parse_ts(row.get("updated_at")) > cutoff:
                continue
            row_id = row.get("id")
            if not row_id:
                continue
            err = self._delete_row(table, row_id, uid)
            if err:
                errors += 1
            else:
                deleted += 1
        return deleted, errors

    def _get_user_id(self):
        """Get the current user's UUID. Returns None if not signed in."""
        sess = self._cs.get_session()
        if not sess:
            return None
        user = sess.get("user")
        if isinstance(user, dict):
            return user.get("id")
        # Pydantic model fallback
        if hasattr(user, "id"):
            return str(user.id)
        return None

    # ══════════════════════════════════════════════════════════════════
    #  SETTINGS SYNC
    # ══════════════════════════════════════════════════════════════════

    def push_settings(self):
        """Push syncable settings to user_settings table."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("settings"):
            return {"ok": True, "message": "Settings sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            payload = dict(self._settings.data)
            payload = self._syncable_settings(payload)
            # Get existing row id so we upsert rather than insert duplicate
            client, err = self._get_client()
            if err:
                return err
            existing = (client.table("user_settings")
                        .select("id")
                        .eq("user_id", uid)
                        .maybe_single()
                        .execute())
            row_id = None
            if hasattr(existing, "data") and isinstance(existing.data, dict):
                row_id = existing.data.get("id")

            err = self._upsert_single_row(
                "user_settings", uid, row_id, payload)
            if err:
                self._mark_error("settings", err.get("message", str(err)))
                return err
            self._mark_synced("settings")
            return {"ok": True, "pushed": True}
        except Exception as e:
            msg = f"Settings push failed: {e}"
            self._mark_error("settings", msg)
            return {"ok": False, "message": msg}

    def pull_settings(self):
        """Pull settings from user_settings and merge locally (LWW)."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("settings"):
            return {"ok": True, "message": "Settings sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            payload, updated_at = self._pull_single_row("user_settings", uid)
            if payload is None or not isinstance(payload, dict):
                self._mark_synced("settings")
                return {"ok": True, "message": "No remote settings to pull."}

            # LWW merge: remote settings that are newer override local
            remote_ts = _parse_ts(updated_at)
            local_ts = _parse_ts(self._last_sync.get("settings", ""))

            if remote_ts <= local_ts:
                self._mark_synced("settings")
                return {"ok": True, "message": "Local settings are up to date."}

            # Merge: apply remote values, but NEVER overwrite API keys
            changes = {}
            for k, v in payload.items():
                if _is_sensitive_setting(k):
                    continue
                if k not in self._settings.data:
                    continue
                current = self._settings.data.get(k)
                if current != v:
                    changes[k] = v

            if changes:
                updater = getattr(self._settings, "update", None)
                if callable(updater):
                    if updater(**changes) is False:
                        raise OSError("remote settings failed validation or could not be saved")
                else:
                    for key, value in changes.items():
                        if self._settings.set(key, value) is False:
                            raise OSError(f"remote setting {key} could not be saved")

            self._mark_synced("settings")
            return {"ok": True, "pulled": True, "changed": len(changes)}
        except Exception as e:
            msg = f"Settings pull failed: {e}"
            self._mark_error("settings", msg)
            return {"ok": False, "message": msg}

    def sync_settings(self):
        """Full settings sync: pull remote, merge, push local."""
        if not self._is_enabled("settings"):
            return {"ok": True, "message": "Settings sync disabled.", "skipped": True}
        pull = self.pull_settings()
        push = self.push_settings()
        return {
            "ok": pull.get("ok") and push.get("ok"),
            "pull": pull,
            "push": push,
        }

    # ══════════════════════════════════════════════════════════════════
    #  STATS SYNC
    # ══════════════════════════════════════════════════════════════════

    def push_stats(self):
        """Push local stats aggregates to user_stats table."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("stats"):
            return {"ok": True, "message": "Stats sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            from stats import Stats
            s = Stats(branding.STATS_JSON)
            payload = dict(s.data)  # snapshot

            client, err = self._get_client()
            if err:
                return err
            existing = (client.table("user_stats")
                        .select("id")
                        .eq("user_id", uid)
                        .maybe_single()
                        .execute())
            row_id = None
            if hasattr(existing, "data") and isinstance(existing.data, dict):
                row_id = existing.data.get("id")

            err = self._upsert_single_row("user_stats", uid, row_id, payload)
            if err:
                self._mark_error("stats", err.get("message", str(err)))
                return err
            self._mark_synced("stats")
            return {"ok": True, "pushed": True}
        except Exception as e:
            msg = f"Stats push failed: {e}"
            self._mark_error("stats", msg)
            return {"ok": False, "message": msg}

    def pull_stats(self):
        """Pull stats from user_stats and merge locally (LWW)."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("stats"):
            return {"ok": True, "message": "Stats sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            payload, updated_at = self._pull_single_row("user_stats", uid)
            if payload is None or not isinstance(payload, dict):
                self._mark_synced("stats")
                return {"ok": True, "message": "No remote stats to pull."}

            remote_ts = _parse_ts(updated_at)
            local_ts = _parse_ts(self._last_sync.get("stats", ""))

            if remote_ts <= local_ts:
                self._mark_synced("stats")
                return {"ok": True, "message": "Local stats are up to date."}

            # LWW merge: overwrite local stats with remote
            from stats import Stats
            s = Stats(branding.STATS_JSON)
            with s._lock, exclusive_file_lock(s.path) as acquired:
                if not acquired:
                    raise RuntimeError("stats store is busy")
                s.data = dict(payload)
                s._coerce_types()
                s.is_new = False
                s._save()

            self._mark_synced("stats")
            return {"ok": True, "pulled": True}
        except Exception as e:
            msg = f"Stats pull failed: {e}"
            self._mark_error("stats", msg)
            return {"ok": False, "message": msg}

    def sync_stats(self):
        """Full stats sync: pull remote, merge, push local."""
        if not self._is_enabled("stats"):
            return {"ok": True, "message": "Stats sync disabled.", "skipped": True}
        pull = self.pull_stats()
        push = self.push_stats()
        return {
            "ok": pull.get("ok") and push.get("ok"),
            "pull": pull,
            "push": push,
        }

    # ══════════════════════════════════════════════════════════════════
    #  HISTORY SYNC
    # ══════════════════════════════════════════════════════════════════

    def push_history(self):
        """Push local history entries to user_history table (last 100)."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("history"):
            return {"ok": True, "message": "History sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            from history import History
            h = History(branding.HISTORY_JSON, branding.HISTORY_TXT, maxlen=100)
            items = list(h.items)  # oldest→newest, capped at 100
            ts = _iso_now()
            local_keys = {(str(entry.get("stamp", "")),
                           str(entry.get("text", ""))) for entry in items}

            pushed = 0
            errors = 0
            for entry in items:
                stamp = str(entry.get("stamp", ""))
                # Stable ids make repeated background pushes idempotent.  The
                # old random UUID created a duplicate server row every 30s.
                identity = f"mumble:history:{uid}:{stamp}:{entry.get('text', '')}"
                row_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
                err = self._upsert_row("user_history", {
                    "id": row_id,
                    "user_id": uid,
                    "payload": entry,
                    "entry_time": stamp,
                    "updated_at": ts,
                })
                if err:
                    errors += 1
                else:
                    pushed += 1

            def _history_key(row):
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    return None
                stamp = str(payload.get("stamp", ""))
                return ((stamp, str(payload.get("text", "")))
                        if stamp else None)

            deleted, delete_errors = self._delete_known_remote_absences(
                "history", "user_history", uid, local_keys, _history_key)
            errors += delete_errors

            if errors:
                self._pending["history"] = errors
                self._mark_error("history", f"{errors} history rows failed to upload")
                return {"ok": False, "pushed": pushed, "errors": errors,
                        "message": self._last_error["history"]}
            self._mark_synced("history")
            return {"ok": True, "pushed": pushed, "deleted": deleted,
                    "errors": 0}
        except Exception as e:
            msg = f"History push failed: {e}"
            self._mark_error("history", msg)
            return {"ok": False, "message": msg}

    def pull_history(self):
        """Pull history from user_history and merge locally (LWW, dedup by stamp)."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("history"):
            return {"ok": True, "message": "History sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            rows = self._pull_rows("user_history", uid, limit=100)
            if not rows:
                self._mark_synced("history")
                return {"ok": True, "message": "No remote history to pull."}

            from history import History
            h = History(branding.HISTORY_JSON, branding.HISTORY_TXT, maxlen=100)

            # A timestamp has only second precision, so two genuine entries can
            # share it.  Deduplicate by stable content identity, not stamp alone.
            with h._lock, exclusive_file_lock(h.json_path) as acquired:
                if not acquired:
                    raise RuntimeError("history store is busy")
                h._sync_from_disk()
                local_ids = {(str(e.get("stamp", "")), str(e.get("text", "")))
                             for e in h.items}

                merged = 0
                for row in rows:
                    payload = row.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    stamp = str(payload.get("stamp", ""))
                    identity = (stamp, str(payload.get("text", "")))
                    if stamp and identity not in local_ids:
                        h.items.append(payload)
                        local_ids.add(identity)
                        merged += 1

                while len(h.items) > h.maxlen:
                    h.items.popleft()
                h._save_json()

            self._mark_synced("history")
            return {"ok": True, "pulled": merged}
        except Exception as e:
            msg = f"History pull failed: {e}"
            self._mark_error("history", msg)
            return {"ok": False, "message": msg}

    def sync_history(self):
        """Full history sync: push local changes/deletes, then pull additions."""
        if not self._is_enabled("history"):
            return {"ok": True, "message": "History sync disabled.", "skipped": True}
        push = self.push_history()
        if not push.get("ok"):
            return {"ok": False, "push": push,
                    "pull": {"ok": False, "skipped": True}}
        pull = self.pull_history()
        return {
            "ok": pull.get("ok") and push.get("ok"),
            "pull": pull,
            "push": push,
        }

    # ══════════════════════════════════════════════════════════════════
    #  READER LIBRARY SYNC
    # ══════════════════════════════════════════════════════════════════

    def push_reader_library(self):
        """Push local reader library docs to user_reader_library."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("reader"):
            return {"ok": True, "message": "Reader sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            import reader_store as rs
            rows = rs._load()  # all docs, uncapped
            ts = _iso_now()
            local_keys = {(doc.get("id") or rs._doc_id(doc.get("text", "")))
                          for doc in rows}

            pushed = 0
            errors = 0
            for doc in rows:
                doc_hash = (doc.get("id") or
                            rs._doc_id(doc.get("text", "")))
                payload = dict(doc)
                # Absolute local paths reveal usernames/folder structure and
                # are meaningless on another device. Keep them local-only.
                payload.pop("source_path", None)
                # Get existing row for this doc
                client, err = self._get_client()
                if err:
                    errors += 1
                    continue
                try:
                    existing = (client.table("user_reader_library")
                                .select("id")
                                .eq("user_id", uid)
                                .eq("doc_hash", doc_hash)
                                .maybe_single()
                                .execute())
                except Exception:
                    errors += 1
                    continue

                row_id = None
                if hasattr(existing, "data") and isinstance(existing.data, dict):
                    row_id = existing.data.get("id")

                err = self._upsert_row("user_reader_library", {
                    "id": row_id or str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"mumble:reader:{uid}:{doc_hash}")),
                    "user_id": uid,
                    "payload": payload,
                    "doc_hash": doc_hash,
                    "updated_at": doc.get("updated_at") or ts,
                })
                if err:
                    errors += 1
                else:
                    pushed += 1

            def _reader_key(row):
                payload = row.get("payload")
                return (row.get("doc_hash")
                        or (payload.get("id")
                            if isinstance(payload, dict) else None))

            deleted, delete_errors = self._delete_known_remote_absences(
                "reader", "user_reader_library", uid, local_keys, _reader_key)
            errors += delete_errors

            if errors:
                self._pending["reader"] = errors
                self._mark_error("reader", f"{errors} reader rows failed to upload")
                return {"ok": False, "pushed": pushed, "errors": errors,
                        "message": self._last_error["reader"]}
            self._mark_synced("reader")
            return {"ok": True, "pushed": pushed, "deleted": deleted,
                    "errors": 0}
        except Exception as e:
            msg = f"Reader library push failed: {e}"
            self._mark_error("reader", msg)
            return {"ok": False, "message": msg}

    def pull_reader_library(self):
        """Pull reader library from user_reader_library and merge locally (LWW)."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("reader"):
            return {"ok": True, "message": "Reader sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            import reader_store as rs
            rows = self._pull_rows(
                "user_reader_library", uid, limit=rs.MAX_QUERY_LIMIT)
            if not rows:
                self._mark_synced("reader")
                return {"ok": True, "message": "No remote reader data to pull."}

            merged = 0
            with rs._LOCK:
                # Load under the same lock used for the final save. Loading
                # before acquiring it let a local Reader write be overwritten
                # by this stale snapshot.
                local_rows = rs._load()
                local_by_hash = {r.get("id"): r for r in local_rows}
                for row in rows:
                    payload = row.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    payload = dict(payload)
                    payload.pop("source_path", None)
                    payload.setdefault("updated_at", row.get("updated_at") or _iso_now())
                    doc_hash = row.get("doc_hash") or payload.get("id")
                    remote_ts = _parse_ts(row.get("updated_at", ""))
                    local_doc = local_by_hash.get(doc_hash)
                    if local_doc:
                        local_ts = _parse_ts(local_doc.get("updated_at", ""))
                        if remote_ts <= local_ts:
                            continue  # local is newer
                        # Remote is newer — update local. Reader sync remains
                        # whole-document LWW until per-field tombstones/version
                        # vectors exist; guessing a union would make removals
                        # impossible to sync reliably.
                        for k, v in payload.items():
                            local_doc[k] = v
                        merged += 1
                    else:
                        # New doc from remote
                        local_rows.append(payload)
                        local_by_hash[doc_hash] = payload
                        merged += 1

                if merged:
                    if not rs._save(local_rows):
                        raise RuntimeError("Could not persist the merged Reader library.")

            self._mark_synced("reader")
            return {"ok": True, "pulled": merged}
        except Exception as e:
            msg = f"Reader library pull failed: {e}"
            self._mark_error("reader", msg)
            return {"ok": False, "message": msg}

    def sync_reader(self):
        """Full reader sync: push local changes/deletes, then pull additions."""
        if not self._is_enabled("reader"):
            return {"ok": True, "message": "Reader sync disabled.", "skipped": True}
        push = self.push_reader_library()
        if not push.get("ok"):
            return {"ok": False, "push": push,
                    "pull": {"ok": False, "skipped": True}}
        pull = self.pull_reader_library()
        return {
            "ok": pull.get("ok") and push.get("ok"),
            "pull": pull,
            "push": push,
        }

    # ══════════════════════════════════════════════════════════════════
    #  FAVORITES SYNC
    # ══════════════════════════════════════════════════════════════════

    def push_favorites(self):
        """Push local favorites to user_favorites table."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("favorites"):
            return {"ok": True, "message": "Favorites sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            from favorites import Favorites
            f = Favorites()
            items = list(f.items)  # oldest→newest
            ts = _iso_now()
            local_keys = {entry.get("key") for entry in items
                          if entry.get("key")}

            pushed = 0
            errors = 0
            for entry in items:
                fav_text = str(entry.get("text", ""))[:200]
                key = entry.get("key", "")
                row_id = str(uuid.uuid5(
                    uuid.NAMESPACE_URL, f"mumble:favorite:{uid}:{key}"))
                err = self._upsert_row("user_favorites", {
                    "id": row_id,
                    "user_id": uid,
                    "payload": entry,
                    "fav_text": fav_text,
                    "updated_at": ts,
                })
                if err:
                    errors += 1
                else:
                    pushed += 1

            def _favorite_key(row):
                payload = row.get("payload")
                return payload.get("key") if isinstance(payload, dict) else None

            deleted, delete_errors = self._delete_known_remote_absences(
                "favorites", "user_favorites", uid, local_keys, _favorite_key)
            errors += delete_errors

            if errors:
                self._pending["favorites"] = errors
                self._mark_error(
                    "favorites", f"{errors} favorite rows failed to upload")
                return {"ok": False, "pushed": pushed, "errors": errors,
                        "message": self._last_error["favorites"]}
            self._mark_synced("favorites")
            return {"ok": True, "pushed": pushed, "deleted": deleted,
                    "errors": 0}
        except Exception as e:
            msg = f"Favorites push failed: {e}"
            self._mark_error("favorites", msg)
            return {"ok": False, "message": msg}

    def pull_favorites(self):
        """Pull favorites from user_favorites and merge locally (LWW, dedup by key)."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("favorites"):
            return {"ok": True, "message": "Favorites sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            from favorites import Favorites, MAX_FAVS
            rows = self._pull_rows("user_favorites", uid, limit=MAX_FAVS)
            if not rows:
                self._mark_synced("favorites")
                return {"ok": True, "message": "No remote favorites to pull."}

            f = Favorites()
            with f._lock, exclusive_file_lock(f.path) as acquired:
                if not acquired:
                    raise RuntimeError("favorites store is busy")
                f._load()
                local_keys = {e.get("key") for e in f.items}

                merged = 0
                for row in rows:
                    payload = row.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    key = payload.get("key")
                    if not key:
                        continue
                    if key not in local_keys:
                        f.items.append(payload)
                        local_keys.add(key)
                        merged += 1

                f.items = f.items[-MAX_FAVS:]
                if merged:
                    f._save()

            self._mark_synced("favorites")
            return {"ok": True, "pulled": merged}
        except Exception as e:
            msg = f"Favorites pull failed: {e}"
            self._mark_error("favorites", msg)
            return {"ok": False, "message": msg}

    def sync_favorites(self):
        """Full favorites sync: push local changes/deletes, then pull additions."""
        if not self._is_enabled("favorites"):
            return {"ok": True, "message": "Favorites sync disabled.", "skipped": True}
        push = self.push_favorites()
        if not push.get("ok"):
            return {"ok": False, "push": push,
                    "pull": {"ok": False, "skipped": True}}
        pull = self.pull_favorites()
        return {
            "ok": pull.get("ok") and push.get("ok"),
            "pull": pull,
            "push": push,
        }

    # ══════════════════════════════════════════════════════════════════
    #  CUSTOM PRESETS SYNC
    # ══════════════════════════════════════════════════════════════════

    def push_presets(self):
        """Push custom presets to user_presets table (built-in NOT synced)."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("presets"):
            return {"ok": True, "message": "Presets sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            import presets as presets_mod
            custom = presets_mod.load_custom()
            # Only sync non-empty custom slots
            payload = {
                str(slot): data
                for slot, data in custom.items()
                if data.get("title", "").strip() and data.get("instruction", "").strip()
            }

            client, err = self._get_client()
            if err:
                return err
            existing = (client.table("user_presets")
                        .select("id")
                        .eq("user_id", uid)
                        .maybe_single()
                        .execute())
            row_id = None
            if hasattr(existing, "data") and isinstance(existing.data, dict):
                row_id = existing.data.get("id")

            err = self._upsert_single_row(
                "user_presets", uid, row_id, payload)
            if err:
                self._mark_error("presets", err.get("message", str(err)))
                return err
            self._mark_synced("presets")
            return {"ok": True, "pushed": True}
        except Exception as e:
            msg = f"Presets push failed: {e}"
            self._mark_error("presets", msg)
            return {"ok": False, "message": msg}

    def pull_presets(self):
        """Pull custom presets from user_presets and merge locally (LWW)."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._is_enabled("presets"):
            return {"ok": True, "message": "Presets sync is disabled.", "skipped": True}

        uid = self._get_user_id()
        if not uid:
            return {"ok": False, "message": "No user id available."}

        try:
            import presets as presets_mod
            payload, updated_at = self._pull_single_row("user_presets", uid)
            if payload is None or not isinstance(payload, dict):
                self._mark_synced("presets")
                return {"ok": True, "message": "No remote presets to pull."}

            remote_ts = _parse_ts(updated_at)
            local_ts = _parse_ts(self._last_sync.get("presets", ""))

            if remote_ts <= local_ts:
                self._mark_synced("presets")
                return {"ok": True, "message": "Local presets are up to date."}

            # Merge remote custom presets into local
            local_custom = presets_mod.load_custom()
            merged = False
            for slot_str, data in payload.items():
                try:
                    slot = int(slot_str)
                except (ValueError, TypeError):
                    continue
                if slot not in presets_mod.CUSTOM_SLOTS:
                    continue  # never accept built-in slot numbers
                if not isinstance(data, dict):
                    continue
                title = str(data.get("title", "")).strip()
                instr = str(data.get("instruction", "")).strip()
                if title and instr:
                    desc = str(data.get("description", "")).strip()
                    local_custom[slot] = {
                        "title": title,
                        "description": desc,
                        "instruction": instr,
                    }
                    merged = True
                elif slot in local_custom:
                    # Remote deleted this slot
                    del local_custom[slot]
                    merged = True

            if merged:
                presets_mod.save_custom(local_custom)

            self._mark_synced("presets")
            return {"ok": True, "pulled": True}
        except Exception as e:
            msg = f"Presets pull failed: {e}"
            self._mark_error("presets", msg)
            return {"ok": False, "message": msg}

    def sync_presets(self):
        """Full presets sync: pull remote, merge, push local."""
        if not self._is_enabled("presets"):
            return {"ok": True, "message": "Presets sync disabled.", "skipped": True}
        pull = self.pull_presets()
        push = self.push_presets()
        return {
            "ok": pull.get("ok") and push.get("ok"),
            "pull": pull,
            "push": push,
        }

    # ══════════════════════════════════════════════════════════════════
    #  ORCHESTRATION
    # ══════════════════════════════════════════════════════════════════

    def sync_type(self, data_type):
        """Sync one data type (pull + push). Returns result dict."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._has_credentials():
            return {"ok": False, "message": "Supabase not configured."}

        with self._sync_lock:
            if data_type == "settings":
                return self.sync_settings()
            elif data_type == "stats":
                return self.sync_stats()
            elif data_type == "history":
                return self.sync_history()
            elif data_type == "reader":
                return self.sync_reader()
            elif data_type == "favorites":
                return self.sync_favorites()
            elif data_type == "presets":
                return self.sync_presets()
            else:
                return {"ok": False, "message": f"Unknown data type: {data_type}"}

    def sync_all(self):
        """Sync all data types. Returns per-type results."""
        if not self._is_signed_in():
            return {"ok": False, "message": "Not signed in."}
        if not self._has_credentials():
            return {"ok": False, "message": "Supabase not configured."}

        results = {}
        all_ok = True
        for dt in _SYNC_TYPES:
            r = self.sync_type(dt)
            results[dt] = r
            if not r.get("ok"):
                all_ok = False
        return {"ok": all_ok, "results": results}

    def get_sync_status(self):
        """Return per-data-type sync status for the Settings UI.

        Returns a dict safe to pass across the webview bridge:
            {"type": {"last_sync": "ISO"|null, "error": str|null,
                      "enabled": bool, "pending": int}, ...}
        """
        status = {}
        for dt in _SYNC_TYPES:
            status[dt] = {
                "last_sync": self._last_sync.get(dt),
                "error": self._last_error.get(dt),
                "enabled": self._is_enabled(dt),
                "pending": self._pending.get(dt, 0),
            }
        return {
            "ok": True,
            "signed_in": self._is_signed_in(),
            "configured": self._has_credentials(),
            "types": status,
        }

    def start_background_sync(self, interval=_DEFAULT_SYNC_INTERVAL):
        """Start a daemon thread that calls sync_all() every *interval* seconds.

        Only syncs when signed in and credentials are configured. Safe to
        call multiple times — stops any existing background sync first.
        """
        stopped = self.stop_background_sync()
        if not stopped.get("ok"):
            return stopped
        try:
            interval = float(interval)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "message": "Sync interval must be a positive number."}
        if not math.isfinite(interval) or interval <= 0:
            return {"ok": False, "message": "Sync interval must be a positive number."}

        # A fresh event per generation prevents a timed-out old worker from
        # being accidentally re-enabled when a replacement starts.
        self._bg_stop = threading.Event()
        stop_event = self._bg_stop

        def _loop():
            while not stop_event.wait(interval):
                try:
                    if self._is_signed_in() and self._has_credentials():
                        self.sync_all()
                except Exception:
                    pass  # background sync must never crash

        self._bg_thread = threading.Thread(target=_loop, daemon=True)
        self._bg_thread.start()
        return {"ok": True, "interval": interval}

    def stop_background_sync(self):
        """Stop the background sync thread if running."""
        self._bg_stop.set()
        thread = self._bg_thread
        if thread and thread.is_alive():
            if thread is threading.current_thread():
                return {"ok": False,
                        "message": "Background sync cannot join itself."}
            thread.join(timeout=2.0)
            if thread.is_alive():
                # Keep both the reference and its stop event intact.  Clearing
                # either here allowed a second worker to start while the first
                # was still blocked in a network request.
                return {"ok": False,
                        "message": "Background sync is still stopping."}
        self._bg_thread = None
        return {"ok": True}
