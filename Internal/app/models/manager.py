#!/usr/bin/env python3
"""Model Manager — singleton that discovers, loads, unloads, and tracks GGUF models.

Provides ModelManager: the central authority for all on-device GGUF models.
Responsibilities:
  - Discover .gguf files in the models directory
  - Parse GGUF filenames for metadata (name, quantisation, version)
  - Validate GGUF magic bytes to filter non-GGUF files
  - Track model status: available, loaded, downloading
  - Lazy-load: no model is loaded at construction time
  - Single-resident policy: loading a new model unloads the old one
  - Integrate with ModelProcessManager for subprocess lifecycle

Pure stdlib + branding. No heavy dependencies, no llama.cpp imports.
"""

import os
import re
import sys
import threading
import time

# Import branding for the default models directory. Guarded so the module
# still imports on unusual platforms.
try:
    from branding import MODELS_DIR
except ImportError:
    MODELS_DIR = os.path.join(os.path.expanduser("~"), "Mumble", "models")


# ============================================================================
# GGUF filename parsing
# ============================================================================

# GGUF magic bytes: "GGUF" at offset 0, followed by version (u32).
GGUF_MAGIC = b"GGUF"

# Known quantisation suffixes (case-insensitive). Used to extract the quant
# label from the end of a GGUF filename, before the .gguf extension.
_KNOWN_QUANTS = {
    "q2_k", "q3_k_s", "q3_k_m", "q3_k_l",
    "q4_0", "q4_1", "q4_k_s", "q4_k_m",
    "q5_0", "q5_1", "q5_k_s", "q5_k_m",
    "q6_k", "q8_0", "q8_k",
    "f16", "f32", "bf16",
    "iq1_s", "iq1_m", "iq2_s", "iq2_m", "iq2_xs",
    "iq3_s", "iq3_m", "iq3_xs",
    "iq4_nl", "iq4_xs",
}

# Regex to extract version-like numbers from model names.
_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def _parse_gguf_filename(filename):
    """Parse a GGUF filename into metadata."""
    base = os.path.splitext(filename)[0]
    quant = "unknown"
    name_base = base
    sorted_quants = sorted(_KNOWN_QUANTS, key=len, reverse=True)
    quant_pattern = "|".join(re.escape(q) for q in sorted_quants)
    m = re.search(rf"[_-]({quant_pattern})$", base, re.IGNORECASE)
    if m:
        quant = m.group(1).upper()
        name_base = base[:m.start()]
    name = _humanise_name(name_base)
    version = _extract_version_from_name(name)
    return {
        "name": name,
        "quantisation": quant.upper(),
        "version": version,
        "base": base,
    }


def _humanise_name(raw_name):
    """Convert a raw model name base into a human-readable display name."""
    if not raw_name:
        return "Unknown"
    segments = raw_name.split("-")
    humanised = []
    for seg in segments:
        if not seg:
            continue
        upper = seg.upper()
        if upper in ("GRMR", "QWEN", "PHI", "GEMMA", "DEEPSEEK"):
            humanised.append(upper)
        elif re.match(r"^\d+(?:\.\d+)?[bm]$", seg, re.IGNORECASE):
            humanised.append(seg.upper())
        elif re.match(r"^\d+\.\d+$", seg):
            humanised.append(seg)
        elif re.match(r"^\d+[kK]$", seg):
            humanised.append(seg.upper())
        elif seg.lower() == "instruct":
            humanised.append("Instruct")
        elif seg.lower() == "chat":
            humanised.append("Chat")
        elif seg.lower() == "mini":
            humanised.append("Mini")
        else:
            humanised.append(seg.capitalize())
    return "-".join(humanised)


def _extract_version_from_name(name):
    """Extract a version string from a model name."""
    matches = _VERSION_RE.findall(name)
    if matches:
        return matches[0]
    return "0.0"


def _validate_gguf_header(filepath):
    """Check if a file has valid GGUF magic bytes at offset 0."""
    try:
        with open(filepath, "rb") as f:
            magic = f.read(4)
        return magic == GGUF_MAGIC
    except (OSError, EOFError):
        return False


# ============================================================================
# ModelManager
# ============================================================================

class ModelManager:
    """Singleton manager for on-device GGUF models.

    Lifecycle:
      mgr = ModelManager()             # no models loaded, no discovery
      mgr.discover()                   # scan models directory for .gguf files
      models = mgr.available_models()  # list of dicts with metadata
      path = mgr.load_model("Qwen2.5-1.5B-Instruct")  # load into memory
      mgr.unload_model("Qwen2.5-1.5B-Instruct")       # release

    The Manager tracks STATUS for each model:
      - "available":    discovered on disk, not loaded
      - "loaded":       currently the active resident model
      - "downloading":  being fetched from HuggingFace Hub
      - "unknown":      not found

    Single-resident policy: only one model can be "loaded" at a time.
    Calling load_model() unloads the previous model automatically.
    """

    def __init__(self, models_dir=None):
        """Create a ModelManager.

        Args:
            models_dir: Path to the models directory. Defaults to
                        branding.MODELS_DIR (%APPDATA%/Mumble/models/).
        """
        self.models_dir = models_dir or MODELS_DIR
        self._models = {}         # name → model info dict
        self._loaded = None       # name of the currently loaded model
        self._lock = threading.RLock()
        self._discovered = False
        self._process_manager = None  # ModelProcessManager for subprocess lifecycle
        self._system_prompt = ""      # cached system prompt for model loading

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self):
        """Scan the models directory for .gguf files.

        Updates the internal model registry. Idempotent — repeated calls
        re-scan and update the registry with any new or removed files.
        """
        with self._lock:
            self._models.clear()
            self._discovered = True
            try:
                if not os.path.isdir(self.models_dir):
                    return
                for entry in os.listdir(self.models_dir):
                    if not entry.lower().endswith(".gguf"):
                        continue
                    full = os.path.join(self.models_dir, entry)
                    if not os.path.isfile(full):
                        continue
                    if not _validate_gguf_header(full):
                        continue
                    info = _parse_gguf_filename(entry)
                    info["path"] = full
                    info["size_bytes"] = os.path.getsize(full)
                    info["status"] = "available"
                    if self._loaded and info["name"] == self._loaded:
                        info["status"] = "loaded"
                    self._models[info["name"]] = info
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def available_models(self):
        """Return a list of all discovered models with their metadata."""
        if not self._discovered:
            self.discover()
        with self._lock:
            return list(self._models.values())

    def get_model(self, name):
        """Look up a model by name. Returns the model info dict or None."""
        if not self._discovered:
            self.discover()
        with self._lock:
            return self._models.get(name)

    @property
    def loaded_model(self):
        """The name of the currently loaded model, or None."""
        with self._lock:
            return self._loaded

    def status(self, name):
        """Return the status string for a model."""
        if not self._discovered:
            self.discover()
        with self._lock:
            m = self._models.get(name)
            if m:
                return m.get("status", "available")
        return "unknown"

    def set_status(self, name, status):
        """Set the status of a model."""
        valid = {"available", "loaded", "downloading"}
        if status not in valid:
            raise ValueError(f"Invalid status: {status!r}. Must be one of {valid}")
        with self._lock:
            if name in self._models:
                self._models[name]["status"] = status

    def set_process_manager(self, process_manager, system_prompt=""):
        """Attach a ModelProcessManager for subprocess lifecycle management.

        When set, load_model() will start the process manager's subprocess,
        and unload_model() will stop it.
        """
        with self._lock:
            self._process_manager = process_manager
            self._system_prompt = system_prompt

    def has_process_manager(self):
        """Whether a process manager is attached and ready."""
        with self._lock:
            return self._process_manager is not None

    def get_process_manager(self):
        """Return the attached process manager, or None."""
        with self._lock:
            return self._process_manager

    # ------------------------------------------------------------------
    # Load / Unload
    # ------------------------------------------------------------------

    def _finish_process_load(self, name, success):
        """Synchronise catalogue state after a process switch completes."""
        with self._lock:
            if self._loaded and self._loaded in self._models:
                self._models[self._loaded]["status"] = "available"
            if success and name in self._models:
                self._models[name]["status"] = "loaded"
                self._loaded = name
            else:
                self._loaded = None

    def load_model(self, name, system_prompt=None):
        """Load a model by name, returning its absolute file path.

        This implements the single-resident policy: if another model is
        currently loaded, it is unloaded first. The new model's status is
        set to "loaded".

        If a process manager is attached, the old model's subprocess is
        terminated and the new model's subprocess is started. If generation
        is in progress, the switch is queued and applied after completion.

        Returns the model's file path, or None if the model is not found.
        """
        if not self._discovered:
            self.discover()
        with self._lock:
            if name not in self._models:
                return None

            pm = self._process_manager
            new_path = self._models[name]["path"]
            sp = system_prompt if system_prompt is not None else self._system_prompt

            if pm is not None:
                # ModelProcessManager queues this safely when a generation is
                # active and calls us after the actual switch.  Do not claim a
                # model is loaded before llama-cli has successfully started.
                pm.switch_model(
                    new_path,
                    system_prompt=sp,
                    on_complete=lambda success: self._finish_process_load(
                        name, success
                    ),
                )
                return new_path

            # Unload the current model if one is loaded.
            old_loaded = self._loaded
            if old_loaded and old_loaded in self._models:
                self._models[old_loaded]["status"] = "available"

            # Load the new model.
            self._models[name]["status"] = "loaded"
            self._loaded = name
            return new_path

    def unload_model(self, name):
        """Unload a model by name, setting its status back to 'available'.

        If the named model is the currently loaded model, the loaded_model
        property becomes None and any attached process manager's subprocess
        is terminated. If it's not loaded, this is a no-op.
        """
        with self._lock:
            if name in self._models:
                self._models[name]["status"] = "available"
            if self._loaded == name:
                self._loaded = None
                if self._process_manager is not None:
                    self._process_manager.stop()

    def force_unload(self):
        """Unload whatever model is currently loaded (if any).

        Terminates any running subprocess via the attached process manager.
        """
        with self._lock:
            if self._loaded and self._loaded in self._models:
                self._models[self._loaded]["status"] = "available"
            self._loaded = None
            if self._process_manager is not None:
                self._process_manager.stop()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def __repr__(self):
        n = len(self._models)
        ld = self._loaded or "none"
        return f"ModelManager({n} models, loaded={ld})"
