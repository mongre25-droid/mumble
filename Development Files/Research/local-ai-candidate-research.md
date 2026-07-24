# Local speech and language-processing candidate research

**Decision record:** GitHub issue [#5](https://github.com/mongre25-droid/mumble/issues/5)

**Research date:** 24 July 2026 (revalidated against baseline `0aca0233`)

**Scope:** candidate selection and benchmark design only; no runtime, model, or product behaviour was changed

**Current baseline:** keep Mumble's existing `faster-whisper` path until Mumble-owned measurements prove that another option is better

**Related implementation constraints:** [specification #12](https://github.com/mongre25-droid/mumble/issues/12) requires end-to-end performance and platform evidence; [#14](https://github.com/mongre25-droid/mumble/issues/14) requires one immutable local-or-hosted route; [#16](https://github.com/mongre25-droid/mumble/issues/16) requires stable partials, bounded recovery, and one final insertion; and [#19](https://github.com/mongre25-droid/mumble/issues/19) requires reproducible benchmark, licence, packaging, and fallback gates. This report is planning evidence for those issues, not proof that any acceptance criterion is complete.

## Executive decision

Mumble should benchmark a small set of candidates in separate capability lanes. It should **not** choose one large framework to own the entire speech-and-language pipeline, and it should not replace `faster-whisper` on upstream benchmark claims.

The bounded set is:

| Capability | Control or current baseline | Candidate(s) admitted to measurement | Decision before measurement |
|---|---|---|---|
| Local speech-to-text (STT) | `faster-whisper` 1.2.1 with the current model tiers | Moonshine Small Streaming; sherpa-onnx English streaming Zipformer INT8; NVIDIA Parakeet TDT 0.6B v3 on a strong-computer lane | Keep `faster-whisper` as default |
| Genuine live streaming | Current four-second independent `faster-whisper` chunks | Moonshine Small Streaming; sherpa-onnx streaming Zipformer | Neither may ship until transcript-stability and Windows lifecycle tests pass |
| Voice activity detection (VAD) | Silero VAD through `faster-whisper`; current endpoint behaviour | The VAD/endpoint logic bundled with each admitted streaming runtime | Do not add a second standalone VAD dependency initially |
| Punctuation and capitalisation | ASR output plus Mumble's existing deterministic cleanup | FullStop multilingual large only as an isolated research control | Do not bundle FullStop; its current 2.24 GB weights contradict the old approximately 200 MB assumption |
| Language detection | Whisper's acoustic language result plus the user's selected language | Lingua, restricted to the languages Mumble actually offers | Text-side advisory signal only; never switch a live recognizer silently |
| Meeting diarisation | Current lightweight gap/energy heuristic; current optional pyannote 3.1 seam | pyannote Speaker Diarization Community-1; sherpa-onnx diarisation integration spike | Meeting mode only, never in the dictation critical path |
| Voice-command classification | Current deterministic regular-expression command grammar | Model2Vec Potion Base 8M nearest-example classifier; optionally a Mumble-trained fastText classifier | Fail closed: low-confidence speech remains text and cannot trigger an action |
| Rewriting, email/reply drafting, lightweight instruction following | No local model required; current deterministic paths remain valid | Qwen3 0.6B GGUF Q8; Qwen2.5 1.5B Instruct GGUF Q4_K_M as incumbent control; IBM Granite 3.3 2B Instruct GGUF Q4_K_M as quality lane | Explicit user-invoked modes only; no automatic rewriting of raw dictation |
| Embeddings | No current semantic-embedding implementation | Model2Vec Potion Retrieval 32M; all-MiniLM-L6-v2 ONNX INT8 | Measure retrieval quality and cold-start cost before adding either |
| Retrieval | Current recency-only context seam, which is not semantic retrieval | SQLite FTS5 lexical search first; `sqlite-vec` only as an experimental hybrid-search store | FTS5 is the first implementation candidate; vector search must earn its added complexity |

This set is deliberately asymmetric. Fast and reversible capabilities get small candidates. High-risk capabilities such as command execution retain deterministic controls. Heavy models are restricted to explicit user actions or strong hardware.

## What was verified in Mumble

The following points come from the current branch, not from old plans:

1. [`Internal/app/requirements.txt`](../../Internal/app/requirements.txt) pins `faster-whisper==1.2.1` and `sherpa-onnx==1.13.4`. It does not install Transformers, PyTorch, a local language model runtime, a sentence-transformer runtime, or a vector database.
2. [`Internal/app/mumble.py`](../../Internal/app/mumble.py) sets `STREAM_CHUNK_SECONDS = 4.0`. Its worker transcribes independent accumulated chunks; it does not preserve a decoder state across chunks. That is periodic partial transcription, not genuine streaming decoding.
3. The current local transcription path uses `faster-whisper`, greedy decoding, its VAD filter, optional hotwords, and a warm model/VAD path. This is the baseline that every STT candidate must beat on the same Mumble recordings.
4. [`Internal/app/voice_commands.py`](../../Internal/app/voice_commands.py) uses deterministic phrase matching. That is a valuable safety control, not obsolete code.
5. [`Internal/app/meeting_diarise.py`](../../Internal/app/meeting_diarise.py) has a lightweight energy/gap fallback and an optional pyannote 3.1 path requiring a Hugging Face token.
6. [`Internal/app/context_store.py`](../../Internal/app/context_store.py) exposes a limited recency context seam. It does not implement embeddings, vector search, or retrieval-augmented generation.
7. [`Internal/app/models/registry.py`](../../Internal/app/models/registry.py) and the optional local-model paths in [`Internal/app/branding.py`](../../Internal/app/branding.py) are integration seams and catalogue entries. They do **not** prove that a model is approved, bundled, downloaded, legally reviewed, or quality-tested.
8. [`Internal/app/pipeline/stage_punctuation.py`](../../Internal/app/pipeline/stage_punctuation.py) names FullStop as an optional model, but it is not in the application manifest. The model repository currently exposes a 2,235,440,664-byte `model.safetensors` file, so the source comment's approximately 200 MB memory expectation must not be used for packaging decisions.
9. [`Internal/app/pipeline/stage_grammar.py`](../../Internal/app/pipeline/stage_grammar.py) names a `Mumble/grmr-2b-instruct-GGUF` asset for which this research found no approved official source, model card, or terms. Treat that name as an unresolved placeholder, not a releasable dependency.
10. [`Internal/app/THIRD_PARTY_NOTICES.md`](../../Internal/app/THIRD_PARTY_NOTICES.md) already records that runtime code and downloaded model assets need separate notices and separate term review. That rule remains essential for every candidate below.

The earlier [dictation-latency research](mumble-dictation-latency-research.md) remains the authoritative analysis of the current four-second pseudo-streaming path, stable-prefix alternatives, and the existing faster-whisper/sherpa/whisper.cpp landscape. This report does not repeat that work; it narrows the next local-AI experiments.

## Evidence rules and important limits

- **Upstream result** means a number published by a model or runtime author. It is useful for deciding what to test, but it is not a Mumble result.
- **Mumble result** must be measured end to end, from microphone capture to visible text or completed action, on named Windows hardware and a pinned build.
- Word error rate (WER) results from different datasets, normalisers, quantisations, and endpoint rules are not directly comparable.
- Download size is not the same as working memory. Where no Windows working-set measurement exists, this report gives only a planning range and labels it as an estimate.
- A permissive code licence does not grant rights to every model, dataset, tokenizer, voice, or conversion distributed beside the code.
- A community GGUF or ONNX conversion is another supply-chain artifact. Prefer an official conversion; otherwise reproduce the conversion from an official checkpoint, record the tool version and hash, and preserve all upstream terms.

## Candidate lane 1: speech-to-text and genuine streaming

### Admitted candidates

| Candidate | Why it earns a benchmark | Size and hardware planning | Streaming and language coverage | Windows and integration | Terms and evidence boundary |
|---|---|---|---|---|---|
| **Current `faster-whisper` baseline** | Mature in Mumble, multilingual, hotwords, timestamps, VAD integration, existing packaging and tests | Existing tier-dependent footprint; measure the exact installed models and warm working set | Whisper is multilingual, but Mumble's current four-second worker is not stateful streaming | Already integrated; CPU and CUDA paths already understood | MIT runtime; model and dataset terms remain separate. It stays default until another candidate wins Mumble's gates |
| **Moonshine Small Streaming, English** | It is designed for incremental audio and caches encoder/decoder work. This directly tests the weakness of Mumble's independent four-second chunks | 123 million parameters. The shipped assets are 8-bit `.ort`; exact package bytes and Windows working set must be recorded during the spike. Plan for hundreds, not tens, of MB including ONNX Runtime | Genuine cached streaming. English Small is admitted; Medium (245M) is a stretch comparison if Small nearly passes quality. Other language models are not admitted initially | Official C++ core, ONNX Runtime, Windows sample and downloadable Windows library. Python integration is possible but a small out-of-process prototype gives the cleanest failure boundary | MIT code and English models. Non-English models use a non-commercial Moonshine Community License and are excluded from a commercial/default multilingual plan without legal approval |
| **sherpa-onnx streaming Zipformer INT8, English** | A compact transducer supplies a second genuinely stateful architecture through a dependency Mumble already pins | The selected model's INT8 encoder/decoder/joiner total about 72.7 MB; runtime and working memory are additional | Genuine online transducer with configurable chunk/left context and endpoint rules; selected checkpoint is English | Strong Windows/API coverage and existing Mumble dependency reduce integration cost. The exact provider (CPU, CUDA, DirectML where applicable) must be logged rather than inferred | Apache-2.0 runtime and selected model repository. Pin the model SHA and preserve its model notice |
| **NVIDIA Parakeet TDT 0.6B v3** | Strong punctuation/capitalisation, timestamps, automatic language identification, and 25 European languages make it a useful quality ceiling | 0.6B parameters; official safetensors are 2.51 GB. NeMo/PyTorch adds substantial runtime weight. Plan roughly 4–8 GB working memory/VRAM until measured | The official card describes offline transcription features, not Mumble-style cached live streaming. It belongs in completed-utterance and meeting tests, not the first-visible-text lane | Windows packaging is high risk because the supported NeMo stack is much heavier than Mumble's current runtime. Strong-GPU experimental lane only | CC-BY-4.0 model; NeMo code is Apache-2.0. Attribution must ship. Published scores are upstream and do not prove Mumble dictation quality |

### Moonshine evidence that matters

Moonshine's pinned README reports Open ASR Leaderboard averages of 7.84% WER for Small Streaming and 6.65% for Medium Streaming, with author-measured CPU final-response latencies of 165 ms and 269 ms respectively on its Linux x86 system. Its own documentation correctly warns that:

- those headline accuracy values use floating-point reference models across eight datasets;
- the downloadable `.ort` files are 8-bit quantised and score differently;
- on LibriSpeech `test-clean`, the shipped quantised Small and Medium models are reported at 3.03% and 2.37% WER;
- response latency is measured from VAD endpoint detection to final transcript, not from Mumble's hotkey press to visible paste; and
- its Whisper comparison feeds VAD-segmented phrases to `faster-whisper`, which is useful but is not Mumble's current four-second architecture.

These are promising, reproducible upstream signals. They are not permission to replace the baseline.

### What this means for issue #19

The first benchmark is intentionally small: `faster-whisper` versus Moonshine Small Streaming versus the selected sherpa-onnx Zipformer INT8 model on one English live-dictation harness. Parakeet is a separate strong-hardware, completed-utterance quality ceiling. Qwen3, Qwen2.5, and Granite are a separate explicit-action text suite; they must not be mixed into the STT result. No candidate has Mumble-owned accuracy, latency, memory, failure-recovery, or Windows-packaging evidence at this baseline.

### Candidates not admitted to the first STT matrix

- **whisper.cpp** remains a useful runtime experiment in the existing latency report, but it is the same Whisper model family and would expand the first matrix without testing a new streaming architecture. Add it only if CPU packaging or startup data identifies a clear need.
- **Moonshine multilingual models** are technically interesting but non-English models currently carry a non-commercial licence. Do not build a product plan around them without explicit rights.
- **Parakeet as live streaming** is not admitted because the official v3 model card does not establish stateful low-latency decoding suitable for Mumble's partial transcript UI.
- **TEN VAD** is technically small (the repository contains an approximately 315 KB ONNX model and Windows DLLs), but its licence adds a restriction against competing with Agora offerings. It is not an ordinary Apache-2.0 dependency and is excluded pending legal review.
- **SenseVoice through the current DirectML experiment** remains a prototype lead, not an approved default. Any acceleration claim must be proven by logging the actual ONNX execution provider on target Windows machines.

## Candidate lane 2: VAD, punctuation, capitalisation, and language detection

### Voice activity detection

Keep **Silero VAD** as the control because `faster-whisper` already exposes it, it is MIT-licensed, and upstream reports a model of about 2 MB, support for 8/16 kHz audio and 6,000-plus languages, and processing of a 30 ms-plus chunk in under 1 ms on one CPU thread. These are upstream claims; benchmark Mumble's actual endpoint false-cut and missed-speech rates.

Do not introduce a separate VAD package for the first experiment. Moonshine and sherpa must be tested with their intended endpoint logic because endpointing and decoder latency interact. Measure:

- speech-start delay;
- end-of-speech delay;
- clipped first/last phonemes;
- false activations from typing, fans, television, and calls;
- long-pause splitting; and
- the effect on WER and transcript corrections.

### Punctuation and capitalisation

The safest control is the punctuation already emitted by each ASR model plus Mumble's deterministic cleanup. A standalone post-processor must improve human-rated readability without changing meaning, names, numbers, or commands.

**FullStop multilingual large** is retained only as an isolated comparison because it is already named in Mumble source and is MIT-licensed. It supports English, German, French, and Italian, but its model card describes Europarl political text as a training source, and the current checkpoint is 0.6B parameters with a 2.24 GB safetensors file. That is far too large for an always-on punctuation stage without exceptional measured benefit. It is not a default candidate.

For explicit rewrite/email modes, punctuation and capitalisation should be scored as part of the local language-model output rather than by stacking another 0.6B model in the dictation hot path.

### Language detection

Use three signals with clear authority:

1. the language chosen by the user is authoritative for recognizer selection;
2. the ASR's acoustic language signal may warn about a mismatch; and
3. **Lingua** may classify the final text after transcription and provide an advisory confidence.

Lingua is Apache-2.0, implemented in Rust with Python bindings, supports 75 languages, is designed for short text, and reports only a few dozen MB when all language models are considered. Restricting it to Mumble's offered languages should improve both speed and precision. It must not silently switch the live model on one short phrase; names, loanwords, and code-switching make that unsafe.

The compressed fastText language identifier is only 917 KB and covers 176 languages, but its published `lid.176` model is CC-BY-SA-3.0 even though the fastText code is MIT. Lingua is the cleaner first candidate for Mumble; fastText remains useful only if its ShareAlike implications are explicitly accepted.

## Candidate lane 3: diarisation

Diarisation means estimating **who spoke when**. It should remain a meeting-only capability because it is computationally heavier and has no value in single-speaker push-to-talk dictation.

| Candidate | Role | Benefits | Costs and constraints | Decision |
|---|---|---|---|---|
| Current gap/energy heuristic | Lightweight control | No model download or token; deterministic and fast | It does not identify speakers reliably | Keep as graceful fallback |
| pyannote Speaker Diarization Community-1 | Quality candidate | Official pipeline runs locally after download, provides ordinary and exclusive diarisation, and improves on the old 3.1 seam | Gated model: user must accept conditions and use a Hugging Face token for initial download. Heavy PyTorch dependency and significant meeting-processing cost | Benchmark offline after recording; never block recording or dictation |
| sherpa-onnx diarisation | Packaging/integration spike | Reuses ONNX-oriented cross-platform runtime and documents pyannote-style segmentation plus several speaker embedding options | Multiple component models mean multiple terms, hashes, and accuracy interactions; less turnkey than pyannote | Spike only if pyannote's packaging or token flow fails product requirements |

The pyannote code repository is MIT, while the Community-1 model card is CC-BY-4.0. Both notices matter. A successful first download does not make the model redistributable inside Mumble without checking the gated model's current conditions.

Measure diarisation error rate where labelled data is available, but also score Mumble-visible failures: speaker count, speaker swaps, overlap, short interjections, noisy calls, CPU time, peak memory, and whether the user can correct labels after processing.

## Candidate lane 4: safe command classification

The deterministic parser remains the authority for commands that can move, delete, send, or otherwise change user data. A learned classifier may widen phrasing, but it must not widen authority.

The admitted experiment is **Model2Vec Potion Base 8M** used as a nearest-example classifier over a small, versioned set of Mumble command examples. The official model is MIT-licensed; its current safetensors or ONNX weights are about 30.2 MB. It uses static embeddings, so it should have a much lower cold-start and working-set cost than a transformer.

An optional second control is a fastText classifier trained by Mumble on Mumble-owned labelled phrases. That avoids inheriting the terms of an unrelated pretrained language-ID model, but Windows compilation and packaging may cost more than Model2Vec.

Safety gates:

1. Test a separate **not a command** class containing ordinary dictation that resembles commands.
2. Require a calibrated confidence threshold and an explicit abstain result.
3. Pass the selected intent through the existing deterministic argument parser and permission checks.
4. Never let a language model invent an action name or parameters.
5. Destructive, public, or billable actions still require the product's normal confirmation.
6. Record false-action rate as a release-blocking metric. A useful target is zero false executions in the fixed safety corpus; a statistical rate needs a much larger trial before any claim.

Moonshine Voice includes semantic intent tooling backed by a roughly 300M EmbeddingGemma model, but that is unnecessarily large for the first command-classification experiment and would couple Mumble to a much broader framework. It is excluded from the initial matrix.

## Candidate lane 5: local rewriting and lightweight instruction following

These candidates apply only when the user explicitly requests a rewrite, email, reply, summary, or formatting operation. Raw dictation must remain available and recoverable.

### Bounded model set

| Candidate | Official artifact | Intended role | Size and expected hardware | Language and quality evidence | Decision |
|---|---|---|---|---|---|
| **Qwen3 0.6B GGUF Q8** | Official Apache-2.0 GGUF, 639.4 MB | Fast/weak tier: punctuation, formatting, short rewrite, simple email structure | Plan roughly 1–1.5 GB warm working set plus `llama.cpp`; measure CPU tokens/s and cold start | Model card states 100-plus languages and thinking/non-thinking modes. Use non-thinking mode for bounded transformations. Upstream capability claims do not prove faithful rewriting | Admit as smallest general model |
| **Qwen2.5 1.5B Instruct GGUF Q4_K_M** | Official Apache-2.0 GGUF, 1.117 GB | Incumbent catalogue control | Plan roughly 2–3 GB warm working set; CPU first, optional GPU offload | Multilingual instruction model already named by Mumble's registry, but not currently bundled or approved | Admit as control, not as default |
| **IBM Granite 3.3 2B Instruct GGUF Q4_K_M** | Official Apache-2.0 GGUF, 1.545 GB | Strong-computer quality tier for instruction following and structured replies | Plan roughly 2.5–4.5 GB warm working set; measure CPU/GPU separately | Official model card covers 12 languages and publishes instruction-following evaluations. Those scores are not a Mumble rewrite test | Admit as quality ceiling |

All memory ranges above are planning estimates derived from weight size plus runtime/context overhead. They are not measurements.

Use pinned `llama.cpp` as the common experimental runtime because Mumble already has a CLI seam, it is MIT-licensed, publishes Windows binaries, supports grammar-constrained output, and keeps the prototype out of the main Python process. A shipping decision would still require:

- a pinned binary version and hash;
- Microsoft runtime and GPU-backend checks;
- process startup, cancellation, crash, and timeout handling;
- model hash and exact prompt-template pinning;
- third-party notices for runtime, model, tokenizer, and conversion;
- clear attribution using the upstream project/model names; and
- no suggestion that Mumble created, endorsed, or renamed an upstream model.

**SmolLM2 1.7B Instruct** is a useful later control because its official card includes rewriting examples and publishes ONNX variants, including a roughly 1.41 GB Q4 file. It is not in the first matrix because adding a second runtime format would confound model quality with runtime behaviour; use it only if the three admitted models fail faithful rewriting.

### Task-quality tests

Do not use generic chat scores as the acceptance test. Build a private, consented set covering:

- minimal punctuation/capitalisation with no wording changes;
- removing filler words without deleting meaning;
- concise and formal rewrites;
- email subject plus body;
- reply drafting from a supplied message;
- preserving names, dates, amounts, URLs, code, quoted text, and uncertainty;
- refusing or abstaining when instructions conflict or context is missing;
- English plus each language that Mumble claims for that model; and
- prompt-injection strings embedded in dictated or retrieved text.

Score semantic preservation, required-field accuracy, unsupported additions, format validity, user preference, time to first token, time to complete, tokens per second, cold/warm memory, and cancellation latency. Any automatic use must be rejected; an explicit user action and an undo path are required.

## Candidate lane 6: embeddings and retrieval

Mumble should establish whether ordinary text search is sufficient before introducing a semantic model.

### Retrieval stages

1. **SQLite FTS5 control:** index consented transcript/context text in the existing local SQLite design. FTS5 provides phrase/prefix/Boolean search, BM25 ranking, highlighting, and snippets without a model or network request. This is the first implementation candidate.
2. **Model2Vec Potion Retrieval 32M:** MIT model with approximately 129.2 MB safetensors or ONNX weights. Its card reports much lower retrieval quality than MiniLM but substantially faster inference. Admit it as the low-resource semantic candidate.
3. **all-MiniLM-L6-v2 ONNX INT8:** Apache-2.0, 22.7M parameters, 384-dimensional output, 256-wordpiece truncation, and an official AVX2 quantised ONNX file of about 23.0 MB. Admit it as the semantic quality/control candidate; account separately for tokenizer and ONNX Runtime.
4. **Hybrid store spike:** if semantic retrieval wins, compare an in-process brute-force cosine scan at Mumble's expected small scale against `sqlite-vec`. The latter supports Windows and Python but explicitly labels itself pre-v1 with expected breaking changes, so it is not an initial default.

**BGE-small-en-v1.5** is MIT and a credible 33.4M-parameter English retrieval model, but its 133.5 MB weights and overlapping role would enlarge the first matrix. Add it only if MiniLM narrowly fails retrieval relevance. This is a bounded-test decision, not a judgement that BGE is inferior.

### Retrieval safety and privacy

- Index only data the user has chosen to retain.
- Preserve source identifiers, timestamps, and deletion lineage so removing a source also removes its index entries and vectors.
- Never send local content to obtain embeddings.
- Display the retrieved source beside generated answers or drafts.
- Treat retrieved text as untrusted data, not as instructions to the local language model.
- Evaluate exact deletion, database migration, corruption recovery, encrypted-storage expectations, and multi-user Windows boundaries before release.

## Windows packaging and integration comparison

| Candidate family | New runtime/dependency burden | Packaging risk | Suggested isolation |
|---|---|---|---|
| Moonshine Streaming | Native C/C++ library plus ONNX Runtime and model components | Medium: official Windows sample exists, but native ABI, DLL discovery, model download, and crash handling need work | Separate benchmark executable/process first |
| sherpa streaming/diarisation | Existing Python package, ONNX models, optional native backends | Low-to-medium for STT; medium for multi-model diarisation | Existing backend boundary or separate worker |
| Parakeet | NeMo, PyTorch, CUDA stack, 2.51 GB model | High | Optional strong-GPU research environment only |
| Lingua | Rust-backed Python wheel and language data | Low if a supported Windows wheel exists for Mumble's Python; verify offline install | In-process advisory component |
| pyannote Community-1 | PyTorch, gated download/token, large model graph | High | Meeting-only background worker |
| Model2Vec | Model2Vec/tokenizer packages or ONNX export | Low-to-medium | In-process after cold-start measurement |
| `llama.cpp` GGUF models | Pinned executable/DLLs plus 0.64–1.55 GB model | Medium; GPU backend and antivirus/startup behaviour vary | Long-lived child process with timeout and restart |
| MiniLM/Model2Vec retrieval | ONNX/runtime and tokenizer/model files | Medium unless it reuses an already-pinned ONNX Runtime | Dedicated embedding worker or lazy in-process load |
| SQLite FTS5 | Usually available with SQLite, but Python build options must be verified | Low | Existing local database process/path |
| `sqlite-vec` | Native SQLite extension | Medium while pre-v1 | Prototype database only |

Do not assume that two projects using ONNX Runtime can share one binary safely. Version, execution-provider, DLL, and architecture compatibility must be tested in the packaged application.

## Local versus Cerebras comparison boundary

The local candidates can be benchmarked without sending Mumble data off the device. A Cerebras comparison is a separate, owner-approved hosted experiment, not a free substitute for local evidence:

1. It requires a valid Cerebras account and API credential. The current official pricing page describes a free trial with $5 in credits after account creation and a verified payment method, plus a Developer pay-as-you-go tier; the rate-limit documentation says the exact limits depend on the organisation and tier. Do not infer “free” from the presence of a key.
2. Before any request, the owner must approve the account, billing state, model, synthetic corpus, data boundary, and maximum spend. No personal audio, transcript, meeting content, or retrieved local content may be used.
3. The test must record model ID, prompt-template hash, account tier, input/output tokens, rate limits, retries, time to first output, final output, and total action-to-valid-output latency. Provider token speed is not Mumble activation-to-paste latency.
4. If credentials, billing status, or owner approval are missing or ambiguous, use recorded fixtures only and report Cerebras quality, latency, cost, and reliability as unmeasured. Do not claim a local-versus-Cerebras winner.

This boundary follows #14's route rule: device-only must produce zero hosted calls across dictation, Deck, Meetings, Reader, and future actions. It also follows #19's explicit no-paid-cloud-comparator gate.

## Benchmark plan

### Phase 0 — freeze the controls

Record before any candidate experiment:

- Mumble commit, Python version, package lock/manifest, model names and SHA-256 hashes;
- Windows edition/build, CPU, RAM, GPU, VRAM, driver, power mode, and whether the machine is plugged in;
- recognizer language, beam/greedy settings, VAD settings, chunk/update interval, and warm/cold state;
- microphone, sample rate, audio device, and application build mode; and
- the baseline's exact activation-to-first-text, activation-to-final-text, end-of-speech-to-final-text, and paste-complete timings.

Keep at least three hardware lanes:

1. **Weak:** CPU-only, 8 GB RAM, no assumed discrete GPU.
2. **Typical:** modern laptop CPU, 16 GB RAM, integrated or modest GPU.
3. **Strong:** modern CPU, 32 GB RAM, supported NVIDIA GPU with recorded VRAM.

### Phase 1 — corpus

Use a versioned, consented corpus with:

- clean and noisy rooms;
- short commands, ordinary dictation, long paragraphs, pauses, corrections, and overlapping meeting speech;
- accents and speaking speeds represented by consenting testers;
- names, technical terms, numbers, dates, punctuation, email addresses, and URLs;
- English as the first release gate plus separate per-language sets before any multilingual claim;
- adversarial command-like dictation; and
- fixed rewrite, email, retrieval, and deletion test cases.

Keep raw audio private. Store transcripts, labels, normalisation rules, consent, and deletion procedures with the benchmark, not in the product repository unless approved.

### Phase 2 — metrics by lane

| Lane | Required metrics |
|---|---|
| STT | WER and character error rate before/after normalisation; proper-noun/number accuracy; first-visible, first-stable, final, and paste latency; revision rate; real-time factor; cold/warm startup; CPU/GPU utilisation; peak private working set; download/install size |
| Streaming/VAD | Stable-prefix latency, number of visible corrections, endpoint delay, clipped speech, false starts, long-pause splits, and 30-minute stability |
| Punctuation/language | Punctuation F1 plus human readability; semantic changes; language accuracy/confidence on short text and code-switching; added latency and memory |
| Diarisation | Diarisation error rate, speaker-count error, swaps, overlap handling, processing time, memory, failure recovery |
| Commands | Intent F1, out-of-scope rejection, calibration, false-action count/rate, argument accuracy, latency |
| Local LLM | Semantic preservation, unsupported additions, field/format accuracy, human preference, first-token and completion latency, tokens/s, cold/warm memory, timeout/cancel recovery |
| Retrieval | Recall@5, mean reciprocal rank, answer/source correctness, exact deletion, index/build time, query p50/p95, database/model size, cold/warm memory |

Report medians and p95 latency across repeated cold and warm runs. Preserve raw run data and failures; do not report only the best run.

### Phase 3 — release gates

A candidate advances only if all relevant gates pass:

1. **Quality:** no material regression on the Mumble corpus; task-specific improvements are statistically and practically meaningful.
2. **Responsiveness:** measured end-to-end latency improves the user experience, not merely isolated model inference.
3. **Stability:** no transcript corruption, hangs, duplicate paste, resource leak, or unbounded correction behaviour in long sessions.
4. **Hardware fit:** the intended tier remains responsive within a declared RAM/VRAM/storage budget.
5. **Offline and packaging:** clean Windows install works without development tools; model download is explicit, resumable, hash-verified, and usable offline afterward.
6. **Licence and provenance:** runtime, model, tokenizer, dataset-derived obligations, and conversion are recorded; required notices and attribution are prepared.
7. **Safety/privacy:** command abstention, undo/confirmation boundaries, local data deletion, and untrusted retrieval handling pass.
8. **Fallback:** failure cleanly returns to raw text or the previous local baseline without data loss.

### Minimal experiment order

Run work in this order so later work depends on real evidence:

1. Instrument and freeze the current `faster-whisper` baseline.
2. Compare Moonshine Small Streaming and sherpa Zipformer INT8 on the same English live-dictation harness.
3. Test Parakeet only on the strong lane and only for completed utterances/meetings.
4. Test endpoint/VAD variants for whichever STT candidates survive quality screening.
5. Test Lingua and punctuation controls off the dictation critical path.
6. Test the Model2Vec command classifier in shadow mode, where it records decisions but cannot execute them.
7. Test Qwen3, Qwen2.5, and Granite on the fixed rewrite/email suite through one pinned `llama.cpp` build.
8. Implement/evaluate FTS5 retrieval first; add Model2Vec and MiniLM embeddings only to the same retrieval corpus.
9. Test meeting diarisation last because its packaging and workflow are independent of interactive dictation.

## Licence, attribution, and branding ledger

| Asset | Code terms | Model/data terms | Product implication |
|---|---|---|---|
| Moonshine Voice | MIT except identified third-party components | English STT models MIT; non-English STT models non-commercial Moonshine Community License | English experiment is viable; exclude non-English models from commercial/default packaging pending permission |
| sherpa-onnx + selected Zipformer | Apache-2.0 | Selected HF model card reports Apache-2.0; recheck every downloaded component | Preserve notices and hashes for runtime and model |
| Silero VAD | MIT | Model distributed with project under its terms | Existing control; record the exact embedded/runtime version |
| Parakeet TDT 0.6B v3 | NeMo code Apache-2.0 | CC-BY-4.0 | Attribution required; do not describe as a Mumble model |
| TEN VAD | Apache text plus additional non-compete conditions | Same repository-specific restrictions | Excluded without legal approval |
| Lingua | Apache-2.0 | Bundled language models covered by project distribution; verify notice | Low-risk advisory candidate |
| fastText / `lid.176` | MIT code | CC-BY-SA-3.0 published language-ID model | Do not confuse code and model terms; prefer Lingua initially |
| FullStop | MIT model repository | Card cites Europarl-derived training; preserve required provenance/notice | Research-only due size/domain; do not bundle by default |
| pyannote Community-1 | MIT code | CC-BY-4.0 gated model | Initial token/terms flow plus attribution; redistribution requires explicit check |
| Model2Vec Potion models | MIT code | MIT model cards | Preserve model name/version; still record source datasets from the card |
| all-MiniLM-L6-v2 | Apache-2.0 code/model card | Training-data provenance is described in model card | Pin exact model SHA and tokenizer files |
| Qwen3 / Qwen2.5 | `llama.cpp` MIT | Official model/GGUF Apache-2.0 | Attribute Qwen; Mumble supplies integration, not the model |
| Granite 3.3 | `llama.cpp` MIT | Official model/GGUF Apache-2.0 | Attribute IBM Granite; preserve model card and hash |
| SQLite FTS5 | SQLite public-domain dedication | No model | Lowest notice and supply-chain burden |
| `sqlite-vec` | MIT or Apache-2.0 | No model bundled | Pin version if tested; pre-v1 warning must remain visible in the technical record |

Before shipping any model, capture the exact licence files—not merely a model-card metadata label—and have the owner or counsel decide any ambiguous redistribution, commercial-use, ShareAlike, gated-access, or dataset obligation.

## Final recommendation

Issue #5 should resolve to the following planning decision:

1. **Keep `faster-whisper` as Mumble's local STT baseline.** No researched candidate has Mumble-owned end-to-end evidence yet.
2. **Prioritise one live English benchmark:** `faster-whisper` versus Moonshine Small Streaming versus sherpa Zipformer INT8. This is the shortest path to learning whether true stateful streaming materially improves Mumble.
3. **Use Parakeet only as a strong-hardware completed-utterance quality ceiling.** Do not put its heavy NeMo stack in the default desktop package.
4. **Keep endpointing coupled to each streaming candidate at first; keep Silero as the existing control.** Exclude TEN VAD because of its additional licence restriction.
5. **Keep commands deterministic.** Model2Vec may run in shadow mode to measure natural-language coverage, but it receives no execution authority.
6. **Benchmark local rewriting behind explicit actions** with Qwen3 0.6B, Qwen2.5 1.5B, and Granite 2B through one pinned `llama.cpp` runtime.
7. **Implement retrieval evidence in increasing-complexity order:** SQLite FTS5, then Model2Vec/MiniLM, then a vector-store spike only if semantic relevance justifies it.
8. **Move diarisation out of the dictation path** and compare pyannote Community-1 with the current fallback only in meeting workflows.
9. **Make licence/model provenance a release gate**, with separate code, model, tokenizer, dataset, conversion, attribution, and branding records.

This decision is intentionally reversible. A measured winner can advance into a prototype ticket; every other candidate remains research, not product truth.

## Primary source ledger

Sources were read as of 24 July 2026. GitHub repositories and Hugging Face model cards are pinned below where a commit or model revision was available.

### Speech, streaming, and VAD

- [Moonshine Voice repository and README at `8bf4526`](https://github.com/moonshine-ai/moonshine/blob/8bf452607bcff0cb2c7dbb568a77a631cc7e22d3/README.md) — streaming architecture, benchmark definitions, Windows integration, quantised accuracy, languages, and licence split.
- [Moonshine Voice licence at `8bf4526`](https://github.com/moonshine-ai/moonshine/blob/8bf452607bcff0cb2c7dbb568a77a631cc7e22d3/LICENSE) — code licence text; README separately states English/non-English model terms.
- [Moonshine v2 paper](https://arxiv.org/abs/2602.12241) — model architecture and upstream evaluation context.
- [sherpa-onnx repository at `755a78d`](https://github.com/k2-fsa/sherpa-onnx/tree/755a78d94a62390dcd12630febf6057cdd31531f) — cross-platform runtime and licence.
- [sherpa-onnx online Zipformer documentation](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html) — online model configuration and endpoint examples.
- [Selected English streaming Zipformer model at `672fbf1`](https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26/tree/672fbf1b30579d6585301139bb363f42a0ad4a24) — exact INT8/float artifacts and Apache-2.0 metadata.
- [NVIDIA Parakeet TDT 0.6B v3 model card at `7c35754`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/blob/7c35754d166cca382ad1e53e68b01e7c575f3a1d/README.md) — languages, timestamps, punctuation/capitalisation, evaluation, NeMo use, and CC-BY-4.0 terms.
- [NVIDIA Parakeet TDT 0.6B v3 files at `7c35754`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/tree/7c35754d166cca382ad1e53e68b01e7c575f3a1d) — 2.51 GB safetensors artifact.
- [Silero VAD repository at `76e3dc4`](https://github.com/snakers4/silero-vad/tree/76e3dc408eb2a5c655c34e230d2d5459b4439daa) — runtime, size/performance claims, platform support, and MIT licence.
- [TEN VAD licence at `22a3bcd`](https://github.com/TEN-framework/ten-vad/blob/22a3bcd4509d0faaa8eef4881e8af5f39c178950/LICENSE) — additional non-compete conditions that cause exclusion.

### Text processing, language, commands, and diarisation

- [FullStop multilingual large model card at `345e80a`](https://huggingface.co/oliverguhr/fullstop-punctuation-multilang-large/blob/345e80adc07e761d3a35feafd20f2f44a151f453/README.md) and [files](https://huggingface.co/oliverguhr/fullstop-punctuation-multilang-large/tree/345e80adc07e761d3a35feafd20f2f44a151f453) — supported languages, training domain, evaluation and artifact size.
- [Lingua Python at `754ce21`](https://github.com/pemistahl/lingua-py/tree/754ce21122c083a7200763015fdaf7cda8d85453) — short-text approach, language list, Rust-backed implementation, memory statements, and Apache-2.0 licence.
- [fastText language-identification documentation](https://fasttext.cc/docs/en/language-identification.html) — 176-language model sizes and CC-BY-SA-3.0 model terms.
- [fastText code at `1142dc4`](https://github.com/facebookresearch/fastText/tree/1142dc4c4ecbc19cc16eee5cdd28472e689267e6) — MIT runtime and supervised-classifier implementation.
- [pyannote-audio at `b749285`](https://github.com/pyannote/pyannote-audio/tree/b749285c5cdd4636b2edc7f766f1352c8dde9369) — runtime and MIT code licence.
- [pyannote Speaker Diarization Community-1 model card](https://huggingface.co/pyannote/speaker-diarization-community-1/blob/main/README.md) — local/offline use after gated download, exclusive diarisation, token flow, and model terms.
- [sherpa-onnx speaker diarisation documentation](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html) — component architecture and supported APIs.
- [Model2Vec repository at `add2daf`](https://github.com/MinishLab/model2vec/tree/add2daf2839d7874ea337e4e84c40ca0e54bbc49) — static-embedding runtime and MIT licence.
- [Potion Base 8M model card and files at `bf8b056`](https://huggingface.co/minishlab/potion-base-8M/tree/bf8b056651a2c21b8d2565580b8569da283cab23) — model terms and approximately 30.2 MB artifacts.

### Local instruction models

- [`llama.cpp` at `0a50d99`](https://github.com/ggml-org/llama.cpp/tree/0a50d9909a3478e82679f505bf8595d1eee4b0a8) — Windows runtime, GGUF support, grammar constraints, and MIT licence.
- [Official Qwen3 0.6B model card](https://huggingface.co/Qwen/Qwen3-0.6B/blob/main/README.md) and [official GGUF at `23749fe`](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/tree/23749fefcc72300e3a2ad315e1317431b06b590a) — languages, inference modes, Apache-2.0 terms, and 639.4 MB Q8 artifact.
- [Official Qwen2.5 1.5B Instruct GGUF at `91cad51`](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/tree/91cad51170dc346986eccefdc2dd33a9da36ead9) — Apache-2.0 terms and 1.117 GB Q4_K_M artifact.
- [IBM Granite 3.3 2B Instruct model card](https://huggingface.co/ibm-granite/granite-3.3-2b-instruct/blob/main/README.md) and [official GGUF at `7cdf86c`](https://huggingface.co/ibm-granite/granite-3.3-2b-instruct-GGUF/tree/7cdf86ccd1f1bb3491c9b7017b033f2e51367397) — languages, evaluation, Apache-2.0 terms, and 1.545 GB Q4_K_M artifact.
- [SmolLM2 1.7B Instruct at `31b70e2`](https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct/tree/31b70e2e869a7173562077fd711b654946d38674) — rewriting evidence, Apache-2.0 terms, and official ONNX artifacts.

### Embeddings and retrieval

- [SQLite FTS5 documentation](https://www.sqlite.org/fts5.html) — query syntax, BM25, highlighting, and snippets.
- [Potion Retrieval 32M model card and files at `6fc8051`](https://huggingface.co/minishlab/potion-retrieval-32M/tree/6fc8051fab2a1e0ee76689cf08c853792ac285e7) — MIT terms, upstream retrieval results, and approximately 129.2 MB artifacts.
- [all-MiniLM-L6-v2 model card and files at `1110a24`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41) — dimensions, truncation, training provenance, Apache-2.0 terms, and ONNX artifacts.
- [Sentence Transformers at `d407492`](https://github.com/UKPLab/sentence-transformers/tree/d40749229c2518328335dda01084562691052f22) — reference runtime and Apache-2.0 licence.
- [BGE small English v1.5 at `5c38ec7`](https://huggingface.co/BAAI/bge-small-en-v1.5/tree/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a) — deferred comparison, metrics, MIT terms, and artifact size.
- [`sqlite-vec` at `04d28bd`](https://github.com/asg017/sqlite-vec/tree/04d28bd21773981e2d266bbf6aa4efbd011eb4f6) — Windows/Python support, dual MIT/Apache licensing, and explicit pre-v1 warning.
