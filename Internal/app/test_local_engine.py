#!/usr/bin/env python3
"""Offline tests for local_engine.py — hybrid router + local-LLM seam.

Run: python -m pytest test_local_engine.py   (or: python test_local_engine.py)
Pure stdlib; no key, no network, no llama-cpp.
"""

import local_engine as le


# ------------------------------- the router ----------------------------------

def test_cloud_is_primary_when_key_present():
    # Dominance rule: a key present (local-only off) -> CLOUD for every lane.
    for lane in ("text", "foreign", "prompt", "email", "reply", "deck_extract"):
        d = le.route(lane, cloud_key_present=True)
        assert d.engine == le.CLOUD and d.reason == "cloud-dominant", lane


def test_local_only_when_no_key():
    # No key -> local-only gate. Cleanup/foreign run on the rule engine.
    assert le.route("text", cloud_key_present=False).engine == le.LOCAL
    assert le.route("foreign", cloud_key_present=False).engine == le.LOCAL


def test_local_only_toggle_overrides_present_key():
    # Explicit offline toggle wins even when a key exists.
    d = le.route("prompt", cloud_key_present=True, local_only_mode=True,
                 local_llm_ready=True)
    assert d.engine == le.LOCAL_LLM and not d.cloud_augmented


def test_local_only_gate_helper():
    assert le.local_only_active(cloud_key_present=False) is True
    assert le.local_only_active(cloud_key_present=True) is False
    assert le.local_only_active(cloud_key_present=True, local_only_mode=True) is True


def test_smart_lane_local_llm_then_rules_only():
    # local-only smart lane: local LLM if ready, else formatting-ONLY degrade
    # (NO template building — rules-only per the routing directive).
    assert le.route("email", cloud_key_present=False,
                    local_llm_ready=True).engine == le.LOCAL_LLM
    d = le.route("email", cloud_key_present=False, local_llm_ready=False)
    assert d.engine == le.LOCAL and d.reason == "model-free-degrade"
    assert d.rules_only is True


def test_rules_only_indicator():
    # LOCAL == no model == strict formatting-only.
    assert le.route("text", cloud_key_present=False).rules_only is True
    assert le.route("prompt", cloud_key_present=True).rules_only is False  # cloud
    assert le.route("email", cloud_key_present=False,
                    local_llm_ready=True).rules_only is False              # local LLM


def test_foreign_only_resolves_with_a_model():
    # No model -> foreign is formatting-only (term passes through, no boost).
    d = le.route("foreign", cloud_key_present=False, local_llm_ready=False)
    assert d.engine == le.LOCAL and d.rules_only is True
    # A local LLM present -> it may resolve foreign terms with context.
    d2 = le.route("foreign", cloud_key_present=False, local_llm_ready=True)
    assert d2.engine == le.LOCAL_LLM


def test_heavy_task_degrades_with_notice_never_blocks():
    # Directive §3: local-only NEVER hard-blocks. Heavy task -> best-effort LOCAL
    # + an APPENDED upgrade notice, and degraded=True.
    d = le.route("deck_extract", cloud_key_present=False, local_llm_ready=False)
    assert d.engine in (le.LOCAL, le.LOCAL_LLM)        # it still runs
    assert d.degraded is True
    assert d.notice == le.UPGRADE_NOTICE
    assert d.suggested_provider == "cerebras"
    # no "blocked" engine exists anymore
    assert d.engine != "blocked"


def test_oversize_input_degrades_in_local_only():
    big = "word " * 2000
    assert le.classify_task("prompt", big) == "heavy"
    d = le.route("prompt", cloud_key_present=False, local_llm_ready=True,
                 complexity="heavy")
    assert d.degraded is True and d.notice == le.UPGRADE_NOTICE
    assert d.engine == le.LOCAL_LLM            # best-effort, still runs


def test_classify_task():
    assert le.classify_task("email", "short note") == "edge"
    assert le.classify_task("deck_extract") == "heavy"           # heavy lane
    assert le.classify_task("email", "a", n_sources=3) == "heavy"  # multi-doc
    assert le.classify_task("email", "a", preset_requires="cloud") == "heavy"


def test_messages_present():
    # The directive's appended notice, and the original exact phrasing preserved.
    assert "AI Pro Mode" in le.UPGRADE_NOTICE and "cloud API key" in le.UPGRADE_NOTICE
    assert le.CLOUD_REQUIRED_MESSAGE == (
        "This specific preset requires cloud acceleration. "
        "Please link a cloud API key."
    )


def test_cloud_fallback_hint():
    h = le.cloud_fallback_hint()
    assert h["message"] == le.UPGRADE_NOTICE
    assert h["provider"] == "cerebras" and h["free"] is True
    assert h["key_setting"] == "cerebras_api_key"
    assert le.cloud_fallback_hint(strong=True)["message"] == le.CLOUD_REQUIRED_MESSAGE


# --------------------------- local-LLM backend seam --------------------------

def test_default_backend_is_null():
    le.set_backend(None)            # reset to default
    assert le.get_backend().name == "null"
    assert le.local_llm_ready() is False


def test_null_backend_refuses_to_generate():
    nb = le.NullBackend()
    assert nb.available() is False
    raised = False
    try:
        nb.generate("sys", "user")
    except RuntimeError:
        raised = True
    assert raised


def test_llamacpp_backend_unavailable_without_dep_or_model():
    # No model path -> unavailable; bogus path -> unavailable. Never imports
    # llama_cpp just to answer available().
    assert le.LlamaCppBackend().available() is False
    assert le.LlamaCppBackend(model_path="/no/such/model.gguf").available() is False


def test_set_backend_round_trip():
    class FakeReady(le.LocalLLMBackend):
        name = "fake"

        def available(self):
            return True

        def generate(self, system, user, grammar=None, max_tokens=512,
                     temperature=0.3):
            return "ok"

    le.set_backend(FakeReady())
    assert le.local_llm_ready() is True
    # router now prefers the local LLM for a smart lane
    assert le.route("email", local_llm_ready=le.local_llm_ready()).engine == le.LOCAL_LLM
    le.set_backend(None)            # restore default for other tests


# ------------------------------ grammars + style -----------------------------

def test_grammars_present():
    for key in ("json_object", "email"):
        assert key in le.GBNF and "root" in le.GBNF[key]


def test_grammar_for_lane():
    assert le.grammar_for("email") == le.GBNF["email"]
    assert le.grammar_for("prompt") is None    # free-form prose, no grammar
    assert le.grammar_for("reply") is None      # free-form prose, no grammar


def test_style_sheets():
    for lane in ("prompt", "email", "reply", "text"):
        assert le.style_for(lane)              # non-empty
    assert le.style_for("unknown") == ""


def test_build_local_request():
    system, user, grammar = le.build_local_request("email", "tell alex we ship friday")
    assert "email" in system.lower()
    assert "British English" in system
    assert "tell alex we ship friday" in user
    assert grammar == le.GBNF["email"]
    # with context injected
    s2, u2, _ = le.build_local_request("reply", "sounds good", context="their msg")
    assert "their msg" in u2 and "CONTEXT" in u2


# ---------------------- bundled-binary backend + discovery -------------------

def test_discover_model(tmpdir_factory=None):
    import os
    import tempfile
    d = tempfile.mkdtemp()
    # No model yet.
    assert le.discover_model([d]) is None
    # Drop a fake GGUF — it is discovered.
    p = os.path.join(d, "edge-model.gguf")
    open(p, "w").close()
    assert le.discover_model([d]) == os.path.abspath(p)
    # An explicit path wins when it exists.
    p2 = os.path.join(d, "chosen.gguf")
    open(p2, "w").close()
    assert le.discover_model([d], explicit_path=p2) == os.path.abspath(p2)
    # A missing explicit path falls back to discovery.
    assert le.discover_model([d], explicit_path="/no/such.gguf") is not None


def test_build_backend_selection():
    import os
    import tempfile
    # No model → NullBackend.
    b, _ = le.build_backend(model_path=None, cli_bin=None)
    assert b.name == "null"
    # Model + bundled CLI binary, but no llama-cpp-python → the CLI backend.
    d = tempfile.mkdtemp()
    model = os.path.join(d, "m.gguf")
    binp = os.path.join(d, "llama-cli.exe")
    open(model, "w").close()
    open(binp, "w").close()
    b, reason = le.build_backend(model_path=model, cli_bin=binp)
    # llama-cpp-python is not installed in the test venv, so the CLI backend wins.
    assert b.name == "llama_cli" and b.available()
    assert "llama-cli" in reason
    # Model present but neither runtime → Null with an explanatory reason.
    b2, reason2 = le.build_backend(model_path=model, cli_bin=None)
    assert b2.name == "null" and "runtime" in reason2.lower()


def test_llama_cli_cmd_and_grammar():
    cli = le.LlamaCliBackend(model_path="model.gguf", bin_path="llama-cli.exe",
                             n_ctx=2048, n_threads=4)
    cmd = cli._build_cmd("SYS", "USER", grammar_file="g.gbnf",
                         max_tokens=256, temperature=0.2)
    assert cmd[0] == "llama-cli.exe"
    assert "-m" in cmd and "model.gguf" in cmd
    assert "--grammar-file" in cmd and "g.gbnf" in cmd
    assert "-no-cnv" in cmd          # one-shot, not interactive
    # available() is False until both files exist (it never routes to a missing one).
    assert cli.available() is False


def test_strip_cli_echo():
    prompt = "<|system|>\nS\n<|user|>\nU\n<|assistant|>\n"
    # The binary echoes the prompt then the answer.
    assert le._strip_cli_echo(prompt + "Hello.", prompt) == "Hello."
    # Role markers + EOS are scrubbed.
    assert le._strip_cli_echo("<|assistant|>\nHi there</s>", prompt) == "Hi there"


def test_backend_status_roundtrip():
    le.set_backend(le.NullBackend(), "test-null")
    st = le.backend_status()
    assert st["name"] == "null" and st["ready"] is False
    assert st["reason"] == "test-null"


if __name__ == "__main__":
    import sys
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
