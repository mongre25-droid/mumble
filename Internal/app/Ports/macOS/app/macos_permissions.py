"""Truthful macOS permission state and recovery authority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
import time


class PermissionState(str, Enum):
    READY = "ready"
    DENIED = "denied"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PermissionSnapshot:
    microphone: PermissionState
    accessibility: PermissionState
    input_monitoring: PermissionState
    observed_at: float

    @property
    def all_ready(self):
        return all(
            value is PermissionState.READY
            for value in (self.microphone, self.accessibility, self.input_monitoring)
        )

    @property
    def recoverable(self):
        return tuple(
            name for name in ("microphone", "accessibility", "input_monitoring")
            if getattr(self, name) in {PermissionState.DENIED, PermissionState.REVOKED}
        )

    def as_dict(self):
        return {
            "microphone": self.microphone.value,
            "accessibility": self.accessibility.value,
            "input_monitoring": self.input_monitoring.value,
            "all_ready": self.all_ready,
            "recoverable": list(self.recoverable),
        }


class MacPermissionAuthority:
    """Remember prior readiness so a later denial is reported as revocation."""

    NAMES = ("microphone", "accessibility", "input_monitoring")

    def __init__(self, probes=None, requesters=None, settings_opener=None, clock=None):
        self._probes = dict(probes or self._native_probes())
        self._requesters = dict(requesters or self._native_requesters())
        self._settings_opener = settings_opener or self._open_settings
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._ever_ready = {name: False for name in self.NAMES}
        self._snapshot = PermissionSnapshot(
            *(PermissionState.UNKNOWN for _ in self.NAMES), observed_at=self._clock()
        )

    @staticmethod
    def _microphone_probe():
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        status = int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio))
        if status == 3:
            return True
        if status in (1, 2):
            return False
        return None

    @staticmethod
    def _accessibility_probe():
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())

    @staticmethod
    def _input_monitoring_probe():
        try:
            from Quartz import CGPreflightListenEventAccess
        except ImportError:
            return None
        return bool(CGPreflightListenEventAccess())

    @classmethod
    def _native_probes(cls):
        return {
            "microphone": cls._microphone_probe,
            "accessibility": cls._accessibility_probe,
            "input_monitoring": cls._input_monitoring_probe,
        }

    @staticmethod
    def _request_microphone():
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, lambda _granted: None
        )
        return True

    @staticmethod
    def _request_accessibility():
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        return True

    @staticmethod
    def _request_input_monitoring():
        from Quartz import CGRequestListenEventAccess
        return bool(CGRequestListenEventAccess())

    @classmethod
    def _native_requesters(cls):
        return {
            "microphone": cls._request_microphone,
            "accessibility": cls._request_accessibility,
            "input_monitoring": cls._request_input_monitoring,
        }

    @staticmethod
    def _open_settings(name):
        import subprocess
        anchors = {
            "microphone": "Privacy_Microphone",
            "accessibility": "Privacy_Accessibility",
            "input_monitoring": "Privacy_ListenEvent",
        }
        anchor = anchors.get(name)
        if anchor is None:
            return False
        subprocess.Popen([
            "/usr/bin/open",
            f"x-apple.systempreferences:com.apple.preference.security?{anchor}",
        ])
        return True

    def refresh(self):
        values = {}
        with self._lock:
            for name in self.NAMES:
                try:
                    observed = self._probes[name]()
                except Exception:
                    observed = None
                if observed is True:
                    self._ever_ready[name] = True
                    values[name] = PermissionState.READY
                elif observed is False:
                    values[name] = (
                        PermissionState.REVOKED if self._ever_ready[name]
                        else PermissionState.DENIED
                    )
                else:
                    values[name] = PermissionState.UNKNOWN
            self._snapshot = PermissionSnapshot(
                *(values[name] for name in self.NAMES), observed_at=self._clock()
            )
            return self._snapshot

    @property
    def snapshot(self):
        with self._lock:
            return self._snapshot

    def request(self, name):
        if name not in self.NAMES:
            return {"ok": False, "message": "Unknown macOS permission."}
        try:
            requested = bool(self._requesters[name]())
        except Exception as exc:
            return {"ok": False, "message": str(exc)[:200]}
        return {
            "ok": requested,
            "permission": name,
            "state": self.refresh().as_dict()[name],
        }

    def open_settings(self, name):
        if name not in self.NAMES:
            return False
        try:
            return bool(self._settings_opener(name))
        except Exception:
            return False
