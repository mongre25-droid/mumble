"""Fast, local application-and-indexed-file search for Mumble Find.

The search engine deliberately has a narrow authority boundary: it indexes
known application locations, asks an operating-system provider for bounded file
candidates, returns immutable result ids, and will only act on an id that came
from those trusted sources. Search text can never become a command line or an
arbitrary path.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
from concurrent.futures import (
    FIRST_COMPLETED,
    ThreadPoolExecutor,
    TimeoutError as FutureTimeout,
    wait,
)
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import unicodedata

from .process_provider import WindowsSearchProcessProvider


INDEX_VERSION = 3
DEFAULT_MAX_ITEMS = 75_000
DEFAULT_RESULT_LIMIT = 12
DEFAULT_QUERY_DEADLINE_MS = 75
PROVIDER_CANDIDATE_LIMIT = 250
PROVIDER_MAX_INFLIGHT = 2
INDEX_MAX_AGE_SECONDS = 15 * 60
STATE_MAX_HISTORY = 250
ICON_CACHE_LIMIT = 128
ICON_BATCH_LIMIT = 12
ICON_CACHE_VERSION = 1
ICON_CONCURRENCY = 4
APP_SUFFIXES = frozenset({".lnk", ".url", ".exe", ".bat", ".cmd"})
def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _normalise(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def _stable_id(kind, target):
    raw = f"{kind}\0{target}".casefold().encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:24]


def _setting(settings, key, default=None):
    if settings is None:
        return default
    getter = getattr(settings, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            return getter(key)
    if isinstance(settings, dict):
        return settings.get(key, default)
    return default


@dataclass(frozen=True)
class SearchItem:
    id: str
    kind: str
    name: str
    subtitle: str
    target: str
    source: str = "local"
    keywords: str = ""
    icon_hint: str = ""

    @classmethod
    def make(cls, kind, name, target, subtitle="", source="local", keywords="",
             icon_hint=""):
        return cls(
            id=_stable_id(kind, target),
            kind=str(kind),
            name=str(name or Path(str(target)).name or target),
            subtitle=str(subtitle or target),
            target=str(target),
            source=str(source or "local"),
            keywords=str(keywords or ""),
            icon_hint=str(icon_hint or ""),
        )


class QueryCancellation:
    """Cooperative cancellation identity shared by provider and ranker work."""

    def __init__(self, generation):
        self.generation = int(generation)
        self._event = threading.Event()

    @property
    def cancelled(self):
        return self._event.is_set()

    def cancel(self):
        self._event.set()


class SystemSearchEngine:
    """Bounded local index with fuzzy ranking and safe result actions."""

    def __init__(self, settings=None, data_dir=None, platform=None, home=None,
                  file_roots=None, app_roots=None, max_items=None,
                  start_background=True, url_opener=None, file_provider=None,
                  native_drag_starter=None):
        self.settings = settings
        self.platform = self._platform_name(platform)
        self.home = Path(home or Path.home()).expanduser()
        self.data_dir = Path(data_dir or (self.home / ".mumble"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.data_dir / "system_search_index.json"
        self.state_path = self.data_dir / "system_search_state.json"
        configured_max = _setting(settings, "system_search_max_items", max_items)
        try:
            self.max_items = max(1_000, min(200_000, int(
                configured_max or DEFAULT_MAX_ITEMS)))
        except (TypeError, ValueError):
            self.max_items = DEFAULT_MAX_ITEMS
        self._file_roots_override = file_roots
        self._app_roots_override = app_roots
        self._url_opener = url_opener
        self._native_drag_starter = native_drag_starter
        self._lock = threading.RLock()
        self._items = {}
        self._ephemeral = {}
        self._refreshing = False
        self._updated_at = ""
        self._last_error = ""
        self._state = {"favorites": [], "usage": {}}
        self._icon_cache = OrderedDict()
        self._icon_epoch = 1
        self._icon_executor = ThreadPoolExecutor(
            max_workers=ICON_CONCURRENCY,
            thread_name_prefix="mumble-find-icon",
        )
        self._query_lock = threading.RLock()
        self._query_generation = 0
        self._query_tokens = {}
        self._provider_slots = threading.BoundedSemaphore(PROVIDER_MAX_INFLIGHT)
        self._provider_futures = {}
        self._closed = False
        self._file_provider = file_provider
        if self._file_provider is None and self.platform == "windows":
            self._file_provider = WindowsSearchProcessProvider(
                capacity=PROVIDER_MAX_INFLIGHT,
            )
        elif self._file_provider is None and self.platform == "macos":
            from .macos_spotlight import SpotlightSearchProvider
            self._file_provider = SpotlightSearchProvider(home=self.home)
        self._query_executor = None
        if not callable(getattr(self._file_provider, "submit", None)):
            self._query_executor = ThreadPoolExecutor(
                max_workers=PROVIDER_MAX_INFLIGHT,
                thread_name_prefix="mumble-find-query",
            )
        self._linux_icon_paths = {}
        self._background_refresh_enabled = bool(start_background)
        self._load_state()
        self._load_cache()
        if start_background and self.supported:
            self.start_refresh(force=not bool(self._items))

    @staticmethod
    def _platform_name(value=None):
        value = str(value or sys.platform).lower()
        if value.startswith("win") or value == "windows":
            return "windows"
        if value.startswith("linux"):
            return "linux"
        if value.startswith("darwin") or value in {"mac", "macos"}:
            return "macos"
        return "unsupported"

    @property
    def supported(self):
        return self.platform in {"windows", "linux", "macos"}

    def _read_json(self, path, default):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else default
        except (OSError, ValueError, TypeError):
            return default

    def _write_json(self, path, payload):
        temp = path.with_suffix(path.suffix + ".tmp")
        try:
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temp, path)
        finally:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass

    def _load_state(self):
        raw = self._read_json(self.state_path, {})
        favorites = raw.get("favorites", [])
        usage = raw.get("usage", {})
        self._state = {
            "favorites": [str(v) for v in favorites if isinstance(v, str)][
                :STATE_MAX_HISTORY],
            "usage": usage if isinstance(usage, dict) else {},
        }

    def _save_state(self):
        with self._lock:
            payload = json.loads(json.dumps(self._state))
        self._write_json(self.state_path, payload)

    def _load_cache(self):
        raw = self._read_json(self.cache_path, {})
        if (raw.get("version") != INDEX_VERSION
                or raw.get("platform") != self.platform):
            return
        items = {}
        for row in raw.get("items", []):
            if not isinstance(row, dict):
                continue
            try:
                item = SearchItem(**{
                    key: str(row.get(key, ""))
                    for key in SearchItem.__dataclass_fields__
                })
            except (TypeError, ValueError):
                continue
            if item.id and item.kind and item.target:
                items[item.id] = item
        with self._lock:
            self._items = items
            self._updated_at = str(raw.get("updated_at") or "")

    def _save_cache(self, items):
        payload = {
            "version": INDEX_VERSION,
            "platform": self.platform,
            "updated_at": self._updated_at,
            "items": [asdict(item) for item in items.values()],
        }
        self._write_json(self.cache_path, payload)

    def _cache_is_fresh(self):
        try:
            return (time.time() - self.cache_path.stat().st_mtime
                    < INDEX_MAX_AGE_SECONDS)
        except OSError:
            return False

    def start_refresh(self, force=False):
        if not self.supported:
            return {"ok": False, "supported": False,
                    "message": "Mumble Find is currently available on Windows."}
        with self._lock:
            if self._refreshing:
                return {"ok": True, "refreshing": True}
            if not force and self._items and self._cache_is_fresh():
                return {"ok": True, "refreshing": False, "cached": True}
            self._refreshing = True

        def worker():
            try:
                self.refresh()
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)[:300]
                    self._refreshing = False

        threading.Thread(
            target=worker, name="mumble-system-search-index", daemon=True
        ).start()
        return {"ok": True, "refreshing": True}

    def refresh(self):
        if not self.supported:
            return self.status()
        with self._lock:
            self._refreshing = True
            self._last_error = ""
        items = {}
        try:
            for item in self._discover_applications():
                items[item.id] = item
                if len(items) >= self.max_items:
                    break
            with self._lock:
                self._items = items
                self._ephemeral.clear()
                self._updated_at = _utc_now()
                self._refreshing = False
                self.invalidate_icon_cache()
            self._save_cache(items)
        except Exception as exc:
            with self._lock:
                self._refreshing = False
                self._last_error = str(exc)[:300]
            raise
        return self.status()

    def _file_roots(self):
        if self._file_roots_override is not None:
            candidates = list(self._file_roots_override)
        else:
            configured = _setting(self.settings, "system_search_roots", [])
            candidates = list(configured) if isinstance(configured, list) else []
            candidates.extend(self.home / name for name in (
                "Desktop", "Documents", "Downloads", "Pictures", "Music",
                "Videos",
            ))
        result = []
        seen = set()
        for candidate in candidates:
            try:
                path = Path(candidate).expanduser()
                key = os.path.normcase(os.path.abspath(str(path)))
            except (OSError, TypeError, ValueError):
                continue
            if key in seen or not path.is_dir():
                continue
            seen.add(key)
            result.append(path)
        return result

    def _application_roots(self):
        if self._app_roots_override is not None:
            return [Path(value).expanduser() for value in self._app_roots_override]
        if self.platform == "windows":
            values = [self.home / "Desktop"]
            for env_name, suffix in (
                ("APPDATA", "Microsoft/Windows/Start Menu/Programs"),
                ("PROGRAMDATA", "Microsoft/Windows/Start Menu/Programs"),
                ("PUBLIC", "Desktop"),
            ):
                base = os.environ.get(env_name)
                if base:
                    values.append(Path(base) / suffix)
        elif self.platform == "macos":
            values = [
                self.home / "Applications",
                Path("/Applications"),
                Path("/System/Applications"),
            ]
        else:
            values = [
                self.home / ".local/share/applications",
                Path("/usr/local/share/applications"),
                Path("/usr/share/applications"),
            ]
        return [path for path in values if str(path) and path.is_dir()]

    def _discover_applications(self):
        seen = set()
        seen_names = set()
        for root in self._application_roots():
            if not root.is_dir():
                continue
            if self.platform == "linux":
                iterator = root.rglob("*.desktop")
            elif self.platform == "macos":
                iterator = (
                    path for path in root.rglob("*.app")
                    if path.is_dir()
                )
            else:
                iterator = (
                    path for path in root.rglob("*")
                    if path.is_file() and path.suffix.lower() in APP_SUFFIXES
                )
            try:
                for path in iterator:
                    if self.platform == "linux":
                        item = self._linux_desktop_item(path)
                    elif self.platform == "macos":
                        item = SearchItem.make(
                            "app", path.stem, str(path), "Application", "spotlight-app",
                            icon_hint=str(path),
                        )
                    else:
                        name = path.stem
                        item = SearchItem.make(
                            "app", name, str(path), str(path.parent),
                            "start-menu",
                        )
                    if item is None:
                        continue
                    key = os.path.normcase(item.target)
                    name_key = _normalise(item.name)
                    if key in seen or (name_key and name_key in seen_names):
                        continue
                    seen.add(key)
                    if name_key:
                        seen_names.add(name_key)
                    yield item
            except (OSError, PermissionError):
                continue
        if self.platform == "windows":
            for item in self._windows_registry_apps():
                key = os.path.normcase(item.target)
                name_key = _normalise(item.name)
                if key not in seen and (not name_key or name_key not in seen_names):
                    seen.add(key)
                    if name_key:
                        seen_names.add(name_key)
                    yield item
            for item in self._windows_store_apps():
                key = os.path.normcase(item.target)
                name_key = _normalise(item.name)
                if key not in seen and (not name_key or name_key not in seen_names):
                    seen.add(key)
                    if name_key:
                        seen_names.add(name_key)
                    yield item

    def _windows_registry_apps(self):
        try:
            import winreg
        except ImportError:
            return []
        results = []
        roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
        base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0),
                 getattr(winreg, "KEY_WOW64_32KEY", 0))
        seen = set()
        for hive in roots:
            for view in views:
                try:
                    with winreg.OpenKey(hive, base, 0,
                                        winreg.KEY_READ | view) as key:
                        count = winreg.QueryInfoKey(key)[0]
                        for index in range(count):
                            subname = winreg.EnumKey(key, index)
                            try:
                                with winreg.OpenKey(key, subname) as subkey:
                                    target = str(winreg.QueryValue(subkey, None) or "")
                            except OSError:
                                continue
                            target = target.strip().strip('"')
                            norm = os.path.normcase(target)
                            if not target or norm in seen or not os.path.isfile(target):
                                continue
                            seen.add(norm)
                            results.append(SearchItem.make(
                                "app", Path(target).stem, target,
                                "Registered application", "registry",
                                keywords=subname,
                            ))
                except OSError:
                    continue
        return results

    def _windows_store_apps(self):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            return []
        command = (
            "Get-StartApps | Select-Object Name,AppID | "
            "ConvertTo-Json -Compress"
        )
        try:
            completed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-Command", command],
                capture_output=True, text=True, timeout=8, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode or not completed.stdout.strip():
                return []
            rows = json.loads(completed.stdout)
            if isinstance(rows, dict):
                rows = [rows]
            return [
                SearchItem.make(
                    "app", row.get("Name"),
                    "shell:AppsFolder\\" + str(row.get("AppID")),
                    "Microsoft Store app", "start-apps",
                )
                for row in rows if isinstance(row, dict)
                and row.get("Name") and row.get("AppID")
            ]
        except (OSError, ValueError, subprocess.SubprocessError):
            return []

    @staticmethod
    def _desktop_fields(path):
        fields = {}
        in_entry = False
        try:
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if line.startswith("["):
                    in_entry = line == "[Desktop Entry]"
                    continue
                if not in_entry or not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key not in fields:
                    fields[key] = value.strip()
        except OSError:
            return {}
        return fields

    def _linux_desktop_item(self, path):
        fields = self._desktop_fields(path)
        if (fields.get("Type", "Application") != "Application"
                or fields.get("Hidden", "false").lower() == "true"
                or fields.get("NoDisplay", "false").lower() == "true"):
            return None
        name = fields.get("Name") or path.stem
        keywords = " ".join((fields.get("Keywords", ""),
                             fields.get("GenericName", "")))
        return SearchItem.make(
            "app", name, str(path), fields.get("Comment") or "Application",
            "desktop-entry", keywords=keywords,
            icon_hint=fields.get("Icon", ""),
        )

    def status(self):
        provider = self._provider_status()
        provider_work = self._provider_work_status()
        with self._lock:
            counts = {"app": 0, "file": 0, "folder": 0}
            for item in self._items.values():
                if item.kind in counts:
                    counts[item.kind] += 1
            return {
                "ok": self.supported,
                "supported": self.supported,
                "platform": self.platform,
                "platform_label": (
                    "Windows" if self.platform == "windows" else
                    "Linux" if self.platform == "linux" else
                    "macOS" if self.platform == "macos" else "Unsupported"
                ),
                "refreshing": self._refreshing,
                "updated_at": self._updated_at,
                "last_error": self._last_error,
                "counts": counts,
                "total": len(self._items),
                "roots": [],
                "hotkey": _setting(
                    self.settings, "search_hotkey",
                    "ctrl+option+f" if self.platform == "macos" else "ctrl+alt+f",
                ),
                "file_provider": provider,
                "provider_work": provider_work,
                "icon_version": self.icon_version,
                "message": (
                    "Mumble Find is unavailable on this platform."
                    if not self.supported else ""
                ),
            }

    def _provider_work_status(self):
        with self._query_lock:
            futures = list(self._provider_futures.values())
            closed = self._closed
        provider_status = {}
        work_status = getattr(self._file_provider, "work_status", None)
        if callable(work_status):
            try:
                provider_status = work_status()
            except Exception:
                provider_status = {}
        executor_threads = sum(
            thread.is_alive()
            for thread in getattr(self._query_executor, "_threads", ())
        )
        return {
            "capacity": PROVIDER_MAX_INFLIGHT,
            "submitted": len(futures),
            "owned": len(futures),
            "running": sum(future.running() for future in futures),
            "queued": sum(
                not future.running() and not future.done() for future in futures
            ),
            "pending": sum(
                not future.running() and not future.done() for future in futures
            ),
            "threads": executor_threads + int(provider_status.get("threads", 0)),
            "processes": int(provider_status.get("processes", 0)),
            "executors": int(provider_status.get(
                "executors", 0 if closed else bool(self._query_executor)
            )),
            "ownership_entries": len(futures),
        }

    def _provider_status(self):
        if not bool(_setting(
                self.settings, "system_search_include_files", True)):
            return {
                "available": False,
                "state": "disabled",
                "name": "Indexed file search",
                "message": "File and folder results are turned off.",
            }
        provider = self._file_provider
        if provider is None:
            return {
                "available": False,
                "state": "error",
                "name": "Indexed file search",
                "message": (
                    "Indexed file and folder search is not available on this "
                    "platform yet; installed applications remain searchable."
                ),
            }
        try:
            result = provider.status()
            return result if isinstance(result, dict) else {
                "available": True, "state": "ready",
                "name": "Indexed file search", "message": "",
            }
        except Exception as exc:
            return {
                "available": False,
                "state": "error",
                "name": "Indexed file search",
                "message": str(exc)[:200],
            }

    @staticmethod
    def _subsequence_score(query, text):
        if not query or not text:
            return None
        positions = []
        start = 0
        for char in query:
            pos = text.find(char, start)
            if pos < 0:
                return None
            positions.append(pos)
            start = pos + 1
        span = positions[-1] - positions[0] + 1
        density = len(query) / max(1, span)
        early = 1 / (1 + positions[0] * 0.15)
        return 38 + 18 * density + 6 * early

    @classmethod
    def _text_score(cls, query, item):
        name = _normalise(item.name)
        subtitle = _normalise(item.subtitle)
        keywords = _normalise(item.keywords)
        haystack = " ".join(value for value in (name, keywords, subtitle) if value)
        tokens = query.split()
        if not tokens:
            return 0.0
        if query == name:
            return 160.0
        if name.startswith(query):
            return 138.0 - min(12.0, (len(name) - len(query)) * 0.15)
        if query in name:
            return 112.0 - min(18.0, name.index(query) * 0.7)
        words = name.split()
        acronym = "".join(word[0] for word in words if word)
        token_scores = []
        for token in tokens:
            if any(word == token for word in words):
                score = 112.0
            elif any(word.startswith(token) for word in words):
                score = 101.0
            elif token in name:
                score = 86.0
            elif token in keywords:
                score = 73.0
            elif token in subtitle:
                score = 59.0
            elif acronym.startswith(token):
                score = 68.0
            else:
                score = cls._subsequence_score(token, haystack)
                if score is None:
                    return None
            token_scores.append(score)
        return sum(token_scores) / len(token_scores)

    def _usage_boost(self, item_id):
        usage = self._state.get("usage", {}).get(item_id, {})
        if not isinstance(usage, dict):
            return 0.0
        try:
            count = max(0, int(usage.get("count", 0)))
        except (TypeError, ValueError):
            count = 0
        try:
            age_hours = max(0.0, (time.time() - float(usage.get("last", 0))) / 3600)
        except (TypeError, ValueError):
            age_hours = 9999.0
        return min(22.0, math.log1p(count) * 7.5) + max(0.0, 14.0 - age_hours / 12)

    def _actions_for(self, item):
        actions = ["open", "favorite"]
        if item.kind in {"file", "folder", "app"} and item.source not in {"start-apps"}:
            actions.append("reveal")
        if self.platform in {"windows", "macos"} and item.kind in {"file", "folder"}:
            actions.append("drag")
        if item.source not in {"start-apps"}:
            actions.append("copy_path")
        return actions

    def _public_result(self, item, score=0.0):
        favorites = set(self._state.get("favorites", []))
        meta = self._display_meta(item)
        return {
            "id": item.id,
            "kind": item.kind,
            "name": item.name,
            # Public metadata is deliberately friendly and path-free. The real
            # target never crosses the bridge; execution still uses the opaque id.
            "subtitle": meta,
            "meta": meta,
            "source": item.source,
            "score": round(float(score), 3),
            "favorite": item.id in favorites,
            "actions": self._actions_for(item),
        }

    @staticmethod
    def _display_meta(item):
        if item.kind == "app":
            return "Windows app" if item.source == "start-apps" else "Application"
        if item.kind == "folder":
            return "Folder"
        suffix = Path(item.name).suffix.lower().lstrip(".")
        labels = {
            "doc": "Word document", "docx": "Word document",
            "xls": "Excel workbook", "xlsx": "Excel workbook",
            "ppt": "PowerPoint presentation", "pptx": "PowerPoint presentation",
            "pdf": "PDF document", "txt": "Text document",
            "md": "Markdown document", "png": "PNG image",
            "jpg": "JPEG image", "jpeg": "JPEG image", "gif": "GIF image",
            "mp3": "Audio file", "wav": "Audio file", "mp4": "Video file",
        }
        return labels.get(suffix, f"{suffix.upper()} file" if suffix else "File")

    def cancel(self, generation):
        try:
            generation = int(generation)
        except (TypeError, ValueError):
            return False
        with self._query_lock:
            token = self._query_tokens.get(generation)
            if token is not None:
                token.cancel()
            future = self._provider_futures.get(generation)
            if future is not None:
                future.cancel()
        provider = self._file_provider
        if provider is not None:
            try:
                provider.cancel(generation)
            except Exception:
                pass
        return token is not None

    def _begin_query(self, generation):
        with self._query_lock:
            if self._closed:
                return self._query_generation, None
            if generation is None:
                generation = self._query_generation + 1
            try:
                generation = int(generation)
            except (TypeError, ValueError):
                generation = self._query_generation + 1
            if generation <= self._query_generation:
                return generation, None
            older = list(self._query_tokens)
            self._query_generation = generation
            token = QueryCancellation(generation)
            self._query_tokens[generation] = token
        for old_generation in older:
            if old_generation < generation:
                self.cancel(old_generation)
        with self._query_lock:
            for old_generation in older:
                if old_generation < generation:
                    self._query_tokens.pop(old_generation, None)
        return generation, token

    def _stale_query_result(self, generation, message="Superseded query"):
        return {
            "ok": False,
            "supported": self.supported,
            "generation": generation,
            "stale": True,
            "results": [],
            "total_matches": 0,
            "provider_state": "cancelled",
            "message": message,
            "icon_version": self.icon_version,
        }

    @staticmethod
    def _provider_item(value):
        if isinstance(value, SearchItem):
            return value if value.kind in {"file", "folder"} else None
        if not isinstance(value, dict):
            return None
        kind = str(value.get("kind") or "file").lower()
        target = str(value.get("target") or "")
        if kind not in {"file", "folder"} or not target:
            return None
        return SearchItem.make(
            kind,
            value.get("name") or Path(target).name,
            target,
            value.get("subtitle") or "",
            value.get("source") or "windows-search",
            keywords=value.get("keywords") or "",
        )

    def _query_file_provider(self, query, category, candidate_limit,
                             deadline, token, generation):
        if (not query or category == "app" or not bool(_setting(
                self.settings, "system_search_include_files", True))):
            return {"items": [], "state": "complete", "message": ""}
        provider = self._file_provider
        if provider is None:
            return {
                "items": [], "state": "error",
                "message": (
                    "Indexed file and folder search is unavailable; installed "
                    "applications remain searchable."
                ),
            }
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            return {
                "items": [], "state": "partial",
                "message": (
                    "Indexed file search reached the query deadline; installed "
                    "applications remain usable."
                ),
                "deadline_exceeded": True,
            }
        if not self._provider_slots.acquire(blocking=False):
            return {
                "items": [], "state": "partial",
                "message": (
                    "Indexed file search is still completing earlier work; "
                    "installed applications remain usable."
                ),
                "capacity_exhausted": True,
            }
        try:
            submit = getattr(provider, "submit", None)
            if callable(submit):
                future = submit(
                    query,
                    limit=candidate_limit,
                    deadline=deadline,
                    cancellation=token,
                    generation=generation,
                )
            else:
                future = self._query_executor.submit(
                    provider.query,
                    query,
                    limit=candidate_limit,
                    deadline=deadline,
                    cancellation=token,
                    generation=generation,
                )
        except Exception as exc:
            self._provider_slots.release()
            return {
                "items": [], "state": "error",
                "message": (
                    "Indexed file search could not start; installed applications "
                    f"remain searchable. {str(exc)[:160]}"
                ),
            }
        with self._query_lock:
            self._provider_futures[generation] = future

        def release_ownership(completed):
            release = False
            with self._query_lock:
                if self._provider_futures.get(generation) is completed:
                    self._provider_futures.pop(generation, None)
                    release = True
            if release:
                self._provider_slots.release()

        future.add_done_callback(release_ownership)
        try:
            result = future.result(timeout=max(
                0.0, deadline - time.monotonic()
            ))
        except FutureTimeout:
            future.cancel()
            try:
                provider.cancel(generation)
            except Exception:
                pass
            return {
                "items": [], "state": "partial",
                "message": (
                    "Indexed file search reached the query deadline; installed "
                    "applications remain usable."
                ),
                "deadline_exceeded": True,
            }
        except Exception as exc:
            return {
                "items": [], "state": "error",
                "message": (
                    "Indexed file search is unavailable; installed applications "
                    f"remain searchable. {str(exc)[:160]}"
                ),
            }
        return result if isinstance(result, dict) else {
            "items": [], "state": "error",
            "message": "Indexed file search returned an invalid response.",
        }

    def search(self, query="", category="all", limit=DEFAULT_RESULT_LIMIT,
               generation=None, deadline_ms=DEFAULT_QUERY_DEADLINE_MS):
        if not self.supported:
            return {"ok": False, "supported": False, "results": [],
                    "message": "Mumble Find is unavailable on this platform."}
        generation, token = self._begin_query(generation)
        if token is None:
            with self._query_lock:
                closed = self._closed
            if closed:
                return {
                    "ok": False, "supported": self.supported,
                    "generation": generation, "stale": False, "results": [],
                    "message": "Mumble Find search is closed.",
                }
            return self._stale_query_result(generation)
        started = time.monotonic()
        try:
            deadline_ms = max(10, min(5_000, int(deadline_ms)))
        except (TypeError, ValueError):
            deadline_ms = DEFAULT_QUERY_DEADLINE_MS
        deadline = started + deadline_ms / 1000.0
        # Keep a long-lived WebUI useful without a watcher service: the first
        # query after the cache window refreshes only the app catalogue.
        if self._background_refresh_enabled:
            self.start_refresh(force=False)
        query = str(query or "").strip()[:300]
        category = str(category or "all").strip().lower()
        aliases = {"apps": "app", "files": "file", "folders": "folder"}
        category = aliases.get(category, category)
        prefixes = {"app:": "app", "file:": "file", "folder:": "folder"}
        lowered = query.casefold()
        for prefix, value in prefixes.items():
            if lowered.startswith(prefix):
                category = value
                query = query[len(prefix):].strip()
                break
        if category not in {"all", "app", "file", "folder"}:
            category = "all"
        try:
            limit = max(1, min(DEFAULT_RESULT_LIMIT, int(limit)))
        except (TypeError, ValueError):
            limit = DEFAULT_RESULT_LIMIT
        norm_query = _normalise(query)
        with self._lock:
            items = [item for item in self._items.values() if item.kind == "app"]
            favorites = set(self._state.get("favorites", []))
            refreshing = self._refreshing
        provider_result = self._query_file_provider(
            query,
            category,
            min(PROVIDER_CANDIDATE_LIMIT, max(12, limit * 20)),
            deadline,
            token,
            generation,
        )
        if token.cancelled or generation != self._query_generation:
            return self._stale_query_result(generation)
        provider_items = []
        for value in provider_result.get("items", []):
            item = self._provider_item(value)
            if item is not None:
                provider_items.append(item)
        items.extend(provider_items)
        ranked = []
        for item in items:
            if token.cancelled:
                break
            if category != "all" and item.kind != category:
                continue
            if not norm_query and category == "all" and item.kind != "app":
                continue
            score = self._text_score(norm_query, item) if norm_query else 0.0
            if score is None:
                continue
            score += self._usage_boost(item.id)
            if item.id in favorites:
                score += 34.0
            if category == "all" and item.kind == "app":
                score += 8.0
            ranked.append((score, item))
        ranked.sort(key=lambda row: (-row[0], row[1].name.casefold(), row[1].id))
        if token.cancelled or generation != self._query_generation:
            return self._stale_query_result(generation)
        selected = ranked[:limit]
        results = [self._public_result(item, score) for score, item in selected]
        with self._lock:
            self._ephemeral = {
                item.id: item for _score, item in selected if item.kind != "app"
            }
        provider_state = str(provider_result.get("state") or "complete")
        message = str(provider_result.get("message") or "")
        deadline_exceeded = bool(
            provider_result.get("deadline_exceeded")
            or time.monotonic() >= deadline
        )
        if deadline_exceeded and provider_state == "complete":
            provider_state = "partial"
            message = message or (
                "The query deadline was reached; installed applications "
                "remain usable."
            )
        complete_error = bool(
            provider_state == "error" and not results and category != "app"
        )
        return {
            "ok": not complete_error,
            "supported": True,
            "query": query,
            "category": category,
            "generation": generation,
            "stale": False,
            "deadline_ms": deadline_ms,
            "deadline_exceeded": deadline_exceeded,
            "refreshing": refreshing,
            "results": results,
            "total_matches": len(ranked),
            "provider_state": provider_state,
            "partial": provider_state == "partial",
            "capacity_exhausted": bool(
                provider_result.get("capacity_exhausted")
            ),
            "message": message,
            "icon_version": self.icon_version,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }

    def close(self):
        """Stop accepting work and release bounded search/icon ownership."""
        with self._query_lock:
            if self._closed:
                return
            self._closed = True
            tokens = list(self._query_tokens.values())
            futures = list(self._provider_futures.values())
        for token in tokens:
            token.cancel()
            provider = self._file_provider
            if provider is not None:
                try:
                    provider.cancel(token.generation)
                except Exception:
                    pass
        provider = self._file_provider
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass
        for future in futures:
            future.cancel()
        unfinished = set()
        if futures:
            _finished, unfinished = wait(futures, timeout=0.5)
        if self._query_executor is not None:
            self._query_executor.shutdown(
                wait=not bool(unfinished), cancel_futures=True
            )
        self._icon_executor.shutdown(wait=False, cancel_futures=True)
        with self._query_lock:
            self._query_tokens.clear()


    @property
    def icon_version(self):
        with self._lock:
            return f"{ICON_CACHE_VERSION}:{self._icon_epoch}"

    def invalidate_icon_cache(self):
        with self._lock:
            self._icon_epoch += 1
            self._icon_cache.clear()
            return f"{ICON_CACHE_VERSION}:{self._icon_epoch}"

    @staticmethod
    def _icon_fingerprint(item):
        try:
            stat = Path(item.target).stat()
            source = f"{item.target}\0{stat.st_mtime_ns}\0{stat.st_size}"
        except OSError:
            source = item.target
        return hashlib.sha256(source.encode("utf-8", "replace")).hexdigest()[:16]

    def _extract_icon(self, item):
        return (
            self._windows_icon_data(item.target)
            if self.platform == "windows"
            else self._macos_icon_data(item)
            if self.platform == "macos"
            else self._linux_icon_data(item)
        )

    def icons(self, result_ids, generation=None, icon_version=None):
        """Hydrate visible app icons with bounded, versioned worker ownership."""
        if self.platform not in {"windows", "linux", "macos"} or not isinstance(
                result_ids, (list, tuple)):
            return {"ok": True, "icons": {}, "stale": False}
        with self._query_lock:
            current_generation = self._query_generation
            token = self._query_tokens.get(current_generation)
        try:
            generation = (current_generation if generation is None
                          else int(generation))
        except (TypeError, ValueError):
            generation = current_generation
        current_version = self.icon_version
        icon_version = str(icon_version or current_version)
        if (generation != current_generation or icon_version != current_version
                or (token is not None and token.cancelled)):
            return {
                "ok": True, "icons": {}, "stale": True,
                "generation": generation, "icon_version": current_version,
            }
        requested = []
        for raw in result_ids[:ICON_BATCH_LIMIT]:
            item_id = str(raw or "")
            if item_id and item_id not in requested:
                requested.append(item_id)
        with self._lock:
            items = {
                item_id: self._items.get(item_id)
                for item_id in requested
                if self._items.get(item_id) is not None
                and self._items[item_id].kind == "app"
            }
            cached = {}
            misses = []
            for item_id, item in items.items():
                key = (current_version, item_id, self._icon_fingerprint(item))
                icon = self._icon_cache.get(key)
                if icon:
                    self._icon_cache.move_to_end(key)
                    cached[item_id] = icon
                else:
                    misses.append((item_id, item, key))
        futures = {
            self._icon_executor.submit(self._extract_icon, item): (item_id, key)
            for item_id, item, key in misses
        }
        output = dict(cached)
        pending = set(futures)
        while pending:
            with self._query_lock:
                cancelled = (
                    generation != self._query_generation
                    or icon_version != self.icon_version
                    or (token is not None and token.cancelled)
                )
            if cancelled:
                for future in pending:
                    future.cancel()
                break
            done, pending = wait(
                pending, timeout=0.01, return_when=FIRST_COMPLETED)
            for future in done:
                item_id, key = futures[future]
                try:
                    icon = future.result()
                except Exception:
                    icon = ""
                if icon:
                    output[item_id] = icon
                    with self._lock:
                        self._icon_cache[key] = icon
                        self._icon_cache.move_to_end(key)
                        while len(self._icon_cache) > ICON_CACHE_LIMIT:
                            self._icon_cache.popitem(last=False)
        with self._query_lock:
            stale = (
                generation != self._query_generation
                or icon_version != self.icon_version
                or (token is not None and token.cancelled)
            )
        return {
            "ok": True,
            "icons": {} if stale else output,
            "stale": stale,
            "generation": generation,
            "icon_version": self.icon_version,
        }

    @staticmethod
    def _image_file_data(path):
        """Encode a small local image without ever depending on the network."""
        try:
            path = Path(path)
            if not path.is_file() or path.stat().st_size > 1_500_000:
                return ""
            suffix = path.suffix.casefold()
            mime = {
                ".png": "image/png", ".svg": "image/svg+xml",
                ".webp": "image/webp", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }.get(suffix)
            if mime:
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return f"data:{mime};base64,{encoded}"
            from PIL import Image
            with Image.open(path) as source:
                image = source.convert("RGBA")
                image.thumbnail((64, 64), Image.Resampling.LANCZOS)
                return SystemSearchEngine._png_data(image)
        except Exception:
            return ""

    @staticmethod
    def _png_data(image):
        payload = io.BytesIO()
        image.save(payload, format="PNG", optimize=True)
        encoded = base64.b64encode(payload.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}" if len(encoded) < 120_000 else ""

    @staticmethod
    def _windows_shortcut_candidates(target):
        """Resolve shortcut metadata when the shell entry itself has no icon."""
        candidates = []
        path = Path(str(target))
        try:
            if path.suffix.casefold() == ".lnk" and path.is_file():
                from win32com.client import Dispatch
                shortcut = Dispatch("WScript.Shell").CreateShortcut(str(path))
                location = str(getattr(shortcut, "IconLocation", "") or "")
                if location:
                    icon_path = location.rsplit(",", 1)[0].strip().strip('"')
                    if icon_path:
                        candidates.append(icon_path)
                destination = str(getattr(shortcut, "TargetPath", "") or "")
                if destination:
                    candidates.append(destination)
            elif path.suffix.casefold() == ".url" and path.is_file():
                for line in path.read_text(
                        encoding="utf-8", errors="replace").splitlines():
                    if line.casefold().startswith("iconfile="):
                        candidates.append(line.split("=", 1)[1].strip())
                        break
        except Exception:
            pass
        return candidates

    @staticmethod
    def _windows_shell_hicon(target):
        """Ask Explorer for the exact icon, including virtual Start-app items."""
        try:
            from win32com.shell import shell, shellcon
            flags = shellcon.SHGFI_ICON | shellcon.SHGFI_LARGEICON
            value = str(target)
            if value.casefold().startswith("shell:"):
                pidl, _attrs = shell.SHParseDisplayName(value, 0)
                info = shell.SHGetFileInfo(
                    pidl, 0, flags | shellcon.SHGFI_PIDL)
            else:
                info = shell.SHGetFileInfo(value, 0, flags)
            return info[1][0] if info and len(info) > 1 and info[1] else None
        except Exception:
            return None

    @staticmethod
    def _draw_windows_hicon(hicon, background):
        """Draw into a top-down DIB so icons can never be vertically flipped."""
        import ctypes
        from ctypes import wintypes
        from PIL import Image

        class BitmapInfoHeader(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BitmapInfo(ctypes.Structure):
            _fields_ = [("bmiHeader", BitmapInfoHeader),
                        ("bmiColors", wintypes.DWORD * 3)]

        size = 32
        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = size
        # Negative height is the important detail: rows are stored top-to-bottom.
        info.bmiHeader.biHeight = -size
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC, ctypes.POINTER(BitmapInfo), wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
        ]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        user32.DrawIconEx.argtypes = [
            wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON,
            ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH,
            wintypes.UINT,
        ]
        user32.DrawIconEx.restype = wintypes.BOOL
        dc = gdi32.CreateCompatibleDC(None)
        bitmap = gdi32.CreateDIBSection(
            dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not dc or not bitmap or not bits.value:
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if dc:
                gdi32.DeleteDC(dc)
            return None
        old = gdi32.SelectObject(dc, bitmap)
        try:
            blue, green, red = background
            pixel = bytes((blue, green, red, 255))
            ctypes.memmove(bits.value, pixel * (size * size), size * size * 4)
            if not user32.DrawIconEx(
                    dc, 0, 0, int(hicon), size, size, 0, None, 0x0003):
                return None
            raw = ctypes.string_at(bits.value, size * size * 4)
            return Image.frombytes("RGBA", (size, size), raw, "raw", "BGRA")
        finally:
            gdi32.SelectObject(dc, old)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(dc)

    @classmethod
    def _windows_icon_data(cls, target):
        """Return Explorer's correctly oriented icon for a path or Start app."""
        try:
            from PIL import Image
            import win32gui

            candidates = [str(target), *cls._windows_shortcut_candidates(target)]
            seen = set()
            for candidate in candidates:
                key = candidate.casefold()
                if not candidate or key in seen:
                    continue
                seen.add(key)
                hicon = cls._windows_shell_hicon(candidate)
                if not hicon:
                    direct = cls._image_file_data(candidate)
                    if direct:
                        return direct
                    continue
                try:
                    # Drawing once over black and once over white recovers a clean
                    # alpha channel even for legacy icons whose DIB alpha is empty.
                    black = cls._draw_windows_hicon(hicon, (0, 0, 0))
                    white = cls._draw_windows_hicon(hicon, (255, 255, 255))
                    if black is None or white is None:
                        continue
                    pixels = []
                    dark_pixels = black.load()
                    light_pixels = white.load()
                    for y in range(black.height):
                        for x in range(black.width):
                            dark = dark_pixels[x, y]
                            light = light_pixels[x, y]
                            spread = sum(max(0, light[i] - dark[i])
                                         for i in range(3)) / 3
                            alpha = max(0, min(255, round(255 - spread)))
                            if alpha:
                                rgb = tuple(max(0, min(255, round(
                                    dark[i] * 255 / alpha))) for i in range(3))
                            else:
                                rgb = (0, 0, 0)
                            pixels.append((*rgb, alpha))
                    image = Image.new("RGBA", black.size)
                    image.putdata(pixels)
                    if image.getbbox():
                        return cls._png_data(image)
                finally:
                    win32gui.DestroyIcon(hicon)
        except Exception:
            return ""
        return ""

    def _linux_icon_data(self, item):
        hint = item.icon_hint
        if not hint and item.source == "desktop-entry":
            hint = self._desktop_fields(Path(item.target)).get("Icon", "")
        if not hint:
            return ""
        hint_path = Path(hint).expanduser()
        if hint_path.is_absolute():
            return self._image_file_data(hint_path)
        key = Path(hint).stem.casefold()
        cached = self._linux_icon_paths.get(key)
        if cached is not None:
            return self._image_file_data(cached) if cached else ""
        bases = [
            self.home / ".local/share/icons", self.home / ".icons",
            self.home / ".local/share/flatpak/exports/share/icons",
            Path("/usr/local/share/icons"), Path("/usr/share/icons"),
            Path("/var/lib/flatpak/exports/share/icons"),
            Path("/usr/share/pixmaps"),
        ]
        size_paths = (
            "scalable/apps", "256x256/apps", "128x128/apps", "96x96/apps",
            "64x64/apps", "48x48/apps", "32x32/apps",
        )
        names = [hint] if Path(hint).suffix else [
            f"{hint}.png", f"{hint}.svg", f"{hint}.webp", f"{hint}.xpm",
        ]
        found = None
        for base in bases:
            if not base.is_dir():
                continue
            direct = [base / name for name in names]
            themed = []
            try:
                for theme in base.iterdir():
                    if not theme.is_dir():
                        continue
                    themed.extend(theme / size_path / name
                                  for size_path in size_paths for name in names)
            except OSError:
                pass
            found = next((path for path in (*direct, *themed) if path.is_file()), None)
            if found:
                break
        self._linux_icon_paths[key] = found
        return self._image_file_data(found) if found else ""

    @classmethod
    def _macos_icon_data(cls, item):
        try:
            from AppKit import NSWorkspace
            from PIL import Image
            icon = NSWorkspace.sharedWorkspace().iconForFile_(str(item.target))
            data = icon.TIFFRepresentation() if icon is not None else None
            if data is None:
                return ""
            with Image.open(io.BytesIO(bytes(data))) as source:
                image = source.convert("RGBA")
                image.thumbnail((64, 64), Image.Resampling.LANCZOS)
                return cls._png_data(image)
        except Exception:
            return ""

    def toggle_favorite(self, item_id, favorite=None):
        item_id = str(item_id or "")
        with self._lock:
            if item_id not in self._items and item_id not in self._ephemeral:
                return {"ok": False, "message": "That search result is no longer indexed."}
            favorites = list(self._state.get("favorites", []))
            current = item_id in favorites
            want = (not current) if favorite is None else bool(favorite)
            favorites = [value for value in favorites if value != item_id]
            if want:
                favorites.insert(0, item_id)
            self._state["favorites"] = favorites[:STATE_MAX_HISTORY]
        self._save_state()
        return {"ok": True, "favorite": want}

    def _record_use(self, item_id):
        with self._lock:
            usage = self._state.setdefault("usage", {})
            row = usage.get(item_id, {})
            try:
                count = int(row.get("count", 0)) + 1
            except (TypeError, ValueError, AttributeError):
                count = 1
            usage[item_id] = {"count": count, "last": time.time()}
            if len(usage) > STATE_MAX_HISTORY:
                keep = sorted(
                    usage, key=lambda key: usage[key].get("last", 0), reverse=True
                )[:STATE_MAX_HISTORY]
                self._state["usage"] = {key: usage[key] for key in keep}
        self._save_state()

    def execute(self, item_id, action="open"):
        item_id = str(item_id or "")
        action = str(action or "open").lower()
        if action == "favorite":
            return self.toggle_favorite(item_id)
        with self._lock:
            item = self._items.get(item_id) or self._ephemeral.get(item_id)
        if item is None:
            return {"ok": False, "message": "That search result expired. Search again."}
        if action not in self._actions_for(item):
            return {"ok": False, "message": "That action is not available for this result."}
        try:
            if action == "copy_path":
                import pyperclip
                pyperclip.copy(item.target)
            elif action == "drag":
                starter = self._native_drag_starter
                if starter is None:
                    if self.platform == "macos":
                        from .macos_drag import start_macos_file_drag
                        starter = start_macos_file_drag
                    else:
                        from .native_drag import start_windows_shell_drag
                        starter = start_windows_shell_drag
                result = starter(item.target)
                if not isinstance(result, dict):
                    result = {"ok": bool(result)}
                return {
                    **result,
                    "id": item.id,
                    "action": "drag",
                }
            elif action == "reveal":
                self._reveal(item)
            else:
                self._open(item)
                self._record_use(item.id)
            return {"ok": True, "action": action, "id": item.id,
                    "message": "Opened" if action == "open" else "Done"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)[:240] or "The item could not be opened."}

    def _open(self, item):
        if self.platform == "windows":
            if item.source == "start-apps":
                subprocess.Popen(
                    ["explorer.exe", item.target],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                if not os.path.exists(item.target):
                    raise FileNotFoundError("That item has moved. Refresh the search index.")
                os.startfile(item.target)
            return
        if self.platform == "macos":
            target = Path(item.target)
            if not target.exists():
                raise FileNotFoundError("That item has moved. Refresh the search index.")
            subprocess.Popen(["/usr/bin/open", str(target)])
            return
        if item.kind == "app" and item.source == "desktop-entry":
            self._open_linux_desktop(Path(item.target))
            return
        if not os.path.exists(item.target):
            raise FileNotFoundError("That item has moved. Refresh the search index.")
        opener = shutil.which("xdg-open")
        if not opener:
            raise RuntimeError("xdg-open is unavailable on this Linux system.")
        subprocess.Popen([opener, item.target])

    def _open_linux_desktop(self, path):
        if not path.is_file():
            raise FileNotFoundError("That application has moved. Refresh the search index.")
        launcher = shutil.which("gtk-launch")
        if launcher:
            subprocess.Popen([launcher, path.stem])
            return
        fields = self._desktop_fields(path)
        command = fields.get("Exec", "")
        args = [arg for arg in shlex.split(command) if not re.fullmatch(r"%[a-zA-Z]", arg)]
        if not args:
            raise RuntimeError("This application entry has no usable launch command.")
        subprocess.Popen(args)

    def _reveal(self, item):
        target = Path(item.target)
        if not target.exists():
            raise FileNotFoundError("That item has moved. Refresh the search index.")
        if self.platform == "windows":
            subprocess.Popen(
                ["explorer.exe", "/select,", str(target)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        if self.platform == "macos":
            subprocess.Popen(["/usr/bin/open", "-R", str(target)])
            return
        opener = shutil.which("xdg-open")
        if not opener:
            raise RuntimeError("xdg-open is unavailable on this Linux system.")
        subprocess.Popen([opener, str(target if target.is_dir() else target.parent)])


class SystemSearchService:
    """Process-owned engine with window leases and one fixed provider boundary."""

    def __init__(self, engine_factory=None):
        self._engine_factory = engine_factory
        self._lock = threading.RLock()
        self._engine = None
        self._leases = set()
        self._next_lease = 1
        self._engines_created = 0
        self._closed = False

    def acquire(self, *, settings=None, data_dir=None, url_opener=None):
        with self._lock:
            if self._closed:
                raise RuntimeError("Mumble Find search service is closed.")
            if self._engine is None:
                factory = self._engine_factory or SystemSearchEngine
                self._engine = factory(
                    settings=settings,
                    data_dir=data_dir,
                    url_opener=url_opener,
                )
                self._engines_created += 1
            lease = self._next_lease
            self._next_lease += 1
            self._leases.add(lease)
            return self._engine, lease

    def release(self, lease):
        with self._lock:
            self._leases.discard(lease)

    def status(self):
        with self._lock:
            engine = self._engine
            return {
                "closed": self._closed,
                "leases": len(self._leases),
                "engines": int(engine is not None),
                "engines_created": self._engines_created,
                "provider_capacity": PROVIDER_MAX_INFLIGHT,
            }

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            engine = self._engine
            self._leases.clear()
        if engine is not None:
            engine.close()
