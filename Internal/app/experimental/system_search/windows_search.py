"""Bounded Windows Search ``SystemIndex`` provider for Mumble Find."""

from __future__ import annotations

import math
import os
from pathlib import Path
import threading
import time


WINDOWS_SEARCH_PROVIDER = "Search.CollatorDSO.1"
MAX_PROVIDER_RESULTS = 250


def _escape_like(value):
    """Escape a literal for Windows Search SQL LIKE without broad wildcards."""
    replacements = {
        "'": "''", "[": "[[]", "]": "[]]", "%": "[%]", "_": "[_]",
    }
    return "".join(replacements.get(character, character)
                   for character in str(value or ""))


class WindowsSearchProvider:
    """Query the operating system index without copying or walking user files."""

    name = "Windows Search"

    def __init__(self, connection_factory=None):
        self._connection_factory = connection_factory or self._make_connection
        self._active = {}
        self._lock = threading.RLock()
        self._last_error = ""

    @staticmethod
    def _make_connection():
        from win32com.client import Dispatch

        connection = Dispatch("ADODB.Connection")
        connection.ConnectionTimeout = 1
        connection.Open(
            "Provider=Search.CollatorDSO.1;"
            "Extended Properties='Application=Mumble Find';"
        )
        return connection

    def status(self):
        return {
            "available": not bool(self._last_error),
            "state": "error" if self._last_error else "ready",
            "name": self.name,
            "message": self._last_error,
        }

    def cancel(self, generation):
        with self._lock:
            connection = self._active.get(int(generation))
        if connection is None:
            return False
        try:
            connection.Cancel()
            return True
        except Exception:
            return False

    @staticmethod
    def _field(recordset, name):
        try:
            return recordset.Fields.Item(name).Value
        except Exception:
            return None

    def query(self, query, *, limit, deadline, cancellation, generation):
        query = str(query or "").strip()[:120]
        if not query:
            return {"items": [], "state": "complete", "message": ""}
        limit = max(1, min(MAX_PROVIDER_RESULTS, int(limit)))
        remaining = max(0.0, float(deadline) - time.monotonic())
        if cancellation.cancelled or remaining <= 0:
            return {
                "items": [], "state": "partial",
                "message": "The indexed file search reached its deadline.",
            }

        literal = _escape_like(query)
        sql = (
            f"SELECT TOP {limit} System.ItemPathDisplay, System.FileName, "
            "System.ItemNameDisplay, System.ItemType, System.Kind "
            "FROM SystemIndex "
            "WHERE System.ItemPathDisplay IS NOT NULL AND "
            f"(System.FileName LIKE '%{literal}%' OR "
            f"System.ItemNameDisplay LIKE '%{literal}%')"
        )
        connection = None
        recordset = None
        try:
            connection = self._connection_factory()
            try:
                connection.CommandTimeout = max(1, int(math.ceil(remaining)))
            except Exception:
                pass
            with self._lock:
                self._active[int(generation)] = connection
            if cancellation.cancelled:
                return {
                    "items": [], "state": "partial",
                    "message": "The indexed file search was cancelled.",
                }
            recordset = connection.Execute(sql)
            if isinstance(recordset, tuple):
                recordset = recordset[0]
            items = []
            while (recordset is not None and not bool(recordset.EOF)
                   and len(items) < limit):
                if cancellation.cancelled or time.monotonic() >= deadline:
                    return {
                        "items": items, "state": "partial",
                        "message": "The indexed file search reached its deadline.",
                    }
                target = str(self._field(
                    recordset, "System.ItemPathDisplay") or "").strip()
                if target:
                    name = str(
                        self._field(recordset, "System.FileName")
                        or self._field(recordset, "System.ItemNameDisplay")
                        or Path(target).name
                    )
                    item_type = str(
                        self._field(recordset, "System.ItemType") or ""
                    ).casefold()
                    raw_kind = self._field(recordset, "System.Kind") or ""
                    if isinstance(raw_kind, (list, tuple)):
                        system_kinds = {
                            str(value).casefold() for value in raw_kind
                        }
                    else:
                        system_kinds = {str(raw_kind).casefold()}
                    kind = (
                        "folder" if "folder" in system_kinds
                        or item_type in {"directory", "folder", ".folder"}
                        or os.path.isdir(target) else "file"
                    )
                    items.append({
                        "kind": kind,
                        "name": name,
                        "target": target,
                        "subtitle": "Folder" if kind == "folder" else "",
                        "source": "windows-search",
                        "keywords": Path(name).suffix.lstrip("."),
                    })
                recordset.MoveNext()
            self._last_error = ""
            return {"items": items, "state": "complete", "message": ""}
        except Exception as exc:
            message = (
                "Windows Search is unavailable; installed applications remain "
                f"searchable. {str(exc)[:160]}"
            )
            self._last_error = message
            return {"items": [], "state": "error", "message": message}
        finally:
            with self._lock:
                self._active.pop(int(generation), None)
            if recordset is not None:
                try:
                    recordset.Close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.Close()
                except Exception:
                    pass
