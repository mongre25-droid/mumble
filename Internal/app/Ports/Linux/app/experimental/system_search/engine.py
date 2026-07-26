"""Fast, local application-and-file search for Mumble.

The search engine deliberately has a narrow authority boundary: it indexes
known application locations and user folders, returns immutable result ids,
and will only act on an id that came from its own index.  Search text can never
become a command line or an arbitrary path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
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
from urllib.parse import quote_plus
import webbrowser


INDEX_VERSION = 1
DEFAULT_MAX_ITEMS = 75_000
DEFAULT_RESULT_LIMIT = 40
INDEX_MAX_AGE_SECONDS = 15 * 60
STATE_MAX_HISTORY = 250
SKIP_DIRS = frozenset({
    ".cache", ".git", ".hg", ".idea", ".mypy_cache", ".pytest_cache",
    ".svn", ".tox", ".venv", "__pycache__", "appdata", "cache",
    "node_modules", "site-packages", "temp", "tmp", "venv",
})
APP_SUFFIXES = frozenset({".lnk", ".url", ".exe", ".bat", ".cmd"})
WEB_ENGINES = {
    "google": "https://www.google.com/search?q={q}",
    "perplexity": "https://www.perplexity.ai/search?q={q}",
    "brave": "https://search.brave.com/search?q={q}",
}


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

    @classmethod
    def make(cls, kind, name, target, subtitle="", source="local", keywords=""):
        return cls(
            id=_stable_id(kind, target),
            kind=str(kind),
            name=str(name or Path(str(target)).name or target),
            subtitle=str(subtitle or target),
            target=str(target),
            source=str(source or "local"),
            keywords=str(keywords or ""),
        )


class SystemSearchEngine:
    """Bounded local index with fuzzy ranking and safe result actions."""

    def __init__(self, settings=None, data_dir=None, platform=None, home=None,
                 file_roots=None, app_roots=None, max_items=None,
                 start_background=True):
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
        self._lock = threading.RLock()
        self._items = {}
        self._ephemeral = {}
        self._refreshing = False
        self._updated_at = ""
        self._last_error = ""
        self._state = {"favorites": [], "usage": {}}
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
        return "unsupported"

    @property
    def supported(self):
        return self.platform in {"windows", "linux"}

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
                    "message": "Mumble Find is available on Windows and Linux."}
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
            remaining = max(0, self.max_items - len(items))
            if remaining and bool(_setting(
                    self.settings, "system_search_include_files", True)):
                for item in self._discover_files(remaining):
                    items[item.id] = item
                    if len(items) >= self.max_items:
                        break
            with self._lock:
                self._items = items
                self._ephemeral.clear()
                self._updated_at = _utc_now()
                self._refreshing = False
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
            else:
                iterator = (
                    path for path in root.rglob("*")
                    if path.is_file() and path.suffix.lower() in APP_SUFFIXES
                )
            try:
                for path in iterator:
                    if self.platform == "linux":
                        item = self._linux_desktop_item(path)
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
        )

    def _discover_files(self, limit):
        emitted = 0
        for root in self._file_roots():
            try:
                for current, dirs, files in os.walk(root, followlinks=False):
                    dirs[:] = [
                        name for name in dirs
                        if not name.startswith(".") and name.casefold() not in SKIP_DIRS
                    ]
                    current_path = Path(current)
                    for name in dirs:
                        path = current_path / name
                        yield SearchItem.make(
                            "folder", name, str(path), str(current_path), "files"
                        )
                        emitted += 1
                        if emitted >= limit:
                            return
                    for name in files:
                        if name.startswith("."):
                            continue
                        path = current_path / name
                        yield SearchItem.make(
                            "file", name, str(path), str(current_path), "files",
                            keywords=path.suffix.lstrip("."),
                        )
                        emitted += 1
                        if emitted >= limit:
                            return
            except (OSError, PermissionError):
                continue

    def status(self):
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
                    "Linux" if self.platform == "linux" else "Unsupported"
                ),
                "refreshing": self._refreshing,
                "updated_at": self._updated_at,
                "last_error": self._last_error,
                "counts": counts,
                "total": len(self._items),
                "roots": [str(path) for path in self._file_roots()],
                "hotkey": _setting(self.settings, "search_hotkey", "ctrl+alt+s"),
                "message": (
                    "Mumble Find is available on Windows and Linux."
                    if not self.supported else ""
                ),
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

    @staticmethod
    def _actions_for(item):
        if item.kind == "web":
            return ["open"]
        actions = ["open", "favorite"]
        if item.kind in {"file", "app"} and item.source not in {"start-apps"}:
            actions.append("reveal")
        if item.source not in {"start-apps"}:
            actions.append("copy_path")
        return actions

    def _public_result(self, item, score=0.0):
        favorites = set(self._state.get("favorites", []))
        return {
            "id": item.id,
            "kind": item.kind,
            "name": item.name,
            "subtitle": item.subtitle,
            "source": item.source,
            "score": round(float(score), 3),
            "favorite": item.id in favorites,
            "actions": self._actions_for(item),
        }

    def _web_result(self, query):
        engine = str(_setting(self.settings, "search_engine", "perplexity"))
        template = WEB_ENGINES.get(engine, WEB_ENGINES["perplexity"])
        url = template.format(q=quote_plus(query))
        label = engine.title()
        item = SearchItem.make(
            "web", f'Search the web for “{query}”', url,
            f"Open with {label}", "web",
        )
        with self._lock:
            self._ephemeral[item.id] = item
        return item

    def search(self, query="", category="all", limit=DEFAULT_RESULT_LIMIT):
        if not self.supported:
            return {"ok": False, "supported": False, "results": [],
                    "message": "Mumble Find is available on Windows and Linux."}
        # Keep a long-lived WebUI useful without a watcher service: the first
        # query after the 15-minute cache window refreshes in the background.
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
            limit = max(1, min(100, int(limit)))
        except (TypeError, ValueError):
            limit = DEFAULT_RESULT_LIMIT
        norm_query = _normalise(query)
        with self._lock:
            items = list(self._items.values())
            favorites = set(self._state.get("favorites", []))
            refreshing = self._refreshing
        ranked = []
        for item in items:
            if category != "all" and item.kind != category:
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
        results = [self._public_result(item, score) for score, item in ranked[:limit]]
        return {
            "ok": True,
            "supported": True,
            "query": query,
            "category": category,
            "refreshing": refreshing,
            "results": results,
            "total_matches": len(ranked),
        }

    def toggle_favorite(self, item_id, favorite=None):
        item_id = str(item_id or "")
        with self._lock:
            if item_id not in self._items:
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
        if item.kind == "web":
            if not webbrowser.open(item.target):
                raise RuntimeError("The default browser did not accept the search.")
            return
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
            if target.is_dir():
                os.startfile(str(target))
            else:
                subprocess.Popen(
                    ["explorer.exe", "/select,", str(target)],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            return
        opener = shutil.which("xdg-open")
        if not opener:
            raise RuntimeError("xdg-open is unavailable on this Linux system.")
        subprocess.Popen([opener, str(target if target.is_dir() else target.parent)])
