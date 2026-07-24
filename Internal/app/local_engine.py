#!/usr/bin/env python3
"""Local Post-Processing Engine — the hybrid local/cloud router + local-LLM seam.

This module is the POLICY layer for "run it on the edge unless you truly can't".
It answers one question per request — *where does this lane run?* — and it carries
the pieces a local small-LLM lane needs (a pluggable backend, GBNF output grammars,
and context-injected style sheets), without taking a heavy dependency itself.

Three engines, chosen per lane + input:
  • local      — deterministic rules (pipeline stages 1-4). Stage 4 (model_free)
                 is always available; Stages 1-3 degrade gracefully.
  • local_llm  — a small GGUF model on-device (llama.cpp), used to lift the
                 smart-mode lanes (prompt/email/reply) from templated output
                 to fluent output WITHOUT the cloud. Optional; off until a backend
                 + model are installed (owner-gated — see the blueprint).
  • cloud      — Cerebras (default) / OpenAI / Anthropic / OpenRouter via ai.py.

Pipeline integration: run_local_pipeline(text, lane) chains the four-stage
merged-model pipeline (punctuation → grammar → formatting → cleanup) for local
processing. This is the main post-processing path when no cloud key is present.

Graceful degradation is the whole point:
  smart-mode lane → local_llm if ready → else cloud if a key is set → else the
  deterministic offline builder (formatting.build_*). It NEVER hard-blocks an
  everyday lane.

The ONE place a hard block is correct is a genuinely heavy preset — deep semantic
extraction, multi-document reasoning — that is beyond a small edge model. Asked for
with no cloud key, the router returns CLOUD_REQUIRED_MESSAGE and points the user at
Cerebras (the free, fastest fallback). See route().

Pure stdlib. The local-LLM backend is import-guarded, so this module imports and
unit-tests with NO llama-cpp installed (NullBackend is the default). Deterministic
-> unit-tested in test_local_engine.py.
"""

import os


# ============================================================================
# 1. The cloud-dominance routing matrix  (Objective §1 + the dominance directive)
# ============================================================================
# THE HIERARCHY (owner directive, 2026-06-27). Cloud is the PRIMARY authority for
# final text transformation. Local post-processing is GATED to the local-only case:
#
#   cloud key present AND local-only OFF   -> CLOUD is primary. Local does raw STT +
#                                             inline `//` uncertainty marking ONLY;
#                                             the raw transcript (with markers) goes
#                                             straight to cloud. No intermediate
#                                             local reasoning/restructuring intervenes.
#   no cloud key  OR  local-only toggle ON -> LOCAL engine runs (best-effort).
#
# Local-only NEVER hard-blocks: it attempts every task, degrades gracefully, and
# APPENDS (does not interrupt) a notice when a task exceeds edge capability.

# Engine identifiers returned by route().
LOCAL = "local"           # deterministic rules (formatting.py / foreign_boost.py)
LOCAL_LLM = "local_llm"   # small GGUF model on-device (opt-in, Strategy B)
CLOUD = "cloud"           # the primary authority when a key is present

# Lane families. SMART lanes get the local LLM (then the builder) in local-only;
# HEAVY lanes exceed the edge and trigger the appended upgrade notice.
LOCAL_NATIVE = {"text", "foreign"}           # rule engines do these fully on-device
SMART_LANES = {"prompt", "email", "reply"}
HEAVY_LANES = {"deck_extract", "deck_multidoc", "deck_reason"}
CLOUD_ONLY = set(HEAVY_LANES)                # kept name for classify_task back-compat

# Notices are APPENDED at the output stage — they never block execution (directive §3).
# UPGRADE_NOTICE is the active one (its wording is the directive's). CLOUD_REQUIRED_MESSAGE
# preserves the original exact string for any UI that still wants the stronger phrasing.
UPGRADE_NOTICE = (
    "For enhanced results on this task, enable AI Pro Mode or connect a cloud API key."
)
CLOUD_REQUIRED_MESSAGE = (
    "This specific preset requires cloud acceleration. "
    "Please link a cloud API key."
)

# Cerebras is the primary free cloud route (fastest, free tier); the notice points here.
SUGGESTED_PROVIDER = "cerebras"
SUGGESTED_KEY_SETTING = "cerebras_api_key"

# A single big reasoning input blows a small edge model's context window; past this
# many words a prompt/reply is flagged heavy (best-effort + notice in local-only).
HEAVY_WORDS = 1500


class RouteDecision:
    """The router's verdict for one request. `notice` is appended at output (never
    blocks); `degraded` flags a local best-effort run that exceeded edge capability."""

    __slots__ = ("engine", "reason", "notice", "degraded", "suggested_provider",
                 "suggested_key_setting")

    def __init__(self, engine, reason, notice=None, degraded=False):
        self.engine = engine                 # LOCAL | LOCAL_LLM | CLOUD
        self.reason = reason                 # short machine-readable reason
        self.notice = notice                 # None, or text to APPEND at output
        self.degraded = degraded             # local best-effort beyond optimal range
        self.suggested_provider = SUGGESTED_PROVIDER
        self.suggested_key_setting = SUGGESTED_KEY_SETTING

    @property
    def cloud_augmented(self):
        return self.engine == CLOUD

    @property
    def model_free(self):
        """LOCAL engine == rules only == NO model == strict formatting-only
        (model_free.process): no semantics, no substitution, no ambiguity
        resolution. LOCAL_LLM and CLOUD are the model paths that may interpret."""
        return self.engine == LOCAL

    @property
    def rules_only(self):
        """Backward-compatible name for the deterministic model-free route."""
        return self.model_free

    def __repr__(self):
        d = " degraded" if self.degraded else ""
        return f"RouteDecision({self.engine}, {self.reason!r}{d})"


def local_only_active(cloud_key_present, local_only_mode=False):
    """The gate (directive §3): local-only is active iff there is NO cloud key, OR
    the user explicitly flipped the offline toggle. Every other case is cloud-augmented."""
    return bool(local_only_mode or not cloud_key_present)


def classify_task(lane, text="", n_sources=0, preset_requires=None):
    """Edge vs heavy. Heavy = beyond a small on-device model:
      • a preset explicitly tagged requires='cloud', or a heavy/cloud-only lane,
      • multi-document reasoning (>= 2 source documents),
      • a single reasoning input larger than the edge context budget.
    Everything else is 'edge'."""
    if preset_requires == "cloud" or lane in HEAVY_LANES:
        return "heavy"
    if n_sources >= 2:
        return "heavy"
    if lane in ("prompt", "reply") and len((text or "").split()) > HEAVY_WORDS:
        return "heavy"
    return "edge"


def route(lane, *, cloud_key_present=False, local_only_mode=False,
          local_llm_ready=False, complexity="edge"):
    """The single source of truth for the cloud-dominance matrix.

      cloud-augmented (key present, local-only off):
        every lane -> CLOUD   (local only does raw STT + `//` marking beforehand)
      local-only (no key, or explicit toggle):
        text            -> LOCAL  (model-free formatting; model_free.process)
        foreign         -> LOCAL_LLM if a model is ready, else LOCAL (NO boost —
                           resolving foreign terms needs context a model provides)
        smart lane      -> LOCAL_LLM if ready, else LOCAL (formatting-ONLY degrade —
                           NO template/intent building when there is no model)
        heavy / oversize-> best-effort LOCAL(+LLM) + APPENDED upgrade notice
    When the result is LOCAL it is `model_free` (strict formatting only — see the
    model-free trial, model_free.py). Never returns a hard block."""
    # ---- CLOUD-AUGMENTED: a key is present and local-only is off. Cloud rules. ----
    if not local_only_active(cloud_key_present, local_only_mode):
        return RouteDecision(CLOUD, "cloud-dominant")

    # ---- LOCAL-ONLY: no key, or the explicit offline toggle. Best-effort, no block. ----
    if lane == "text":
        return RouteDecision(LOCAL, "model-free-format")
    if lane == "foreign":
        # A model can use full-sentence context to resolve a foreign term; the
        # model-free path MUST NOT (it would be guessing). So a local LLM takes it
        # if present; otherwise it is plain formatting and the term passes through.
        if local_llm_ready:
            return RouteDecision(LOCAL_LLM, "edge-llm")
        return RouteDecision(LOCAL, "model-free-format")

    degraded = lane in HEAVY_LANES or complexity == "heavy"
    if local_llm_ready:
        engine, reason = LOCAL_LLM, "edge-llm"
    else:
        # No model: smart modes degrade to formatting-ONLY (model_free.process),
        # never the intent/template builders. Formatting and structure, not meaning.
        engine, reason = LOCAL, "model-free-degrade"
        # Smart lanes without LLM are inherently degraded — the output quality
        # is lower than what a user would get with a model or cloud.
        if lane in SMART_LANES:
            degraded = True
    if degraded:
        return RouteDecision(engine,
                             "local-best-effort-degraded" if local_llm_ready
                             else "model-free-degrade",
                             notice=UPGRADE_NOTICE, degraded=True)
    if lane in SMART_LANES:
        return RouteDecision(engine, reason)
    return RouteDecision(LOCAL, "model-free-format")


def cloud_fallback_hint(strong=False):
    """The structured upgrade hint a local-only UI appends (never blocks): the
    notice text plus exactly which provider/key unlocks cloud (Cerebras, free).
    `strong=True` returns the original CLOUD_REQUIRED_MESSAGE phrasing."""
    return {
        "message": CLOUD_REQUIRED_MESSAGE if strong else UPGRADE_NOTICE,
        "provider": SUGGESTED_PROVIDER,
        "key_setting": SUGGESTED_KEY_SETTING,
        "free": True,
    }


# ============================================================================
# 1b. Pipeline integration — run the merged-model pipeline for local processing
# ============================================================================

def run_local_pipeline(text, lane="text", **kwargs):
    """Run the four-stage local post-processing pipeline on `text`.

    This is the MAIN entry point for local-only (no cloud key) processing.
    It delegates to the pipeline.PipelineOrchestrator, which chains:
        Stage 1 (punctuation) → Stage 2 (grammar) → Stage 3 (formatting)
        → Stage 4 (cleanup)

    Stages that are unavailable (missing models, failed imports) are silently
    skipped.  At minimum, Stage 4 (model_free surface cleanup) always runs,
    so the output is always at least formatted.

    When degradation occurs (stages skipped or failed), an UPGRADE_NOTICE is
    appended to the output so the user knows they can improve results by
    adding a cloud key or downloading local models (VAL-CROSS-016).

    Parameters:
        text: The raw transcript to process.
        lane: The mode lane ("text", "prompt", "email", "reply", "foreign",
              "list").
        **kwargs: Forwarded to the pipeline orchestrator.

    Returns:
        The processed text after all available stages have been applied.
        Returns "" for empty/whitespace-only input.

        If `return_meta=True` is passed in kwargs, returns a
        (result, degradation_meta) tuple instead of just the result string.
    """
    return_meta = kwargs.pop("return_meta", False)
    try:
        from pipeline import get_orchestrator
        orch = get_orchestrator()
        result = orch.process(text, lane=lane, **kwargs)
        meta = orch.last_degradation
        if return_meta:
            return result, meta
        return result
    except ImportError:
        # Pipeline package not installed — fall back to model_free directly.
        try:
            import model_free
            result = model_free.process(text, mode=lane)
            if return_meta:
                return result, None
            return result
        except ImportError:
            return (text, None) if return_meta else text  # absolute last resort


def get_pipeline_orchestrator():
    """Return the pipeline orchestrator singleton, or None if unavailable."""
    try:
        from pipeline import get_orchestrator
        return get_orchestrator()
    except ImportError:
        return None


def pipeline_status():
    """Return a status snapshot of the local pipeline for diagnostics.

    Returns a dict with:
      • available: bool — whether the pipeline package is importable
      • stages: list of int — which stage numbers are currently available
      • labels: list of str — human-readable stage labels
    """
    orch = get_pipeline_orchestrator()
    if orch is None:
        return {"available": False, "stages": [], "labels": []}
    return {
        "available": True,
        "stages": orch.available_stages(),
        "labels": orch.stage_labels(),
    }


# ============================================================================
# 2. Local-LLM backend seam  (Objective §4 — runtime packages)
# ============================================================================
# A thin, swappable interface so the rest of Mumble never imports llama_cpp
# directly. The default is NullBackend (no local LLM); the documented production
# backend is LlamaCppBackend, which is import-guarded — it reports available()
# == False until both `llama-cpp-python` and a GGUF model file are present, so
# this whole module imports and tests with no heavy dependency installed.

class LocalLLMBackend:
    """Abstract on-device LLM. Implementations must be CPU-viable and lazy —
    never load the model at construction (boot must stay fast)."""

    name = "base"

    def available(self):
        """True only if this backend can actually generate right now (runtime +
        model present). The router calls this to decide local_llm_ready."""
        return False

    def generate(self, system, user, grammar=None, max_tokens=512,
                 temperature=0.3):
        """Return the model's completion. `grammar` is an optional GBNF string
        that constrains the sampler to a required output shape (see GBNF)."""
        raise NotImplementedError


class NullBackend(LocalLLMBackend):
    """The default: no local LLM installed. available() is False, so smart-mode
    lanes route to cloud / the offline builder, never to a missing model."""

    name = "null"

    def available(self):
        return False

    def generate(self, system, user, grammar=None, max_tokens=512,
                 temperature=0.3):
        raise RuntimeError("no local LLM backend installed (NullBackend)")


class LlamaCppBackend(LocalLLMBackend):
    """llama.cpp / llama-cpp-python adapter — the documented production backend.

    Not wired by default (owner-gated: it adds a heavy dependency and a GGUF model
    download — see the blueprint). It is written out concretely so the integration
    is real, but available() returns False until `llama-cpp-python` is importable
    AND `model_path` exists, so importing this module never requires the package.
    """

    name = "llama_cpp"

    def __init__(self, model_path=None, n_ctx=4096, n_threads=None,
                 n_gpu_layers=0):
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.n_threads = n_threads or max(2, (os.cpu_count() or 4) - 2)
        self.n_gpu_layers = n_gpu_layers
        self._llm = None              # lazily constructed Llama instance
        self._grammar_cache = {}

    def _importable(self):
        try:
            import importlib.util
            return importlib.util.find_spec("llama_cpp") is not None
        except Exception:
            return False

    def available(self):
        return bool(self.model_path and os.path.exists(self.model_path)
                    and self._importable())

    def _ensure(self):
        """Lazy-load the GGUF model on first generate (never at boot)."""
        if self._llm is not None:
            return self._llm
        from llama_cpp import Llama  # import only when actually generating
        self._llm = Llama(
            model_path=self.model_path,
            n_ctx=self.n_ctx,
            n_threads=self.n_threads,
            n_gpu_layers=self.n_gpu_layers,
            verbose=False,
        )
        return self._llm

    def _grammar(self, gbnf):
        if not gbnf:
            return None
        if gbnf in self._grammar_cache:
            return self._grammar_cache[gbnf]
        from llama_cpp import LlamaGrammar
        g = LlamaGrammar.from_string(gbnf)
        self._grammar_cache[gbnf] = g
        return g

    def generate(self, system, user, grammar=None, max_tokens=512,
                 temperature=0.3):
        llm = self._ensure()
        out = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            grammar=self._grammar(grammar),
        )
        return (out["choices"][0]["message"]["content"] or "").strip()


class LlamaCliBackend(LocalLLMBackend):
    """Drives the BUNDLED llama.cpp `llama-cli` binary via subprocess — no Python
    package required. This is what makes the bundled `llama-cpp-bin/` binaries
    usable: point it at a GGUF model and the bundled `llama-cli.exe` and it runs
    fully on-device with zero pip dependency.

    When a ModelProcessManager is provided, the backend uses a persistent
    subprocess (model stays warm between requests). Without one, it falls back
    to one-shot subprocess calls. The persistent mode supports model switching,
    crash recovery, and memory pressure unloading.

    available() is True only when BOTH the binary and a model file exist (and
    if using persistent mode, the process manager is running), so the router
    never routes to a missing engine.
    """

    name = "llama_cli"

    def __init__(self, model_path=None, bin_path=None, n_ctx=4096,
                 n_threads=None, timeout=90, process_manager=None,
                 model_manager=None):
        self.model_path = model_path
        self.bin_path = bin_path
        self.n_ctx = n_ctx
        self.n_threads = n_threads or max(2, (os.cpu_count() or 4) - 2)
        self.timeout = timeout
        self._process_manager = process_manager  # ModelProcessManager or None
        self._model_manager = model_manager      # ModelManager or None

    def available(self):
        """True if the backend can generate right now."""
        if self._process_manager is not None:
            return self._process_manager.is_running()
        return bool(self.model_path and os.path.exists(self.model_path)
                    and self.bin_path and os.path.exists(self.bin_path))

    @property
    def loaded_model_name(self):
        """The name of the currently loaded model (from ModelManager), or None."""
        if self._model_manager is not None:
            return self._model_manager.loaded_model
        return None

    @property
    def persistent(self):
        """Whether the backend is using a persistent (warm) subprocess."""
        return self._process_manager is not None

    # ------------------------------------------------------------------
    # Model switching
    # ------------------------------------------------------------------

    def switch_model(self, model_name, system_prompt=""):
        """Switch to a different model. If generation is in progress, the switch
        is queued and applied after the current generation completes."""
        if self._model_manager is None:
            return None
        path = self._model_manager.load_model(model_name, system_prompt)
        if path is None:
            return None
        if self._process_manager is not None:
            if self._process_manager._pending_model is not None:
                return False
            if (self._model_manager.loaded_model == model_name
                    and self._process_manager.is_running()):
                self.model_path = path
                return True
            return False
        self.model_path = path
        return True

    def unload(self):
        """Unload the current model and terminate the subprocess."""
        if self._model_manager is not None:
            self._model_manager.force_unload()
        elif self._process_manager is not None:
            self._process_manager.stop()
        self.model_path = None

    def reload_if_needed(self):
        """Reload the model if it was unloaded due to memory pressure."""
        if self._process_manager is not None and not self._process_manager.is_running():
            if self._model_manager is not None and self._model_manager.loaded_model:
                return self._restart_process()
            elif self.model_path:
                return self._process_manager.start(self.model_path)
        return False

    def _restart_process(self):
        """Attempt to restart the process for the currently loaded model."""
        if self._model_manager is None or self._process_manager is None:
            return False
        model_name = self._model_manager.loaded_model
        if not model_name:
            return False
        model = self._model_manager.get_model(model_name)
        if not model:
            return False
        system_prompt = getattr(self._model_manager, '_system_prompt', '')
        return self._process_manager.start(model["path"],
                                           system_prompt=system_prompt)

    # ------------------------------------------------------------------
    # Memory pressure
    # ------------------------------------------------------------------

    def check_memory_pressure(self):
        """Check if the system is under memory pressure."""
        if self._process_manager is not None:
            return self._process_manager.check_memory_pressure()
        return False

    def handle_memory_pressure(self):
        """Unload the model if memory pressure is detected."""
        if self._process_manager is not None:
            if self._process_manager.unload_if_pressure():
                if self._model_manager is not None:
                    self._model_manager.force_unload()
                return True
        return False

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    @staticmethod
    def _format_prompt(system, user):
        """A simple, model-agnostic chat framing the binary can complete."""
        return (f"<|system|>\n{(system or '').strip()}\n"
                f"<|user|>\n{(user or '').strip()}\n<|assistant|>\n")

    def _build_cmd(self, system, user, grammar=None, grammar_file=None,
                   max_tokens=512, temperature=0.3):
        """Construct the llama-cli argv. Factored out so it is unit-testable."""
        cmd = [
            self.bin_path,
            "-m", self.model_path,
            "-p", self._format_prompt(system, user),
            "-n", str(int(max_tokens)),
            "--temp", str(float(temperature)),
            "-c", str(int(self.n_ctx)),
            "-t", str(int(self.n_threads)),
            "-no-cnv",          # one-shot completion, not interactive chat
            "--simple-io",
        ]
        if grammar_file:
            cmd += ["--grammar-file", grammar_file]
        elif grammar:
            cmd += ["--grammar", grammar]
        return cmd

    def generate(self, system, user, grammar=None, max_tokens=512,
                 temperature=0.3):
        """Generate a completion from the model.

        In persistent mode, sends the prompt to the running subprocess.
        In one-shot mode, spawns a new subprocess for each call.
        """
        import subprocess as sp_mod
        import tempfile

        # Persistent mode: delegate to the process manager.
        if self._process_manager is not None:
            self.handle_memory_pressure()
            if not self._process_manager.is_running():
                if not self.reload_if_needed():
                    raise RuntimeError(
                        "Model is not loaded. Call load_model() first."
                    )
            prompt_text = self._format_prompt(system, user)
            try:
                return self._process_manager.generate(
                    prompt_text, max_tokens=max_tokens,
                    temperature=temperature
                )
            except RuntimeError:
                if self.reload_if_needed():
                    return self._process_manager.generate(
                        prompt_text, max_tokens=max_tokens,
                        temperature=temperature
                    )
                raise

        # One-shot mode: spawn a subprocess for each call.
        gfile = None
        try:
            if grammar:
                fd, gfile = tempfile.mkstemp(suffix=".gbnf")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(grammar)
            cmd = self._build_cmd(system, user, grammar_file=gfile,
                                  max_tokens=max_tokens, temperature=temperature)
            proc = sp_mod.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
                cwd=os.path.dirname(self.bin_path) or None,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()
                if len(detail) > 500:
                    detail = detail[:500] + "..."
                raise RuntimeError(
                    f"llama-cli exited with code {proc.returncode}"
                    + (f": {detail}" if detail else "")
                )
            out = (proc.stdout or "").strip()
            return _strip_cli_echo(out, self._format_prompt(system, user))
        finally:
            if gfile and os.path.exists(gfile):
                try:
                    os.remove(gfile)
                except OSError:
                    pass


def _strip_cli_echo(out, prompt):
    """llama-cli echoes the prompt before the completion; strip it (and any
    leftover role markers) so only the model's answer is returned."""
    if not out:
        return ""
    p = (prompt or "").strip()
    if p and out.startswith(p):
        out = out[len(p):]
    # Some builds echo only the tail marker — cut at the last assistant marker.
    marker = "<|assistant|>"
    if marker in out:
        out = out.rsplit(marker, 1)[-1]
    for junk in ("<|system|>", "<|user|>", "<|assistant|>", "[end of text]",
                 "</s>"):
        out = out.replace(junk, "")
    return out.strip()


def discover_model(search_dirs, explicit_path=""):
    """Find an on-device GGUF model. An `explicit_path` (a user setting) wins if it
    exists; otherwise the first *.gguf found across `search_dirs` (in order) is
    used. Returns an absolute path or None. Pure filesystem.

    Prefer using ModelManager.available_models() for discovery with metadata;
    this function remains for backward compatibility and quick path lookups.
    """
    if explicit_path and os.path.exists(explicit_path):
        return os.path.abspath(explicit_path)
    for d in search_dirs or []:
        try:
            if not d or not os.path.isdir(d):
                continue
            ggufs = sorted(f for f in os.listdir(d) if f.lower().endswith(".gguf"))
            if ggufs:
                return os.path.abspath(os.path.join(d, ggufs[0]))
        except OSError:
            continue
    return None


def build_backend(model_path=None, cli_bin=None):
    """Pick the best AVAILABLE on-device backend for a discovered model, without
    loading anything:
      - a GGUF + the `llama-cpp-python` package  -> LlamaCppBackend (in-process)
      - a GGUF + the bundled llama-cli binary     -> LlamaCliBackend (subprocess)
      - otherwise                                 -> NullBackend (no local LLM)
    Returns (backend, reason) where reason is a short status string for the UI."""
    if not model_path or not os.path.exists(model_path):
        return NullBackend(), "no GGUF model found"
    lib = LlamaCppBackend(model_path=model_path)
    if lib.available():
        return lib, "llama-cpp-python + model"
    if cli_bin and os.path.exists(cli_bin):
        return LlamaCliBackend(model_path=model_path, bin_path=cli_bin), \
            "bundled llama-cli + model"
    return NullBackend(), "model present but no runtime (install llama-cpp-python)"


def init_model_backend(models_dir=None, cli_bin=None, model_name=None,
                       system_prompt="", auto_load=False):
    """Initialise the full model backend stack with ModelManager integration.

    This is the MAIN entry point for wiring ModelManager into the LlamaCliBackend.
    It creates and wires together:
      1. ModelManager - discovers GGUF files, tracks status
      2. ModelProcessManager - manages the persistent llama-cli subprocess
      3. LlamaCliBackend - the inference backend that uses both

    By default, no model is loaded (auto_load=False). The model is lazily loaded
    on first use, respecting the single-resident policy. Set auto_load=True or
    pass model_name to eagerly load a specific model at initialisation time.

    Returns:
        (backend, model_manager, reason) tuple.
    """
    try:
        from models.manager import ModelManager
        from models.backend import ModelProcessManager
    except ImportError:
        backend, reason = build_backend(
            model_path=discover_model([models_dir] if models_dir else []),
            cli_bin=cli_bin,
        )
        return backend, None, reason

    if cli_bin is None:
        try:
            from branding import llama_cli_path as _lcp
            cli_bin = _lcp()
        except ImportError:
            cli_bin = ""

    mgr = ModelManager(models_dir=models_dir)
    mgr.discover()
    models = mgr.available_models()

    if not models:
        return NullBackend(), mgr, "no GGUF models found"

    if model_name:
        path = mgr.load_model(model_name, system_prompt)
        if path is None:
            return NullBackend(), mgr, f"model {model_name!r} not found"
    elif auto_load:
        first = models[0]
        path = mgr.get_model(first["name"])
        if path:
            path = path["path"]
            model_name = first["name"]
            mgr.load_model(model_name, system_prompt)
        else:
            return NullBackend(), mgr, "model discovery inconsistency"
    else:
        backend = LlamaCliBackend(
            model_path=None, bin_path=cli_bin,
            model_manager=mgr,
        )
        mgr.set_process_manager(None)
        return backend, mgr, "model manager ready (no model loaded)"

    pm = ModelProcessManager(bin_path=cli_bin)
    if path and os.path.exists(path):
        pm.start(path, system_prompt=system_prompt)
    mgr.set_process_manager(pm, system_prompt)

    backend = LlamaCliBackend(
        model_path=path,
        bin_path=cli_bin,
        process_manager=pm,
        model_manager=mgr,
    )

    if backend.available():
        reason = f"model {model_name} loaded via llama-cli"
    else:
        reason = f"model {model_name} failed to start"

    return backend, mgr, reason


# The active backend. Mumble swaps this in once (boot) via set_backend(); default
# is Null so nothing depends on a local model being present.
_BACKEND = NullBackend()
_BACKEND_REASON = "not initialised"
_MODEL_MANAGER = None  # Set when init_model_backend() is called.


def set_backend(backend, reason="", model_manager=None):
    """Install the process-wide local-LLM backend (call once at boot)."""
    global _BACKEND, _BACKEND_REASON, _MODEL_MANAGER
    _BACKEND = backend or NullBackend()
    _BACKEND_REASON = reason or (
        "ready" if local_llm_ready() else "no local LLM")
    _MODEL_MANAGER = model_manager
    return _BACKEND


def get_backend():
    return _BACKEND


def get_model_manager():
    """Return the process-wide ModelManager, or None if not initialised."""
    return _MODEL_MANAGER


def local_llm_ready():
    """Convenience for the router: is a usable on-device LLM resident right now?"""
    try:
        return bool(_BACKEND and _BACKEND.available())
    except Exception:
        return False


def backend_status():
    """A snapshot of the local-LLM state for the UI / diagnostics:
      {"name", "ready", "model", "reason", "model_manager", "loaded_model"}.
    """
    b = _BACKEND
    status = {
        "name": getattr(b, "name", "null"),
        "ready": local_llm_ready(),
        "model": getattr(b, "model_path", None),
        "reason": _BACKEND_REASON,
        "model_manager": _MODEL_MANAGER is not None,
        "loaded_model": None,
        "persistent": getattr(b, "persistent", False),
    }
    if _MODEL_MANAGER is not None:
        status["loaded_model"] = _MODEL_MANAGER.loaded_model
        status["available_models"] = len(_MODEL_MANAGER.available_models())
    return status


# ============================================================================
# 3. GBNF output grammars  (Objective §4 — structural formatting constraints)
# ============================================================================
# Real llama.cpp GBNF grammars. Bound onto the sampler, they make a malformed
# shape IMPOSSIBLE — an email lane follows the skeleton, a JSON lane can only
# emit valid JSON — instead of hoping the prompt is obeyed. This is the local
# equivalent of the cloud lanes' strict output rules, enforced at the token level.

GBNF = {
    # A minimal but valid JSON object (for structured extraction presets).
    "json_object": r'''root    ::= object
object  ::= "{" ws (pair (ws "," ws pair)*)? ws "}"
pair    ::= string ws ":" ws value
value   ::= string | number | object | array | "true" | "false" | "null"
array   ::= "[" ws (value (ws "," ws value)*)? ws "]"
string  ::= "\"" ([^"\\] | "\\" .)* "\""
number  ::= "-"? [0-9]+ ("." [0-9]+)?
ws      ::= [ \t\n]*''',

    # An email skeleton: optional Subject, greeting, body, sign-off.
    "email": r'''root     ::= subject? greeting "\n\n" body "\n\n" signoff
subject  ::= "Subject: " line "\n\n"
greeting ::= ("Hi" | "Hello" | "Dear") " " line ","
body     ::= (line "\n"?)+
signoff  ::= ("Best regards," | "Kind regards," | "Thanks,") "\n" line
line     ::= [^\n]+''',
}


def grammar_for(lane):
    """The GBNF grammar that constrains a lane's local-LLM output, or None when
    the lane is free-form (prompt/reply produce prose, not a fixed shape)."""
    return GBNF.get({"email": "email"}.get(lane))


# ============================================================================
# 4. Context-injected reference documents  (Objective §4 — style steering)
# ============================================================================
# Compact system fragments injected into the local model's context to steer
# STYLE without ballooning resources. They are short and STABLE (byte-identical
# across calls) so a llama.cpp prompt-cache keeps them warm — the cost is paid
# once, not per dictation. This is the local mirror of ai.py's system prompts,
# trimmed to what a small model can actually follow.

UK_ENGLISH = (
    "Use British English spelling and grammar (colour, organise, behaviour, "
    "-ise endings). Never use American spellings."
)

STYLE_SHEETS = {
    "prompt": (
        "Turn the user's rough dictation into ONE clean, ready-to-use AI prompt. "
        "Open with a one-line expert role + the goal, then the key requirements as "
        "bullets, most important first. Preserve their intent; remove rambling and "
        "repetition; never invent names, numbers, or deadlines. Output ONLY the "
        "prompt — no preamble, no quotes."
    ),
    "email": (
        "Write a complete, well-structured email in the user's voice: optional "
        "Subject line, a greeting, a concise body, and a sign-off. Keep a clear, "
        "professional, friendly tone. Output ONLY the email."
    ),
    "reply": (
        "Write a natural reply that matches the tone of the message being replied "
        "to. Keep the user's intent and voice. Output ONLY the reply."
    ),
    "text": (
        "Clean up the dictation: fix transcription errors from context, add "
        "punctuation and capitalisation, remove fillers. Keep the user's exact "
        "words and meaning — do not rephrase, summarise, or add anything. Output "
        "ONLY the cleaned text."
    ),
}


def style_for(lane):
    """The context-injected reference sheet for a lane (empty string if none)."""
    return STYLE_SHEETS.get(lane, "")


def build_local_request(lane, text, prefs=None, context=""):
    """Assemble the (system, user, grammar) triple for a local-LLM smart-mode
    call: the lane's style sheet + UK-English rule as a cacheable system prefix,
    the dictation framed as user content, and the lane's GBNF grammar (or None).

    This mirrors how ai.py frames each cloud lane, but trimmed for a small model
    and paired with a hard grammar constraint instead of a long instruction."""
    sheet = style_for(lane)
    system = (sheet + "\n\n" + UK_ENGLISH).strip() if sheet else UK_ENGLISH
    parts = []
    if (context or "").strip():
        parts.append("----- CONTEXT (reference, do not echo) -----\n"
                      + context.strip() + "\n----- END CONTEXT -----")
    parts.append("----- DICTATION -----\n" + (text or "").strip()
                 + "\n----- END DICTATION -----")
    return system, "\n\n".join(parts), grammar_for(lane)
