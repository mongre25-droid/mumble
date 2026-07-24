#!/usr/bin/env python3
"""Model Process Manager — manages a persistent llama-cli subprocess for GGUF models.

Provides ModelProcessManager: a bridge between ModelManager (file/status tracking)
and the actual llama-cli subprocess that runs on-device inference.

Key responsibilities:
  - Start/stop a persistent llama-cli subprocess (model stays warm between requests)
  - Generate completions by feeding prompts to the running process via stdin
  - Crash recovery: detect subprocess death and auto-restart
  - Memory pressure detection: monitor system RAM and unload when near budget
  - Model switching coordination: queue switches during active generation
  - Single-resident enforcement: only one subprocess at a time

The process runs in interactive mode so the GGUF model stays loaded in RAM.
Generation is synchronous — one prompt at a time — with a threading lock to
serialise access and support switch-during-dictation queuing.

Pure stdlib + optional branding import. No heavy dependencies.
"""

import os
import queue
import subprocess
import threading
import time

# Import branding for the llama-cli binary path and memory info.
# Guarded so the module still imports on unusual platforms.
try:
    from branding import llama_cli_path, LLAMA_BIN_DIR
except ImportError:
    LLAMA_BIN_DIR = ""
    def llama_cli_path():
        return ""

# ============================================================================
# Constants
# ============================================================================

# Memory budget for the single-resident policy (2 GB combined for pipeline).
MEMORY_BUDGET_MB = 2048

# Free RAM threshold below which we trigger model unload (in MB).
MEMORY_PRESSURE_THRESHOLD_MB = 500

# Default number of threads for LLM inference.
DEFAULT_N_THREADS = max(2, (os.cpu_count() or 4) - 2)

# Default context size (token limit).
DEFAULT_N_CTX = 4096

# Maximum time (seconds) to wait for a response after sending a prompt.
GENERATE_TIMEOUT = 120

# Maximum time (seconds) to wait for the subprocess to start up.
STARTUP_TIMEOUT = 30

# Prompt markers used to communicate with the interactive llama-cli process.
PROMPT_PREFIX_USER = "<|user|>\n"
PROMPT_PREFIX_ASSISTANT = "<|assistant|>\n"
PROMPT_SUFFIX = "\n<|end|>\n"


def _get_free_memory_mb():
    """Return the approximate free system RAM in MB, or None if unavailable."""
    try:
        import psutil
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except (ImportError, AttributeError, OSError):
        pass

    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
        return int(mem.ullAvailPhys / (1024 * 1024))
    except (AttributeError, OSError, ValueError):
        pass

    try:
        pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        if pages >= 0 and page_size > 0:
            return int((pages * page_size) / (1024 * 1024))
    except (AttributeError, OSError, ValueError):
        pass

    # macOS does not consistently expose SC_AVPHYS_PAGES. vm_stat reports
    # reclaimable page buckets and is available on every supported Mac.
    try:
        result = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=2,
            check=False)
        if result.returncode == 0:
            import re
            page_match = re.search(r"page size of\s+(\d+) bytes", result.stdout)
            page_size = int(page_match.group(1)) if page_match else 4096
            reclaimable = 0
            for label in ("Pages free", "Pages inactive",
                          "Pages speculative", "Pages purgeable"):
                match = re.search(
                    rf"^{re.escape(label)}:\s+(\d+)\.", result.stdout,
                    re.MULTILINE)
                if match:
                    reclaimable += int(match.group(1))
            if reclaimable:
                return int((reclaimable * page_size) / (1024 * 1024))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def _get_process_memory_mb(process):
    """Return a child process's resident memory in MB, or None if unavailable."""
    pid = getattr(process, "pid", None)
    if not pid:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0010, False,
                                                        int(pid))
            if not handle:
                return None
            try:
                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(counters)
                ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb)
                if ok:
                    return int(counters.WorkingSetSize / (1024 * 1024))
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            return None
        return None
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(int(pid))], capture_output=True,
            text=True, timeout=2, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return int(int(result.stdout.strip().splitlines()[0]) / 1024)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


class ModelProcessManager:
    """Manages a persistent llama-cli subprocess for on-device model inference.

    The subprocess is started in interactive mode and kept warm between requests.
    The GGUF model stays loaded in RAM and accepts multiple prompts until
    explicitly unloaded. This avoids the per-request load cost of one-shot mode.

    Usage:
        mgr = ModelProcessManager(
            bin_path="path/to/llama-cli.exe",
            n_ctx=4096,
            n_threads=4,
            memory_limit_mb=2048,
        )
        mgr.start("path/to/model.gguf")
        output = mgr.generate("What is the capital of France?")
        mgr.stop()
    """

    def __init__(self, bin_path=None, n_ctx=None, n_threads=None,
                 memory_limit_mb=None, memory_pressure_threshold_mb=None):
        """Create a ModelProcessManager.

        Args:
            bin_path: Absolute path to the llama-cli binary. Defaults to the
                      bundled binary from branding.llama_cli_path().
            n_ctx: Context size in tokens. Default 4096.
            n_threads: Number of CPU threads for inference. Default auto-detected.
            memory_limit_mb: Single-resident memory budget in MB. Default 2048.
            memory_pressure_threshold_mb: Free RAM threshold (MB) that triggers
                                          model unload. Default 500.
        """
        self.bin_path = bin_path or llama_cli_path()
        self.n_ctx = n_ctx or DEFAULT_N_CTX
        self.n_threads = n_threads or DEFAULT_N_THREADS
        self.memory_limit_mb = memory_limit_mb or MEMORY_BUDGET_MB
        self.memory_pressure_threshold_mb = (
            memory_pressure_threshold_mb or MEMORY_PRESSURE_THRESHOLD_MB
        )

        # Subprocess state.
        self._process = None          # subprocess.Popen or None
        self._model_path = None       # currently loaded model path
        self._system_prompt = ""
        self._lock = threading.RLock()
        self._pending_lock = threading.Lock()
        self._generating = False      # True during an active generate() call
        self._pending_model = None    # queued model path for switch-during-dictation
        self._pending_callback = None # notified after a queued switch is applied
        self._crash_count = 0         # consecutive crash counter for backoff
        self._max_crash_retries = 3   # give up after this many consecutive crashes
        self._stdout_queue = None
        self._stdout_thread = None
        self._stderr_thread = None

    # ------------------------------------------------------------------
    # Public API — lifecycle
    # ------------------------------------------------------------------

    def start(self, model_path, system_prompt=""):
        """Start a persistent llama-cli subprocess with the given model.

        If a subprocess is already running, it is stopped first (single-resident
        policy). Blocks until the process is ready to accept prompts.

        Args:
            model_path: Absolute path to the GGUF model file.
            system_prompt: Optional system-level instruction prepended to every
                           completion (e.g., style sheet, UK English rule).

        Returns:
            True if the process started successfully, False otherwise.
        """
        if not model_path or not os.path.exists(model_path):
            return False
        if not self.bin_path or not os.path.exists(self.bin_path):
            return False

        with self._lock:
            # Single-resident: stop any existing process first.
            if self._process is not None:
                self._stop_locked()

            self._model_path = model_path
            self._system_prompt = system_prompt or ""
            self._crash_count = 0

            # Build the command line for interactive mode.
            cmd = [
                self.bin_path,
                "-m", model_path,
                "-c", str(self.n_ctx),
                "-t", str(self.n_threads),
                "-i",               # interactive mode — keeps model warm
                "--simple-io",       # cleaner output, no escape codes
                "--temp", "0.3",
            ]

            if system_prompt:
                # Seed the conversation with a system prompt so the model
                # loads with the right context.
                cmd += ["-p", system_prompt.strip()]

            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,  # line-buffered
                    cwd=os.path.dirname(self.bin_path) or None,
                )
            except (OSError, subprocess.SubprocessError):
                self._process = None
                self._model_path = None
                return False

            self._start_pipe_readers_locked()
            # Consume initial output (model loading info, system prompt echo).
            self._drain_startup()
            return self.is_running()

    def stop(self):
        """Terminate the llama-cli subprocess and free all resources."""
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        """Stop the subprocess. Caller must hold self._lock."""
        if self._process is None:
            return
        proc = self._process
        self._process = None
        self._model_path = None
        self._generating = False
        # Handle test stubs (non-Popen objects) gracefully.
        if not hasattr(proc, 'terminate'):
            self._reset_pipe_readers_locked()
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
        # Close remaining pipes to avoid ResourceWarning.
        for pipe in (proc.stdout, proc.stderr):
            if pipe:
                try:
                    pipe.close()
                except OSError:
                    pass
        self._reset_pipe_readers_locked()

    def is_running(self):
        """Check if the subprocess is alive and ready."""
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def model_path(self):
        """The path of the currently loaded model, or None."""
        return self._model_path

    @property
    def loaded(self):
        """Whether a model is currently loaded (process is running)."""
        return self.is_running()

    # ------------------------------------------------------------------
    # Public API — generation
    # ------------------------------------------------------------------

    def generate(self, user_text, max_tokens=512, temperature=0.3,
                 grammar=None):
        """Send a prompt to the running model and return the completion.

        If the subprocess has crashed, attempts a restart once before giving up.
        If the process is not running and cannot be restarted, raises RuntimeError.

        Args:
            user_text: The user prompt to send to the model.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature (0.0–2.0).
            grammar: Optional GBNF grammar string (not used in interactive mode).

        Returns:
            The model's completion text, stripped of prompt echo and markers.

        Raises:
            RuntimeError: If the model is not running and cannot be started.
        """
        callback = None
        callback_success = False
        try:
            with self._lock:
                # Check for crash and attempt recovery.
                if self._process is not None and self._process.poll() is not None:
                    self._handle_crash_locked()

                if self._process is None:
                    raise RuntimeError(
                        "Model is not loaded. Call start() with a model path first."
                    )

                with self._pending_lock:
                    self._generating = True
                try:
                    result = self._generate_locked(
                        user_text, max_tokens, temperature
                    )
                    self._crash_count = 0
                    # Check for memory pressure after generation.
                    self._check_memory_pressure_locked()
                    return result
                finally:
                    with self._pending_lock:
                        self._generating = False
                    # Apply any pending model switch.  The callback is invoked
                    # only after releasing the process lock to avoid lock-order
                    # deadlocks with ModelManager.
                    callback, callback_success = \
                        self._apply_pending_switch_locked()
        finally:
            if callback is not None:
                callback(callback_success)

    # ------------------------------------------------------------------
    # Public API — model switching
    # ------------------------------------------------------------------

    def switch_model(self, new_model_path, system_prompt="", on_complete=None):
        """Request a model switch. If generation is in progress, the switch is
        queued and applied after the current generation completes.

        Args:
            new_model_path: Absolute path to the new GGUF model file.
            system_prompt: Optional system prompt for the new model.

        Returns:
            True if the switch was applied immediately, False if queued.
        """
        callback = None
        success = False
        # Generation deliberately holds the subprocess lock while it performs
        # blocking I/O.  Inspecting the queue state under its own short-lived
        # lock lets a concurrent switch return promptly instead of waiting for
        # generation to finish and then switching synchronously.
        with self._pending_lock:
            if self._generating:
                self._pending_model = (new_model_path, system_prompt)
                self._pending_callback = on_complete
                return False
        with self._lock:
            # Apply immediately.
            self._stop_locked()
            success = self.start(new_model_path, system_prompt)
            callback = on_complete
        if callback is not None:
            callback(success)
        return success

    def _apply_pending_switch_locked(self):
        """Apply a queued model switch. Caller must hold self._lock."""
        with self._pending_lock:
            if self._pending_model is None:
                return None, False
            new_path, sys_prompt = self._pending_model
            callback = self._pending_callback
            self._pending_model = None
            self._pending_callback = None
        self._stop_locked()
        success = self.start(new_path, sys_prompt)
        return callback, success

    def apply_pending_switch(self):
        """Apply a queued switch now and notify its owner after unlocking."""
        with self._lock:
            callback, success = self._apply_pending_switch_locked()
        if callback is not None:
            callback(success)
        return success

    # ------------------------------------------------------------------
    # Public API — memory pressure
    # ------------------------------------------------------------------

    def check_memory_pressure(self):
        """Check if the system is under memory pressure and should unload.

        Returns True if free RAM is below the pressure threshold, meaning
        the model should be unloaded to free memory for other stages.
        """
        resident_mb = _get_process_memory_mb(self._process)
        if (resident_mb is not None and self.memory_limit_mb > 0
                and resident_mb >= self.memory_limit_mb):
            return True
        free_mb = _get_free_memory_mb()
        if free_mb is None:
            return False
        return free_mb < self.memory_pressure_threshold_mb

    def unload_if_pressure(self):
        """Unload the model if memory pressure is detected. Returns True if
        the model was unloaded, False otherwise."""
        if self.check_memory_pressure():
            self.stop()
            return True
        return False

    # ------------------------------------------------------------------
    # Internal — subprocess communication
    # ------------------------------------------------------------------

    @staticmethod
    def _pump_pipe(pipe, output_queue=None):
        """Drain a subprocess text pipe without blocking the calling thread."""
        try:
            while True:
                line = pipe.readline()
                if not line:
                    break
                if output_queue is not None:
                    output_queue.put(line)
        except (OSError, ValueError):
            pass
        finally:
            if output_queue is not None:
                output_queue.put(None)

    def _start_pipe_readers_locked(self):
        proc = self._process
        if proc is None:
            return
        self._stdout_queue = queue.Queue()
        if getattr(proc, "stdout", None) is not None:
            self._stdout_thread = threading.Thread(
                target=self._pump_pipe,
                args=(proc.stdout, self._stdout_queue),
                daemon=True,
                name="mumble-llama-stdout",
            )
            self._stdout_thread.start()
        if getattr(proc, "stderr", None) is not None:
            self._stderr_thread = threading.Thread(
                target=self._pump_pipe,
                args=(proc.stderr,),
                daemon=True,
                name="mumble-llama-stderr",
            )
            self._stderr_thread.start()

    def _reset_pipe_readers_locked(self):
        self._stdout_queue = None
        self._stdout_thread = None
        self._stderr_thread = None

    def _drain_startup(self):
        """Read and discard the initial output from the subprocess (model loading
        messages, system prompt echo). After this returns, the process should be
        ready to accept the first user prompt."""
        if self._process is None:
            return
        q = self._stdout_queue
        if q is None:
            return
        deadline = time.monotonic() + STARTUP_TIMEOUT
        saw_output = False
        while time.monotonic() < deadline:
            if self._process is None or self._process.poll() is not None:
                return
            wait = min(0.1, max(0.0, deadline - time.monotonic()))
            try:
                line = q.get(timeout=wait)
            except queue.Empty:
                if saw_output:
                    return
                continue
            if line is None:
                return
            saw_output = True

    def _generate_locked(self, user_text, max_tokens, temperature):
        """Send a prompt and read the response. Caller must hold self._lock and
        ensure self._process is alive."""
        proc = self._process
        if proc is None or proc.poll() is not None:
            raise RuntimeError("Subprocess not running")

        # Format the user prompt with clear boundaries.
        prompt = f"{PROMPT_PREFIX_USER}{user_text}{PROMPT_SUFFIX}{PROMPT_PREFIX_ASSISTANT}"

        q = self._stdout_queue
        if q is None:
            self._start_pipe_readers_locked()
            q = self._stdout_queue
        if q is None:
            raise RuntimeError("Subprocess output pipe is unavailable")
        try:
            while True:
                item = q.get_nowait()
                if item is None:
                    q.put(None)
                    break
        except queue.Empty:
            pass

        try:
            proc.stdin.write(prompt + "\n")
            proc.stdin.flush()
        except (OSError, BrokenPipeError):
            self._handle_crash_locked()
            raise RuntimeError("Subprocess crashed while sending prompt")

        # Read the response. The model will output text and then print the
        # next prompt prefix when ready for more input. We collect text until
        # we see the prompt prefix again or timeout.
        output_parts = []
        completed = False
        deadline = time.monotonic() + GENERATE_TIMEOUT
        try:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    # Process exited during generation.
                    self._handle_crash_locked()
                    raise RuntimeError("Subprocess crashed during generation")

                try:
                    line = q.get(timeout=min(
                        0.1, max(0.0, deadline - time.monotonic())
                    ))
                except queue.Empty:
                    continue
                if line is None:
                    self._handle_crash_locked()
                    raise RuntimeError("Subprocess output closed during generation")
                if not line:
                    # No data available yet — brief wait.
                    if output_parts and proc.poll() is None:
                        # If we've already gotten some output and the process
                        # is still alive, we might be done. Check if output
                        # has stabilized.
                        time.sleep(0.1)
                        continue
                    if proc.poll() is not None:
                        break
                    continue

                # Check if the model has printed the next prompt prefix,
                # indicating it's ready for more input.
                stripped = line.rstrip("\n\r")
                if stripped == PROMPT_PREFIX_USER.rstrip("\n") or \
                   stripped == PROMPT_PREFIX_ASSISTANT.rstrip("\n"):
                    completed = True
                    # Model is prompting for more input — done generating.
                    break
                # Check for EOS tokens.
                if stripped in ("</s>", "[end of text]"):
                    completed = True
                    break

                output_parts.append(line)
        except (OSError, ValueError):
            self._handle_crash_locked()
            raise RuntimeError("Subprocess crashed during generation")

        if not completed:
            self._handle_crash_locked()
            raise RuntimeError("Local model generation timed out")

        # Clean up the output: strip marker lines and echo.
        return self._clean_output("".join(output_parts), user_text=user_text)

    def _clean_output(self, raw, user_text=""):
        """Strip prompt markers, role tokens, and echoed input from the output."""
        if not raw:
            return ""
        if "<|assistant|>" in raw:
            raw = raw.rsplit("<|assistant|>", 1)[-1]
        # Remove role markers.
        for marker in ("<|system|>", "<|user|>", "<|assistant|>", "<|end|>",
                       "</s>", "[end of text]"):
            raw = raw.replace(marker, "")
        # Remove leading/trailing whitespace and blank lines.
        lines = [l.strip() for l in raw.split("\n")]
        lines = [l for l in lines if l]
        cleaned = "\n".join(lines).strip()
        echoed = str(user_text or "").strip()
        if echoed and cleaned.startswith(echoed):
            cleaned = cleaned[len(echoed):].lstrip()
        return cleaned

    # ------------------------------------------------------------------
    # Internal — crash recovery
    # ------------------------------------------------------------------

    def _handle_crash_locked(self):
        """Handle a subprocess crash. Caller must hold self._lock.

        Records the crash, cleans up the dead process, and if under the retry
        limit, attempts a restart. After max_crash_retries consecutive crashes,
        gives up and leaves the process as None.
        """
        if self._process is not None:
            try:
                if self._process.poll() is None:
                    self._process.kill()
                    self._process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
            # Close pipes.
            for pipe in (self._process.stdin, self._process.stdout,
                         self._process.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except OSError:
                        pass
            self._process = None
            self._reset_pipe_readers_locked()

        self._crash_count += 1
        saved_model = self._model_path
        self._model_path = None

        # Attempt restart if under the limit.
        if self._crash_count <= self._max_crash_retries and saved_model:
            # Exponential backoff: 1s, 2s, 4s...
            delay = min(2 ** (self._crash_count - 1), 10)
            time.sleep(delay)
            crash_count = self._crash_count
            self.start(saved_model, self._system_prompt)
            self._crash_count = crash_count

    # ------------------------------------------------------------------
    # Internal — memory pressure
    # ------------------------------------------------------------------

    def _check_memory_pressure_locked(self):
        """Check memory and unload if under pressure. Caller must hold self._lock."""
        if self.check_memory_pressure():
            self._stop_locked()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def __repr__(self):
        running = self.is_running()
        model = os.path.basename(self._model_path) if self._model_path else None
        return (f"ModelProcessManager(running={running}, model={model}, "
                f"threads={self.n_threads}, ctx={self.n_ctx})")
