"""Bounded history of generated prompts for the Deck.

This intentionally stores completed prompt artefacts only. It does not retain
conversation threads or feed previous prompts back into later generations.
"""

import json
import os
import threading
import time

import branding
from storage_lock import exclusive_file_lock

MAX_REQUEST_CHARS = 6000
MAX_PROMPT_CHARS = 6000
PROMPTS_HISTORY_CAP = 5000


class PromptHistory:
    def __init__(self, history_path=None):
        self.history_path = history_path or os.path.join(
            branding.DATA_DIR, "prompts.json"
        )
        self._history_lock = threading.Lock()
        # Remove the retired hidden continuity store while preserving the
        # user's visible prompts.json Deck history.
        legacy_thread = os.path.join(
            os.path.dirname(self.history_path), "prompt_thread.json"
        )
        try:
            if os.path.exists(legacy_thread):
                os.remove(legacy_thread)
        except OSError as exc:
            print("legacy prompt thread cleanup error:", exc)

    def _read_history(self):
        """Return (entries, ok); never overwrite an unreadable history file."""
        try:
            with open(self.history_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                return [
                    entry for entry in data
                    if isinstance(entry, dict)
                    and isinstance(entry.get("prompt"), str)
                    and entry.get("prompt").strip()
                ], True
            return [], False
        except FileNotFoundError:
            return [], True
        except Exception as exc:
            print("prompts history read error:", exc)
            return [], False

    def _preserve_corrupt_history(self):
        if not os.path.exists(self.history_path):
            return True
        backup = self.history_path + ".corrupt"
        suffix = 1
        while os.path.exists(backup):
            backup = self.history_path + f".corrupt.{suffix}"
            suffix += 1
        try:
            os.replace(self.history_path, backup)
            return True
        except OSError as exc:
            print("prompts history preserve error:", exc)
            return False

    def record(self, request, prompt):
        request = (request or "").strip()[:MAX_REQUEST_CHARS]
        prompt = (prompt or "").strip()[:MAX_PROMPT_CHARS]
        if not request or not prompt:
            return False
        return self._append_history(time.time(), request, prompt)

    def _append_history(self, timestamp, request, prompt):
        with self._history_lock, exclusive_file_lock(self.history_path) as acquired:
            if not acquired:
                return False
            try:
                entries, ok = self._read_history()
                if not ok:
                    if not self._preserve_corrupt_history():
                        return False
                    entries = []
                entries.append({
                    "t": timestamp,
                    "time": time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(timestamp)
                    ),
                    "request": request,
                    "prompt": prompt,
                })
                entries = entries[-PROMPTS_HISTORY_CAP:]
                os.makedirs(os.path.dirname(self.history_path) or ".", exist_ok=True)
                tmp = self.history_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(entries, handle, ensure_ascii=False, indent=1)
                os.replace(tmp, self.history_path)
                return True
            except Exception as exc:
                print("prompts history save error:", exc)
                try:
                    os.remove(self.history_path + ".tmp")
                except OSError:
                    pass
                return False

    def all_prompts(self, newest_first=True):
        entries, _ = self._read_history()
        return list(reversed(entries)) if newest_first else entries

    def clear_history(self):
        with self._history_lock, exclusive_file_lock(self.history_path) as acquired:
            if not acquired:
                return False
            try:
                os.makedirs(os.path.dirname(self.history_path) or ".", exist_ok=True)
                tmp = self.history_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump([], handle)
                os.replace(tmp, self.history_path)
                return True
            except OSError as exc:
                print("prompts history clear error:", exc)
                try:
                    os.remove(self.history_path + ".tmp")
                except OSError:
                    pass
                return False
