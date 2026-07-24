# Processing-mode truth and local-versus-hosted routing investigation

**Decision record:** GitHub issue [#10](https://github.com/mongre25-droid/mumble/issues/10)

**Investigation date:** 24 July 2026

**Scope:** diagnosis, measurement, and implementation guidance only. No product behaviour, provider account, model installation, or platform port was changed.

**Checkout:** `codex/wayfinder-10-processing-truth` at `5e19b1ef87e1276803d0fbb5090c7fa593339c8d` before this report

## Executive decision

Mumble does not currently have one truthful processing-mode system. It has three related but incompletely joined systems:

1. **Visual effects** use the values `lite`, `standard`, and `enhanced`. The System control calls these Basic, Standard, and Enhanced. This is presentation quality, not text-processing quality.
2. **Text processing** uses `instant_text`, `pro_mode`, `local_only_mode`, provider/key readiness, the selected lane, and optional local-model readiness. The Settings summary projects only part of this state.
3. **Individual features** do not all use the same routing gate. Ordinary dictation uses `local_engine.route()`, but Deck jobs and Meetings analysis bypass that policy.

The reported “Basic selected while Enhanced is active” problem is reproducible and its immediate cause is established: the real Settings `<select>` starts on its first HTML option, Basic, while startup separately applies the saved Enhanced class to the document. The control is not hydrated until Settings is opened, and that hydration is asynchronous. On a successful bridge response it eventually corrects itself; while the response is pending, or permanently if hydration fails, the user sees a false value.

This is **not** a persistence defect on the investigated machine. The persisted value was `enhanced`, Resource Saver was `false`, the backend default was `enhanced`, and there was only one `data-setting="ui_effects"` control per platform. A real Chromium/Web UI probe reproduced the mismatch five times out of five:

```text
before Settings navigation: control=lite, bodyEnhanced=true
first Settings frame:       control=lite, bodyEnhanced=true
after hydration:            control=enhanced, bodyEnhanced=true
```

The more serious result is routing truth. A content-free, fully mocked probe proved that both Deck processing and Meetings analysis enter the Cerebras-compatible hosted call path when `local_only_mode=true` and a key is present. Meetings analysis also builds a hosted call context when `pro_mode=false`. No network request was made: the provider functions were replaced by local recorders. This violates the Settings promise that the device-only override prevents transcript text from leaving the device.

The required order is therefore:

1. **Close the privacy-policy gaps** in Deck and Meetings and add red tests at the actual call seam.
2. **Create one complete immutable route snapshot** shared by dictation, Deck, Meetings, and the Settings disclosure.
3. **Remove the false first render** by hydrating settings before exposing editable controls, and show saved versus effective state where an override exists.
4. **Benchmark local candidates against hosted processing** with the fixed harness defined here. Do not choose a model from marketing speed, upstream benchmark scores, or generic prompts.

## Evidence boundaries

- “Observed” means executed on this checkout with fixed synthetic text and no personal content.
- “Code-traced” means the live call path was followed in the current source, but not exercised against an external provider.
- “Upstream” means a provider or model author’s claim. It is useful for candidate selection, not a Mumble result.
- No Cerebras or OpenRouter completion was invoked. Cerebras documents a free tier, but a key alone does not prove that its organization is currently on that tier rather than Pay as You Go. The account has to be checked in the provider console before a no-cost benchmark is authorized.[^cerebras-pricing][^cerebras-console]
- Local model stages were tested in offline mode. This installation had the bundled `llama-cli.exe` but no GGUF model, and only deterministic cleanup was available.
- The five-item quality checks below are diagnostic smoke checks, not a statistically valid quality score.

## 1. The visible Basic-versus-Enhanced defect

### Reproduction loop

The temporary probe loaded the real [`Internal/app/webui/index.html`](../../Internal/app/webui/index.html) and real [`Internal/app/webui/app.js`](../../Internal/app/webui/app.js) in headless Chromium. It waited for the normal preview boot, inspected the real `data-setting="ui_effects"` control, called the real `navTo("settings")`, inspected the first frame, then inspected the control after `hydrateSettings()` completed. It was run five independent times with the same result shown above.

The probe was deleted after diagnosis. No test-only file remains in the product tree.

### Root cause

The following facts form one causal chain:

1. The System control’s first option is `value="lite"` with label **Basic** (`index.html`, System → Visual effects).
2. HTML selects the first option when no option has a static `selected` attribute, so the unhydrated control begins as `lite`.
3. Startup independently calls `get_settings()`, then `applyEffects(st.ui_effects)` and `applySaver(st.resource_saver)`. This correctly applies `body.enhanced` when the stored setting is Enhanced (`app.js`, `boot()`).
4. Startup does **not** bind the returned settings object into the controls.
5. `navTo("settings")` reveals the view and calls `hydrateSettings()` without awaiting it. The view can therefore paint before the bridge returns.
6. `hydrateSettings()` later calls `get_settings()` again and writes each returned value into every matching control.
7. Windows catches a failed `get_settings()` and leaves the existing configuration unchanged, but it also leaves the false Basic control visible. The macOS and Linux copies assign the awaited result without this guard, so a rejection becomes an unhandled failed hydration while the false first option remains.

The persisted runtime evidence eliminates persistence as the primary cause for this report:

```json
{"persisted_ui_effects":"enhanced","persisted_resource_saver":false}
```

The backend default in [`Internal/app/settings.py`](../../Internal/app/settings.py) is also `"ui_effects": "enhanced"`, validation accepts all three values, and [`Internal/app/webui_shell.py`](../../Internal/app/webui_shell.py) includes `ui_effects` in `get_settings()`. Onboarding also accepts and persists Enhanced. These paths are consistent.

### Hypotheses resolved

| Hypothesis | Falsifier | Result |
|---|---|---|
| Stored state is Basic | Load the live settings file through `Settings.load()` | Rejected: stored state was Enhanced |
| Default UI state overwrites stored state | Observe a settings write during startup/navigation | No write was found in this seam; the wrong value is a render default |
| State hydration is late | Observe first frame and post-hydration frame separately | Confirmed in 5/5 browser runs |
| Backend and frontend enum values differ | Compare defaults, validation, bridge payload, and option values | Rejected: `lite/standard/enhanced` agree |
| A hidden duplicate control receives the update | Count matching controls on every platform | Rejected: one matching control per platform |
| Resource Saver makes the effective mode Basic | Read Resource Saver and compare saved/effective state | Rejected for the reported machine: it was off. This remains a separate disclosure requirement |

### Correct state contract

Settings needs three explicit concepts, never one overloaded control:

| State | Meaning | Example |
|---|---|---|
| **Saved** | What the user chose and what persistence contains | Full effects |
| **Effective** | What is actually running after overrides and availability checks | Light effects because Resource Saver is on |
| **Reason** | Why effective differs from saved | Resource Saver override |

The first editable Settings frame must never invent a saved value. Use one of these equivalent patterns:

- load settings before revealing the Settings panels, showing a short local skeleton in the meantime; or
- hydrate once during boot into a shared Settings snapshot, apply both the page classes and controls from that same snapshot, and refresh it atomically when Settings opens.

On bridge failure, disable the controls and say that settings could not be read. Do not leave a usable-looking Basic value on screen. `navTo()` should either await the settings-view preparation or the Settings view should own an explicit `loading/ready/error` state.

Resource Saver should not silently rewrite the selected visual tier. The control should continue to show the saved tier and a nearby status should say, for example, **“Currently using Light effects because Resource Saver is on.”**

Finally, rename the visual choices to **Light effects / Standard effects / Full effects**. “Basic” and “Enhanced” are too easily mistaken for processing capability. This matches the [interface-contract recommendation](mumble-interface-contract-research.md#settings), which separately names text shaping **On this device / Hosted provider**.

## 2. Current end-to-end routing truth

### Ordinary dictation

At `_process()` start, Mumble captures these processing values: `pro_mode`, `local_only_mode`, `llm_provider`, language flags, `format_enabled`, and `instant_text`. It resolves an explicit island lane to Prompt or Email; without an active lane it forces Text and performs no automatic mode inference.

The effective path is:

| User action and state | Actual path | Leaves device? | Current quality boundary |
|---|---|---:|---|
| Plain dictation, `instant_text=true` | `formatting.format_transcript(..., commands=False)` | No | Fast deterministic cleanup; no semantic rewrite |
| Plain dictation, Instant Text off, Pro on, supported key, device-only off | hosted `polish_text()` | Transcript text only | Provider can do semantic polish; audio is not sent by this route |
| Plain dictation, hosted unavailable or forbidden | optional local LLM for smart lanes is skipped for Text, then local pipeline | No | Current installation had cleanup only |
| Prompt or Email, Pro on, supported key, device-only off | hosted lane-specific prompt | Transcript plus lane context as applicable | Strongest currently implemented semantic lane |
| Prompt or Email, no key / Pro off / device-only on | optional local GGUF → staged local pipeline → deterministic builder | No | Current installation degrades to cleanup plus an upgrade notice |
| Hosted call fails | optional local GGUF → local pipeline → builder | No after failure | User receives fallback; the failed request already transmitted its input |

The `instant_text` override is why the Settings summary can truthfully say **split route: plain local, actions hosted**. However, that is only true for the ordinary dictation path.

### Punctuation and capitalisation

The latency-critical plain path relies on speech-to-text punctuation plus `format_transcript()`. That function removes filled pauses, normalizes spacing, inserts a limited set of commas, capitalizes sentence starts and the pronoun “I”, and supplies terminal punctuation. The live app deliberately disables spoken layout commands to avoid false positives.

The optional staged pipeline names FullStop punctuation, a grammar GGUF, a Qwen formatting GGUF, and deterministic cleanup. On this machine:

```text
available stages: cleanup
unavailable: punctuation, grammar, mode formatting
```

The [local-AI candidate report](local-ai-candidate-research.md#executive-decision) already corrected the relevant planning assumption: FullStop’s current weights are about 2.24 GB, not the old approximately 200 MB expectation. It should remain an isolated research control, not be silently downloaded or bundled.

### Prompt interpretation and mode selection

Prompt and Email are explicit island modes. Reply is intentionally a Deck/backend lane, not an island mode. List was retired as an independent Smart Mode; ordinary dictation no longer interprets spoken list commands. This reduces accidental transformations, but documentation and comments that still describe Prompt/Email/List/Reply as equivalent active lanes are stale.

The cloud “second opinion” code for missed spoken mode keywords remains in `_cloud_generate()`, but the current `_process()` forces Text when no island mode is active and the historical held mode key is retired. That path should be treated as legacy until a live entry point proves otherwise.

### Command classification

[`Internal/app/voice_commands.py`](../../Internal/app/voice_commands.py) contains a very fast deterministic parser for cancel, Deck, dictation, web search, mode changes, and computer-control requests. The current runtime source has no import or call of `parse_voice_command`; only its test module imports it. It is therefore **implemented and tested as a library, but not wired into the live controller**.

This matters to route disclosure: Settings must not imply local command classification is an active feature until the invocation and safety gates are connected. The [local-AI candidate report](local-ai-candidate-research.md#executive-decision) correctly keeps regex as the authority and admits Model2Vec only as a fail-closed shadow experiment.

### Rewriting, email, reply, list, and presets

- **Email from the island:** ordinary dictation routing; hosted when permitted, optional local GGUF when present, cleanup-only degradation today.
- **Reply:** exposed mainly as a Deck preset/output form. The dormant ordinary `_generate()` reply lane can run hosted or local if called, but it is not an island choice.
- **Rewrite, Summarise, Answer, Extract, Humanise, Professional and similar presets:** Deck jobs call `cerebras_intent()` through the selected Cerebras-compatible/OpenRouter endpoint. They have no current local execution path.
- **List:** not an island or Deck mode choice. List-like outputs are produced by a preset instruction or a model, not by the live deterministic spoken-command formatter.

The local pipeline’s Stage 3 technically supports a `list` grammar, but `local_engine.SMART_LANES` excludes List and the live UI does not route a List job into it. This is an integration seam, not a working local List feature.

### Meetings

Meeting audio transcription follows its own local/cloud speech-to-text metadata and the meeting recorder stores whether audio was sent. Summary, action items, decisions, questions, and Deep Process are different: `_analysis_context()` resolves any configured provider key and `_analysis_call()` invokes the provider-compatible chat endpoint.

The saved `meeting_processing_mode` value labels records as `lightweight` or `deep` and Deep Process is an explicit button. It does not select a local analysis engine. The individual Summary/Actions/Decisions/Questions buttons are also hosted analysis calls.

No local meeting-summary or extraction implementation was found.

## 3. Privacy and consistency defects

### P0 — device-only override is bypassed by Deck jobs

`_run_deck_job_impl()` checks only `pro_mode` and whether `_ai_key()` is non-empty. It never checks `local_only_mode` and does not call `local_engine.route()`.

The synthetic probe used `pro_mode=true`, `local_only_mode=true`, and a fake key. `ai.cerebras_intent` was replaced with an in-process recorder. The recorder was called with the hosted endpoint. This proves the route decision without transmitting content.

Impact: selected Deck material can be sent to a hosted provider while Settings says the device-only override prevents transcript text upload.

### P0 — device-only and Pro-off overrides are bypassed by Meetings analysis

`meeting._analysis_context()` reads provider, key, and model. It does not inspect `local_only_mode` or `pro_mode`. In a second synthetic probe it returned a usable hosted context for all three combinations:

```text
pro=false, local_only=false -> hosted context created
pro=false, local_only=true  -> hosted context created
pro=true,  local_only=true  -> hosted context created
```

With `ai.cerebras_chat` replaced by a recorder, `_analysis_call()` entered the hosted endpoint path. Again, no real call was made.

Impact: a meeting transcript can leave the device contrary to both the device-only override and the visible Pro toggle.

### P1 — the dictation snapshot is incomplete

The `_process()` snapshot captures the provider identifier but not the provider key, model, URL, prompt preferences, user name, primary language, local-model readiness, or context inputs. `_generate()` then calls `_ai_cfg()` again, which reads the current provider/key/model, while `_cloud_generate()` reads language and vocabulary settings again.

Therefore the comments claiming a fully frozen in-flight AI configuration are stronger than the implementation. A provider or key changed after `_process()` begins but before `_generate()` resolves can affect the same dictation. The route decision may also have been made using the pre-change key state, producing a mixed snapshot.

The replacement contract should snapshot a non-secret `RouteDecision` plus the exact immutable invocation configuration in one operation:

```text
request id, feature, lane, requested route, effective route, reason,
provider id, endpoint class, model id, key presence/key version (not key bytes in logs),
local-only, Pro enabled, Instant Text, local model readiness,
input categories to be sent, context policy, timestamp
```

The secret key can be retained in the private in-memory invocation object but must never be logged. Every downstream layer receives this object and may not re-read Settings during that job.

### P1 — UI disclosure is a projection, not runtime authority

`webui_shell._settings_route_state()` correctly projects speech-to-text, plain processing, and action processing for the main Settings summary. Existing route tests pass. But Deck and Meetings do not consume this policy, so the display can be truthful about its own calculation while false about feature behaviour.

One pure policy function must supply both the UI projection and the runtime decision. A display-only calculator is insufficient.

### Cross-platform result

The same Deck condition and Meetings `_analysis_context()` exist in Windows, macOS, and Linux copies. The privacy defects are therefore cross-platform, not Windows-only. The Basic-first hydration seam also exists on every platform; macOS and Linux have weaker error handling in `hydrateSettings()`.

The fixes must be made once in shared policy where possible, then covered by equivalent platform entry-point tests. Copying another conditional into three controllers would preserve the underlying drift risk.

## 4. Measured local baseline

### Environment

- Windows checkout described above
- Python 3.12
- `MUMBLE_OFFLINE_TESTS=1`; no provider traffic
- bundled `Internal/app/llama-cpp-bin/llama-cli.exe` present
- no GGUF discovered in `%APPDATA%\Mumble\models`
- local backend: `null`, reason `no GGUF model found`
- pipeline stages available: deterministic cleanup only

### Micro-latency

Each timing is the median of seven runs. The direct functions used 20,000 iterations per run; the complete degraded pipeline used 1,000 because it emits degradation bookkeeping.

| Operation | Median per call | Observed range | Meaning |
|---|---:|---:|---|
| Deterministic `format_transcript` | 0.106 ms | 0.085–0.126 ms | Not a meaningful contributor to perceived dictation delay |
| Deterministic voice-command parser | 0.00114 ms | 0.00109–0.00140 ms | Classification cost is negligible, but the parser is not live-wired |
| Pure route decision | 0.000252 ms | 0.000245–0.000264 ms | Centralizing routing will not create user-visible latency |
| Current degraded four-stage pipeline | 1.424 ms | 1.214–1.494 ms | Only Stage 4 ran; this is not a local-LLM benchmark |

These results rule out deterministic formatting and route selection as explanations for noticeable processing latency. Model inference, provider round trip, audio/STT work, context gathering, retry waits, and paste are the material candidates.

### Small quality smoke check

Five fixed synthetic dictations were compared with hand-written expected surface text. Deterministic formatting matched three of five exactly. It capitalized “I,” repaired glued sentence punctuation, and inferred a closing question mark. It did not add the expected comma in “Hello world, this is a test,” and did not collapse “is is.”

This is consistent with the intended boundary: the deterministic route is a fast, privacy-preserving cleanup floor, not semantic rewriting or robust grammar correction.

The five deterministic command examples all mapped to the expected intent/value, including cancel, Deck, web search, Email mode and a generic computer-control request. This checks known examples only; the safety requirement remains fail-closed evaluation across accents, partial speech, homophones, background audio, and negative examples.

The only-stage-4 pipeline produced essentially the same cleaned sentence for Text, Prompt, Email, Reply and List, followed by the same upgrade notice. It cannot currently supply meaningful task-specific local quality.

## 5. Hosted comparison: what is known and what is not

Cerebras currently documents a $0 free tier with lower rate limits and documents `gpt-oss-120b` free-tier limits separately from Pay as You Go.[^cerebras-pricing][^cerebras-limits] Its supported-model page advertises approximately 3,000 output tokens per second for `gpt-oss-120b`.[^cerebras-models] That is provider-side generation speed, not Mumble activation-to-paste latency or quality.

No live comparison was run because the local key’s billing tier and auto-recharge state were not established. This was the only choice consistent with the no-billable-call boundary. Consequently:

- hosted time to first token, final latency, retry rate, and current model quality are **unmeasured**;
- the source comment claiming approximately 0.4–0.5 seconds round trip is **not accepted as current evidence**;
- no conclusion that Cerebras is “much better” or “only slightly better” is justified yet.

Code capabilities do support a cautious prior:

- hosted processing has lane-specific prompts, large-model semantic interpretation, conversation/clipboard context where allowed, and structured meeting extraction;
- deterministic local processing cannot match those semantic tasks;
- a small local GGUF may close much of the gap for short explicit Prompt, Email, Reply and Rewrite tasks, but no such model is installed or Mumble-benchmarked here;
- privacy, offline operation, predictable marginal cost, and avoidance of network/retry latency are intrinsic local advantages.

### Task-by-task comparison and present decision

“Quality gap” below is deliberately qualitative where no paired run exists. It describes the capability difference in current code, not a measured Cerebras win.

| Task | Current local path | Current hosted path | Measured latency evidence | Quality evidence and decision |
|---|---|---|---|---|
| Plain punctuation/capitalisation | Deterministic cleanup; 0.106 ms median | General polish when Instant Text is off | Hosted unmeasured | Local passed 3/5 exact smoke checks. Keep local default; use hosted only by explicit preference |
| Prompt interpretation | Current installation degrades to cleanup plus notice; optional GGUF seam exists | Prompt constitution on `gpt-oss-120b` or selected OpenRouter model | Local semantic unmeasured; hosted unmeasured | Capability gap is large today because no local model is installed. Benchmark Qwen3/Qwen2.5/Granite before reallocating |
| Email drafting | Same cleanup-only degradation today; optional local GGUF seam | Focused Email prompt with user name/context | Both semantic paths unmeasured | Hosted remains the only implemented fluent path on this installation; local candidate is high priority |
| Reply drafting | Local seam exists but Reply is mainly a Deck path and has no active local Deck runner | Focused Reply/Deck intent with selected context | Unmeasured | Hosted is functionally ahead; first make privacy gating correct, then prototype local short-context Reply |
| Rewrite / Humanise / Professional | No local Deck executor | Hosted Deck intent | Unmeasured | Hosted-only today. Short single-source rewrites are appropriate local-candidate tasks |
| List formatting | Stage 3 has a grammar but is uninstalled and not routed from live UI | Preset instruction through Deck | Deterministic formatting cost is negligible; semantic paths unmeasured | Use deterministic structure for literal lists; benchmark local model for semantic regrouping |
| Command classification | Regex parser, 0.00114 ms median, 5/5 smoke examples; not live-wired | No justified hosted classifier | Local only | Keep deterministic authority and wire it only behind existing safety gates; do not add Cerebras here |
| Meeting summary/actions/decisions/questions | No local analysis implementation | Hosted chat map/reduce and JSON extraction | Unmeasured | Hosted is the only semantic implementation, but its privacy gate is defective. Fix gate before further use; benchmark a long-context local candidate later |
| Multi-source Deck reasoning | Router models it as heavy but live Deck has no local executor | Hosted intent | Unmeasured | Hosted is likely the appropriate opt-in default after truthful disclosure; local best-effort must never pretend parity |

On **privacy**, local wins categorically because transcript/context bytes do not cross a provider boundary. On **marginal cost**, deterministic local is effectively zero after installation; local models consume the user’s hardware and power; hosted cost depends on confirmed tier and tokens. On **consistency**, deterministic local is stable but limited, while both local and hosted generative models need repeat-run measurement. On **hardware**, deterministic local works broadly; the candidate report restricts larger GGUF models to hardware tiers until working-set and thermal tests pass.

## 6. Target routing policy

The default should be local-first **by capability**, not “local for everything regardless of quality” and not “cloud for every action when a key exists.”

| Task | Default target | Hosted escalation | Required disclosure |
|---|---|---|---|
| Plain punctuation/capitalisation | deterministic local | Only when user disables Instant Text or explicitly requests Rewrite | No text leaves device by default |
| Language/foreign term correction | local deterministic, uncertainty preserved | Explicit hosted shaping when enabled | State languages and whether transcript text is sent |
| Command classification | deterministic local authority | No hosted action classification in the dictation critical path | Low confidence remains text/no action |
| Short Prompt, Email, Reply, Rewrite | benchmark-winning small local model on capable hardware | Hosted when user chooses it or local quality gate fails for the task | Show effective engine per action |
| List formatting | deterministic structure or small local model | Hosted only for semantic reorganization | Distinguish layout from reasoning |
| Multi-document reasoning / deep extraction | best-effort local with honest limitation | Hosted, explicit action | Show which selected source text will leave device |
| Meeting summary/extraction | local candidate after it passes long-context tests | Hosted only through an explicit, route-labelled action | Never let device-only or Pro-off call hosted analysis |

“Auto” may be added later only if it is deterministic, inspectable and user-overridable. It must not silently send a task because a key happens to exist.

## 7. Local candidate interface contract

Use the bounded candidates from [local-ai-candidate-research.md](local-ai-candidate-research.md), not the unresolved placeholder model names currently embedded in pipeline comments:

- **Qwen3 0.6B GGUF Q8** — first small instruction-following candidate.
- **Qwen2.5 1.5B Instruct GGUF Q4_K_M** — incumbent control for the existing Stage 3 concept.
- **IBM Granite 3.3 2B Instruct GGUF Q4_K_M** — quality ceiling for capable hardware.
- deterministic cleanup — mandatory no-model control.

Every model adapter must implement one request shape:

```text
input:  lane, transcript, optional allowed context, style preferences,
        output schema, max input/output budgets, cancellation token,
        immutable route snapshot
output: text or structured object, validation status, latency phases,
        model identity/hash, fallback/degradation reason
```

The adapter must support cancellation, hard timeouts, bounded input, structured validation, model warm/cold telemetry and safe teardown. A model’s runtime licence and model weights/terms must be reviewed independently, with the exact artifact hash recorded in the existing third-party notices process.

Do not activate the current staged pipeline merely because its modules import. On this machine its three model stages were absent; its embedded GRMR model identifier has no approved source in the candidate report; and FullStop’s real weight contradicts the lightweight assumption.

## 8. Required benchmark harness

### Preconditions for hosted measurement

1. In the Cerebras console, prove the test organization is on **Free**, has no Pay as You Go balance that can be consumed, and has no auto-recharge or marketplace billing path.
2. Record the account tier without recording the key or organization identifier.
3. Pin provider model ID, API version, Mumble commit and prompt-template hash.
4. If any billing ambiguity remains, do not call the provider. Use recorded fixtures only.

### Fixed task corpus

Use synthetic, non-personal examples divided before testing:

- 100 plain punctuation/capitalisation utterances;
- 75 Prompt instructions across short/medium/long complexity;
- 75 Emails with factual preservation checks;
- 75 Replies with a separate quoted source and tone constraint;
- 50 Rewrites with meaning-preservation and edit-strength targets;
- 50 Lists with explicit ordering/coverage checks;
- 50 command positives and at least 200 negatives/near misses;
- 20 meeting transcripts at short, medium and long chunk counts, with authoritative actions/decisions/questions and chronological reversals.

Do not tune on the held-out evaluation set.

### Configurations

Run at least:

1. deterministic local control;
2. each admitted local GGUF on CPU and supported accelerator paths;
3. Cerebras `gpt-oss-120b` only after the free-tier gate;
4. network-disabled and forced-provider-failure variants;
5. cold process, warm model, and repeated-session conditions.

### Measurements

| Dimension | Measurement |
|---|---|
| Latency | route decision, context build, model load, time to first output, final output, validation, paste; p50/p95/p99 |
| Quality | blind pairwise preference plus lane-specific factual/structural rubric |
| Consistency | pass rate across five deterministic repeats or fixed seed where supported |
| Memory | cold/warm working set, peak private bytes, model residency and unload |
| Hardware | named CPU/GPU, RAM/VRAM, OS/build, power mode |
| Privacy | exact input categories and byte counts presented to provider adapter |
| Cost | input/output tokens and documented tier; zero is not assumed from key presence |
| Reliability | timeout, invalid output, retry, fallback and cancellation rates |

For user-perceived performance, the primary result is action-to-valid-output, not provider tokens per second.

### Adoption gates

- Plain local remains default unless a candidate improves quality without adding more than 100 ms warm p95 on the minimum supported device.
- A local semantic model may own a lane only if it retains all explicit facts in at least 99% of factual fixtures, meets the lane structure in at least 98%, and stays within the published memory tier.
- Command classification must have zero unsafe activations in the negative set; uncertain cases fail closed.
- Hosted escalation must never occur under device-only, Pro-off, missing/invalid key, unsupported provider, or network-disabled conditions.
- A fallback must preserve the original transcript and disclose degradation without pasting provider errors or internal notices into user prose.

## 9. Settings information architecture and wording

Use the interface-contract hierarchy rather than exposing internal component names:

### Speech to text

```text
Speech to text
On this device — audio stays on this computer
Engine: faster-whisper · Small English · CPU/GPU status

Hosted speech recognition [Advanced]
If enabled, recorded audio clips are sent to [provider].
```

### Text shaping

```text
Text shaping
(•) On this device
( ) Hosted provider

Plain dictation: On this device · Fast · No usage cost
Prompt and Email: [effective engine]
Deck actions: [effective engine]
Meeting analysis: [effective engine]
```

Each feature row should expose **What goes in / Engine / Where it runs / What leaves the device / Speed / Privacy / Cost**, with Advanced details for model IDs and provider settings.

Replace “Pro Mode” with **Hosted text processing**. “Pro” sounds like a quality entitlement, while the real switch is permission to use a configured hosted provider. Replace “local-only mode” with **Keep audio and text on this device** and enforce it as a global deny rule at the provider adapter boundary.

The effective route is the truth surface. If a hosted provider is selected but no key is available, show **On this device — hosted provider not ready**, not the saved intention alone.

## 10. Implementation and validation sequence

This report intentionally makes no product edits. A follow-up implementation should be split into reviewable changes:

1. **Privacy red tests:** at the final provider-call seam, assert zero hosted calls for every feature when device-only is on, Pro is off, the provider is unsupported, or its key is missing. Include Deck and every Meetings action on all platform entry points.
2. **Shared policy:** introduce one pure route resolver and immutable invocation snapshot. Make the provider adapter reject any invocation without an explicit hosted decision.
3. **Deck/Meetings migration:** consume the shared decision, implement a truthful local-unavailable result, and never treat key presence as permission.
4. **Hydration fix:** make Settings loading explicit; remove Basic as an implicit value; show saved/effective/reason; add a delayed-bridge browser test and a failed-bridge test.
5. **Snapshot completion:** freeze provider, key version/key bytes privately, model, endpoint class, language, preferences, context policy and local readiness once per job. Eliminate downstream Settings re-reads.
6. **Cross-platform convergence:** share the policy and hydration helper, then run platform-specific entry-point tests. Do not copy-paste another independent implementation.
7. **Benchmark adapters:** implement the candidate interface behind flags, run the fixed offline corpus, and retain the existing default until a candidate passes gates.
8. **Hosted benchmark:** only after the account-free-tier precondition is recorded.
9. **UI rewrite:** apply the interface-contract names and feature-level disclosure after runtime authority exists.

## 11. Verification performed

### Passing checks

- Browser hydration probe: 5/5 reproduced false Basic first frame and correct Enhanced post-hydration frame.
- Persistent state probe: Enhanced saved; Resource Saver off.
- Duplicate-control audit: exactly one `ui_effects` control in Windows, macOS and Linux.
- Mock provider-call probe: demonstrated Deck and Meetings device-only bypass without network access.
- Settings merge procedural suite: **44/44 passed**.
- Settings route procedural suite: **15/15 passed**.
- Local engine procedural suite: **25/25 passed**.
- Pipeline procedural suite in offline mode: **12/12 available tests passed; 7 model-dependent tests skipped**.
- Voice-command tests: **2 passed**.

### Tooling limitation discovered

Importing `test_ui_bug_regressions.py` through `unittest` failed because this environment’s PIL package could not import `ImageDraw`. This does not affect the browser reproduction, but it means the existing Python UI regression module was not an additional green signal. The durable CI investigation should treat the incomplete PIL environment as dependency/tooling evidence rather than suppressing it.

### No external mutation

- No provider call was made.
- No model was downloaded.
- No settings value was changed.
- No product or port source was changed.
- No issue was closed and no branch was pushed.

## 12. Open uncertainties

1. The user’s real WebView2 bridge delay distribution was not instrumented; the browser loop proves the render race, not its visible duration in every launch.
2. A persistent Basic display could also result from a bridge exception. Windows catches that exception and leaves the false value visible; live app logs are required to distinguish a long delay from a permanent failed hydration in a particular run.
3. No local semantic model is installed, so local Prompt/Email/Reply quality, memory, cold start and cancellation remain unmeasured.
4. Cerebras quality and Mumble end-to-end latency remain unmeasured under the no-billable-call rule.
5. OpenRouter quality/cost cannot be summarized as one value because it depends on the chosen model and account pricing.
6. The voice-command parser’s intended live activation path is unresolved; it is currently dead code outside tests.
7. Meeting `lightweight/deep` naming suggests an engine choice more substantial than the current record label and explicit analysis button. Product language should wait for the runtime contract.

## Final recommendation

Do not “fix” the original issue by forcing the Visual effects control to Enhanced. That would hide one symptom while preserving a false-state architecture.

First make hosted access impossible without one authoritative route decision, including Deck and Meetings. Then hydrate the Settings screen from the same effective snapshot before editable controls appear. Only after those truth and privacy gates exist should Mumble compare Qwen3 0.6B, Qwen2.5 1.5B and Granite 3.3 2B with the hosted lane and move more explicit actions local when measured quality justifies it.

[^cerebras-pricing]: Cerebras Inference, [Pricing](https://inference-docs.cerebras.ai/support/pricing), accessed 24 July 2026.
[^cerebras-limits]: Cerebras Inference, [Rate Limits](https://inference-docs.cerebras.ai/support/rate-limits), accessed 24 July 2026.
[^cerebras-models]: Cerebras Inference, [Supported Models](https://inference-docs.cerebras.ai/models/overview), accessed 24 July 2026.
[^cerebras-console]: Cerebras Inference, [Account and Billing](https://inference-docs.cerebras.ai/console/account-billing), accessed 24 July 2026.
