# Mumble Local Dictation Latency Research

**Research date:** 23–24 July 2026
**Scope:** Local-first dictation duration, segmentation, crash recovery, activation-to-paste responsiveness, and cold/warm behaviour
**Status:** Deep source-code inspection plus the Wayfinder duration/latency decision; research and planning only, not product implementation

## Executive conclusion

Mumble's reported “car revving up” behaviour has a strong explanation in the current application architecture, but it should not yet be attributed to one hardware effect.

The clearest verified cause is Mumble's current four-second pseudo-streaming boundary. Plain dictation is decoded in independent four-second pieces while the user is still speaking, but those results are only stored internally. A short dictation that ends before the first boundary receives no overlap benefit: its complete transcription begins after the user stops. A longer dictation can have most of its audio decoded in the background and therefore needs only its final tail decoded after Stop. This alone can make sustained speech feel much faster than short or infrequent speech.

Other cold-path costs remain credible: microphone ownership and stream creation, lazy decoder/VAD/runtime initialization, CUDA module loading, GPU/CPU power-state changes, memory allocation, model fallback, post-processing, persistence, clipboard work, and application scheduling. They are **possible contributors, not established causes**, because Mumble does not yet measure the full activation-to-visible-text path.

The highest-confidence direction is therefore:

1. describe the **current 0.95 ten-minute foreground limit** truthfully and direct longer recording to Meetings for now;
2. treat that cap as a limitation of current policy and architecture, not a fundamental product ceiling: the target design keeps one logical dictation running across bounded capture, decoder, and durable-recovery segments until the user stops;
3. replace independent four-second chunks with a persistent streaming session that produces stable partial text and finalizes only the uncommitted tail;
4. keep the selected local model, VAD, and exact inference path genuinely warm within an explicit memory/power policy;
5. retain `faster-whisper` as the safe CPU/NVIDIA baseline while benchmarking `sherpa-onnx` for true streaming and `whisper.cpp` for broader native acceleration;
6. show live partials in Mumble's Island first, but keep insertion into the user's application final-only until revision behaviour is proven safe; and
7. measure the whole activation-to-paste path with an opt-in, local, content-free trace before making a speed claim.

This report deliberately builds on [Mumble Optimisation Research - 2026-07-10.html](../Archive/Reports/Mumble%20Optimisation%20Research%20-%202026-07-10.html). That earlier study remains the broad performance audit. It identified routing divergence, unused platform adapters, fixed-window streaming, thread oversubscription risk, the lack of an authoritative Stop-to-Paste measure, duration-aware scheduling, and likely backend candidates. This document preserves those findings and narrows the question to **why latency changes with usage pattern and how to make the first spoken words feel immediately responsive**.

## Evidence labels

- **Verified in Mumble:** directly present in the current repository code.
- **Verified in inspected source:** observed directly in executable source at the named commit. These are the claims this deepened pass treats as strongest.
- **Documentation-only:** stated by maintainers in a README, benchmark, model card, or documentation, but not demonstrated by the inspected execution path. It may still be useful, but is not treated as proof that the application actually does it.
- **Inference:** a reasonable explanation supported by verified facts, but not yet demonstrated on Mumble hardware.
- **Proposal:** a design choice or target that must be validated before it becomes a product promise.

### Inspection baseline and limits

This expanded pass inspected Mumble at repository commit [`cde23725e0d9eaeffe218e02c225ff5f8a499312`](https://github.com/mongre25-droid/mumble/tree/cde23725e0d9eaeffe218e02c225ff5f8a499312). The working copy also contained one unrelated, uncommitted startup-cleanup call below the dictation path; it does not change the capture, decoding, formatting, or paste findings in this report. Upstream claims below are pinned to the exact revisions listed in the [source-code inspection appendix](#source-code-inspection-appendix).

This was source inspection, not a claim that each upstream project was built and benchmarked on Mumble's reference hardware. Code can establish architecture, defaults, and failure paths; only the proposed benchmark can establish latency, accuracy, power, and packaging quality on a supported machine.

### 24 July 2026 current-checkout revalidation

The Wayfinder decision pass revalidated the repository at [`6f12ed73fd9350692eab8c55b705f9910aae7e77`](https://github.com/mongre25-droid/mumble/tree/6f12ed73fd9350692eab8c55b705f9910aae7e77), which was also `origin/main` when inspected. The earlier `cde23725` source profiles remain useful pinned upstream evidence, but the following facts supersede their description of Mumble itself:

- **Fact — duration is already capped:** Windows defines normal dictation as 600 seconds and trims the final callback to the exact remaining sample count before stopping from a worker thread ([`recording_limits.py`](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/recording_limits.py#L18-L36), [`mumble.py`](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/mumble.py#L1085-L1157)). The macOS and Linux ports carry the same constants and callback cap ([macOS limit](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/Ports/macOS/app/recording_limits.py#L18-L36), [Linux limit](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/Ports/Linux/app/recording_limits.py#L18-L36)).
- **Fact — the cap is visible in-app, not on the marketing homepage:** all three Home surfaces say “Normal dictation records for up to 10 minutes at a time” ([Windows](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/webui/index.html#L323-L329), [macOS](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/Ports/macOS/app/webui/index.html#L321-L327), [Linux](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/Ports/Linux/app/webui/index.html#L330-L336)). No equivalent ten-minute wording was found in the Astro marketing homepage.
- **Fact — memory is bounded only by the hard cap:** every platform still appends float32 callback blocks to `frames`, concatenates the complete recording at Stop, and only then clears the list ([Windows capture/Stop](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/mumble.py#L1085-L1136), [Windows Stop](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/mumble.py#L1906-L2005), [macOS](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/Ports/macOS/app/mumble_mac.py#L499-L657), [Linux](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/Ports/Linux/app/mumble_linux.py#L680-L811)). Ten minutes of 16 kHz mono PCM16 is about 19.2 MB, but the current float32 sample payload alone is about 38.4 MB before callback-array and final-concatenation overhead. This size calculation is arithmetic from the verified format, not a measured peak-RSS result.
- **Fact — four-second pseudo-streaming remains:** Windows, macOS, and Linux still set `STREAM_CHUNK_SECONDS = 4.0`; each worker independently transcribes enough newly accumulated blocks for the threshold, appends final text internally, and exposes no retained acoustic/decoder state ([Windows](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/mumble.py#L116-L116), [Windows worker](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/mumble.py#L1159-L1243), [macOS worker](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/Ports/macOS/app/mumble_mac.py#L586-L657), [Linux worker](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/Ports/Linux/app/mumble_linux.py#L746-L811)). The Windows trace calls these results `stable_chunks_only`; “stable” here means the whole independent chunk will no longer revise, not that a stable-prefix streaming algorithm produced it ([trace context](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/mumble.py#L310-L339)).
- **Fact — only clean shutdown has a foreground recovery attempt:** `_quit` concatenates whatever remains in memory, tries a synchronous transcription, and adds text to History. A process crash, power loss, forced termination, decoder crash, or storage failure before that path has no durable foreground-audio recovery record ([Windows shutdown](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/mumble.py#L6081-L6111), [macOS shutdown](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/Ports/macOS/app/mumble_mac.py#L4055-L4080), [Linux shutdown](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/Ports/Linux/app/mumble_linux.py#L5005-L5059)).
- **Fact — final history is atomic but not a live journal:** the completed transcript is saved before paste through a temporary-file replacement, but the JSON writer does not flush and `fsync` before replacement; its secondary plain-text append is best-effort ([`History.add`](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/history.py#L114-L142), [`History._save_json`](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/history.py#L393-L417)). This is not an incremental dictation recovery protocol.
- **Fact — the opt-in trace is a useful privacy baseline:** Windows tracing uses an event allowlist and metadata allowlist, rejects transcript/audio/free-text fields, writes completed local JSONL records with `flush` plus `fsync`, rotates at a byte cap, and exports through an atomic temporary replacement ([`dictation_trace.py`](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/dictation_trace.py#L21-L98), [session and sink](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Internal/app/dictation_trace.py#L114-L255)). It is absent from the macOS and Linux controllers in this checkout, and the Windows event named `island_render` is recorded after scheduling/setting state rather than after a renderer acknowledgement, so it does not yet prove pixels were painted.
- **Unmeasured:** no physical-device run in this task established peak memory, crash-loss window, first stable partial, target-application paint, cold/warm distributions, battery use, or platform parity. The source facts above define what must be measured; they are not performance results.

## Wayfinder decision: duration, segmentation, recovery, and latency contract

This section resolves the planning question. Statements labelled **Decision** are the contract to carry into a later specification. They describe intended behaviour; they are not claims about the current product unless a separate **Fact** says so.

### Current ten-minute limit, target continuation, and the meaning of “segment”

**Decision — current 0.95 truth:** Normal foreground dictation currently stops at ten minutes. At 10:00 the present product must stop accepting new samples, preserve the exact captured prefix, tell the user why it stopped, finish transcription, save the result, and paste once. Current documentation and the homepage must say that longer recording should use Meetings. This is a description of shipped behaviour, not the destination architecture.

**Decision — target architecture:** Remove ten minutes as the normal technical product ceiling. One foreground dictation continues across bounded recovery and recognition segments until the user stops. A visible, configurable safety guard must protect against accidental indefinite capture, and hard storage/backpressure limits must prevent resource exhaustion. The guard’s enabled state, default duration, warning cadence, maximum configurable value, and battery policy require measurements and explicit owner approval; this report deliberately does **not** invent them. If a configured guard or hard capacity limit is reached, Mumble must warn visibly, preserve the exact captured prefix, stop safely, and still produce one recoverable logical result.

The present ten-minute policy bounds accidental microphone capture and current memory growth; it is not a streaming chunk size and is not fundamental to local transcription. Ten minutes of the current 16 kHz mono PCM16 representation also fits below the present 25 MB direct-upload ceiling of Groq’s free tier and the legacy OpenAI `whisper-1` route ([Groq Speech-to-Text limits](https://console.groq.com/docs/speech-to-text), [OpenAI Audio API FAQ](https://help.openai.com/en/articles/7031512-whisper-audio-api-faq)). That provider fact must not become a user-duration ceiling: cloud transcription should split the same logical dictation into provider-valid overlapping requests, merge them under the shared sample/timestamp contract, and validate the selected route at request time because quotas can change.

Use four different terms in the implementation specification:

1. **Logical dictation:** the one user activation through the user’s Stop (or a visible configured/capacity safety stop), spanning as many bounded internal segments as needed, with one history result and at most one target-app paste.
2. **Capture block:** the small callback-owned PCM block; enqueue/copy only, with no disk I/O or model work inside the real-time callback.
3. **Recognition window:** the bounded audio/context region presented to a decoder. For the first faster-whisper experiment, use a one-second scheduling cadence, a rolling window capped at 15 seconds, and two-hypothesis stable-prefix confirmation; these values are experiment defaults, not product promises. The pinned Whisper-Streaming implementation likewise retranscribes a bounded buffer, defaults to a one-second minimum chunk, trims after the buffer exceeds 15 seconds, and commits only agreeing word prefixes ([online processor](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L426-L575), [hypothesis buffer](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L359-L417)).
4. **Recovery segment:** an immutable local PCM16 file covering at most 30 seconds, plus one small manifest record. Thirty seconds is a proposed internal engineering bound chosen to limit rewrite/recovery work; it must be fault-injection tested and may change without changing the logical-dictation duration contract.

**Rejected:** treating the existing four-second independent decode as the long-term segment contract. It has no overlap, state, stable-prefix reconciliation, incremental display, or crash durability. Groq’s official larger-file guidance itself recommends overlapping chunks and overlap-aware recombination rather than blind disjoint append ([Groq Speech-to-Text, “Working with Larger Audio Files”](https://console.groq.com/docs/speech-to-text)).

### Bounded memory and incremental persistence

**Decision:** One capture owner feeds two bounded consumers:

- a recognition queue capped by **audio age**, not merely item count; and
- a local recovery writer that converts float32 callbacks to PCM16 and seals immutable segments.

The capture callback must never wait on inference or storage. If the recovery queue reaches its small bound, the session enters an explicit `recovery_degraded` state and tells the user that crash recovery is unavailable; it must not silently discard live audio or pretend the session is fully protected. If recognition falls behind, preserve the durable audio, stop publishing stale partials, and let a bounded catch-up or final batch path recover—never drop uncommitted samples.

Only the active recognition window, a short preroll/overlap, the current unsealed recovery segment, and queue bounds remain in RAM. Already sealed PCM and committed transcript prefixes leave the hot path. This makes memory approximately constant with logical-dictation duration rather than relying on a ten-minute stop to cap growth. Disk use still grows with duration, so the later specification must set a measured, visible local-storage budget and minimum-free-space rule rather than calling segmented storage “unlimited.”

The recovery directory is per-user and local. Windows should use Mumble’s existing Local App Data location; Microsoft describes `FOLDERID_LocalAppData` as the place for machine-specific application data such as local performance state ([Microsoft Windows app data guidance](https://learn.microsoft.com/en-us/windows/apps/develop/windows-app-restore#machine-specific-app-data)). macOS should resolve the user Application Support directory rather than hard-code it ([Apple `applicationSupportDirectory`](https://developer.apple.com/documentation/foundation/url/applicationsupportdirectory)). Linux should use `$XDG_STATE_HOME` for restart-recovery state, creating a missing directory with user-only permissions as the XDG specification directs ([XDG Base Directory Specification 0.8](https://specifications.freedesktop.org/basedir/)).

**Decision:** seal a recovery segment using `write temporary → flush → fsync file → atomic replace within the same directory → fsync directory where supported`, then append/update the content-free manifest. Python documents `os.replace` as the cross-platform overwrite operation and POSIX rename as atomic when successful on one filesystem; Windows’ `ReplaceFile` API performs replacement as one operation but has platform-specific access/ACL failure modes ([Python `os.replace`/`os.fsync`](https://docs.python.org/3/library/os.html#os.replace), [Microsoft `ReplaceFile`](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilea)). Therefore the implementation must test real crash points on NTFS, APFS, and the supported Linux filesystems; “atomic API call” must not be inflated into an unmeasured whole-session durability guarantee.

The manifest may contain only session ID, schema version, platform/build, sample rate/channels/encoding, monotonically increasing segment number, sample start/end, byte count, checksum, state (`writing`, `sealed`, `transcribed`, `committed`, `complete`), and timestamps. It must not contain audio-derived text unless the user’s normal History policy already permits saving that transcript.

### Merge, finalization, and one-paste ownership

**Decision:** merge by sample/timestamp identity, not by concatenating strings:

1. Assign every decoder hypothesis a source sample range and monotonically increasing revision number.
2. Keep two surfaces: `committed_text`, which is append-only inside one session, and `tentative_text`, which may be replaced.
3. Commit only the longest word prefix confirmed by two consecutive overlapping hypotheses for the faster-whisper experiment. At a recovery-segment seam, deduplicate words only when normalized text and timestamp overlap agree; otherwise keep the boundary tentative and include more overlap in the next decode. This follows the pinned LocalAgreement and timestamp-aware boundary logic rather than assuming the same word count means the same audio ([`HypothesisBuffer.insert`/`flush`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L359-L417)).
4. On Stop, drain only work already claimed, decode from the last committed sample through the exact captured end, flush tentative text, then run formatting once over the complete logical dictation.
5. Save one final History entry durably before insertion. Insert into the target application once, using the existing clipboard-preserving final-paste path. Island partials are feedback, not target-app edits.

**Decision:** if recognition is slower than real time, never keep an unbounded audio or websocket queue. Mark partials `paused_catching_up`, continue durable segmented capture while the configured storage/backpressure contract remains healthy, and finish from sealed audio. Apply backpressure by suspending live partial work before risking capture loss. If durable capture cannot continue within the measured storage/free-space bound, warn and stop safely rather than silently dropping samples. The application may trade first-visible latency for correctness; it may not trade away samples without an explicit failed-session outcome.

### Crash recovery semantics

**Decision:** recovery is local, bounded, explicit, and idempotent:

- On normal successful save, delete recovery audio only after the final History record is durable; cleanup failure leaves an orphan eligible for the next cleanup pass, never a second history entry.
- On user Cancel, close capture and delete the recovery set. If deletion fails, show a local cleanup warning and retry next launch.
- On restart after a crash, validate manifest schema, contiguous sample ranges, file sizes, and checksums. Ignore/delete an incomplete final temporary file; never guess missing audio.
- Offer **Recover and transcribe**, **Keep for later**, or **Delete**. Do not auto-paste recovered text because focus and cursor ownership are gone.
- Use a stable session ID in History so retrying recovery cannot create duplicate entries. Mark the recovery set `complete` only after History’s durable commit, then delete it.
- Auto-delete abandoned recovery audio after a proposed seven days, with the retention period visible in Settings and an immediate “Delete recovery audio” action. Seven days is a proposal requiring owner/privacy review, not a current promise.
- A valid manifest prefix is recoverable even if the last segment is absent; the UI must state the exact recovered duration and that the ending is incomplete.

**Privacy consequence:** the current homepage statement “Short dictation buffers are discarded after transcription” can remain true only if recovery audio is described as temporary local recording data and deletion occurs after successful transcription/history save. “No telemetry” can coexist with an opt-in local diagnostics file, but “no usage tracking of any kind” is too absolute once duration/timing/resource traces exist—even locally and disabled by default ([current `LocalAI.astro`](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Development%20Files/Marketing/Website/src/components/LocalAI.astro#L27-L49), [current `Privacy.astro`](https://github.com/mongre25-droid/mumble/blob/6f12ed73fd9350692eab8c55b705f9910aae7e77/Development%20Files/Marketing/Website/src/components/Privacy.astro#L55-L66)).

### Privacy-safe activation-to-paste spans

**Decision:** tracing stays disabled by default, local-only, bounded, user-exported, and content-free. No event or attribute may store audio, transcript text, partial text, clipboard content, prompt/context, file path, device display name, provider response, exception message, or a stable machine/user identifier. Use per-session random IDs and coarse hardware classes in ordinary traces; exact machine details belong only in a separately consented benchmark export.

Required monotonic spans and boundaries:

| Span | Start | End | Current coverage | Required correction |
|---|---|---|---|---|
| Activation → feedback visible | hotkey/command callback entry | renderer acknowledgement after paint | Windows records activation and a state call | Add a real acknowledgement; do not label a queued call “visible” |
| Activation → capture | activation | first audio callback | Windows covered | Add open/start subspans, actual stream format, overflow flag, and route-safe error class |
| First speech → first tentative/stable partial | local VAD speech-start frame | distinct tentative and committed emissions | No speech-start event; four-second completed chunk only | Add speech onset and true partial contract/version |
| Partial ready → Island visible | controller emission | render acknowledgement | Not covered | Add revision ID and acknowledgement without words |
| Stop/end speech → final raw | user Stop and VAD endpoint separately | final decoder text available | Stop → final covered | Preserve both origins so endpoint delay is not mistaken for inference |
| Final raw → durable History | raw ready | file commit acknowledged | Broad persistence span covered | Split formatting, recovery finalize, History commit, and optional stats |
| Durable History → paste sent | durable commit | input event sent | Covered | Keep target paint as `unknown` unless an owned test harness acknowledges it |
| Paste sent → target acknowledged | input event sent | synthetic/owned target reports inserted text | Not generally observable | Benchmark-only; never claim this from Ctrl+V dispatch in arbitrary apps |

The trace should preserve event order and numeric durations while constraining fields with the existing allowlist approach. Errors use a short class/code, never `str(exception)`. Rotation, export, and delete must be reachable in Settings before tracing can become a supported diagnostic feature.

### Cold/warm classifications

**Decision:** classify state from facts the process can record; never infer “GPU warm” merely from app uptime:

| Classification | Required evidence | Notes |
|---|---|---|
| `process_first` | first activation ordinal for this process | Separate from machine reboot |
| `model_absent` | no selected model object before activation | Includes cloud mode awaiting local fallback |
| `model_loaded_unwarmed` | model loaded; exact-path warm-up generation absent/failed | Match model hash, backend, device, compute type, VAD/options |
| `model_exact_warm` | exact signature warm-up completed successfully | Warm-up is evidence of execution, not a latency guarantee |
| `audio_first_open` | selected device has not produced a callback in this process generation | Reset on device change and resume |
| `audio_reused` | same selected device generation produced a recent successful callback | Record time since close; do not imply the OS driver stayed warm |
| `post_resume` | operating-system resume generation changed | First success after resume gets its own class |
| `recent_inference` | same exact model/backend signature and seconds since last successful decode | Report raw seconds plus bins: `<30`, `30–300`, `300–1800`, `>1800` |
| `sequence_ordinal` | 1st, 2nd, 5th, 20th controlled trial | Benchmark label, not automatic product causality |
| `machine_cold` | benchmark harness records a controlled reboot protocol | Cannot be established reliably by Mumble alone |
| `cloud_remote_state_unknown` | cloud route selected | Mumble can measure local preparation/network/request spans, not provider internals |

NVIDIA’s lazy-loading documentation remains evidence that first-use CUDA work can exist, and Windows power/QoS documentation remains evidence that scheduling/power state can vary; neither proves the model, GPU, or power plan causes Mumble’s observed delay ([CUDA lazy loading](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/lazy-loading.html), [Windows processor policy](https://learn.microsoft.com/en-us/windows/win32/power/processor-performance-control-policy-constants), [Windows QoS](https://learn.microsoft.com/en-us/windows/win32/procthread/quality-of-service)). The four-second scheduler is verified; hardware warm-up remains a hypothesis until the above classifications produce distributions on named machines.

### Homepage wording consequence

**Decision:** after continuation across recovery/recognition segments is implemented and verified, remove ten minutes as a selling claim. Use bounded wording such as:

> **Keep dictating until you stop.** Mumble transcribes locally by default, continues safely across small local segments, shows progress while you speak, and saves your finished text before pasting. A visible safety guard protects against accidental ongoing recording. During recording Mumble may keep a temporary recovery copy on this device; that audio is deleted after the dictation is safely saved or when you cancel. Cloud Transcription sends audio only when you explicitly choose it.

This future wording must not ship until physical continuation, recovery, memory, storage, accuracy, and cross-platform tests pass. It also must not say “unlimited”: the configurable safety guard and storage/backpressure limits are real boundaries even though ten minutes is no longer the ordinary ceiling.

Until implementation, the public homepage should **not** claim continuation, live partials, crash recovery, bounded memory, or automatic recovery. The current truthful short form is:

> **Mumble 0.95 currently records normal dictation for up to 10 minutes at a time; use Meetings for longer recording. Local Transcription keeps audio on this device; Cloud Transcription sends the clip only when you explicitly choose it.**

Replace the absolute “No telemetry, no usage tracking of any kind” with:

> **No accounts and no remote analytics. Optional diagnostics stay on this device unless you choose to export them.**

That wording distinguishes remote collection from local, opt-in performance evidence and preserves the existing local-first promise.

### Platform implications and release gates

- **Shared contract:** duration constants, manifest schema, segment merger, trace vocabulary, and fault-injection fixtures must live in shared modules. Controllers own only capture, microphone permission/indicator, paste, paths, and lifecycle seams. Copying the contract into three controllers invites drift.
- **Windows:** keep the current exact-sample cap and clipboard-safe final paste. Validate atomic replacement, ACL preservation, long-path behaviour, sleep/resume, device loss, and crash points on NTFS. Do not claim target-visible text from `SendInput`/Ctrl+V alone.
- **macOS:** store recovery data in Application Support, request microphone access with `NSMicrophoneUsageDescription`, and ensure any keep-open microphone experiment matches the operating system’s visible recording indicator and user expectation ([Apple microphone usage description](https://developer.apple.com/documentation/bundleresources/information-property-list/nsmicrophoneusagedescription), [Apple recording-indicator guidance](https://developer.apple.com/videos/play/wwdc2021/10085/?time=1439)).
- **Linux:** place restart state under `$XDG_STATE_HOME`, enforce user-only directory/file permissions, and test native PipeWire/PulseAudio plus sandboxed packaging separately. Filesystem rename/directory-flush behaviour must be tested on supported distributions rather than assumed from one development machine.
- **Parity gate:** preserve a regression test for the current 0.95 exact ten-minute stop while it ships; separately prove that the target architecture continues one logical dictation across the old ten-minute boundary and many internal segment boundaries. Run configured-safety-stop, storage-exhaustion, bounded-queue, overlap merge, crash-at-every-transition, recovery-idempotence, trace-redaction, sleep/resume, and final-only-paste tests on all three platforms. A green Windows unit test is not evidence of macOS/Linux runtime parity.
- **Performance gate:** publish p50/p90/p95/max for the defined spans and cold/warm states on named weak, average, and strong machines; pair every latency result with seam errors, WER/term errors, clipped endpoints, revisions, RAM/VRAM, idle CPU, battery/power, and recovery success.

### Decision status

- **Verified fact:** the current cap, in-memory float32 accumulation, four-second independent chunks, current final persistence, Windows trace schema, and cross-platform source gaps described above.
- **Decision/proposal:** truthful current ten-minute wording; removal of ten minutes as the target logical-dictation ceiling; continuation until user Stop across bounded segments; a visible configurable safety guard whose default remains undecided; measured storage/backpressure limits; the one-second/15-second recognition experiment; 30-second immutable recovery segments; merge rules; seven-day recovery retention; trace extensions; cold/warm taxonomy; and staged homepage wording.
- **Hypothesis:** retained exact-path state, smaller stable-prefix windows, audio reuse, or another backend will improve specific latency percentiles.
- **Unmeasured:** actual peak memory, recovery loss window, latency distributions, target paint, accuracy, power, thermals, and physical-device parity.

## The current Mumble latency pipeline

The production Windows path is primarily in [`Internal/app/mumble.py`](../../Internal/app/mumble.py). Relevant current behaviour includes:

- The audio format is 16 kHz mono and the live chunk threshold is four seconds (`STREAM_CHUNK_SECONDS = 4.0`) ([mumble.py, lines 110-116](../../Internal/app/mumble.py#L110-L116)).
- On activation, Mumble first pauses the optional wake-word microphone owner, resets recording state, shows the listening Island, opens the selected input stream, starts it, and then launches a transcription worker ([mumble.py, lines 1320-1442](../../Internal/app/mumble.py#L1320-L1442)).
- The worker waits until at least four seconds of new samples exist, concatenates them, and performs a complete local transcription of that independent piece ([mumble.py, lines 1062-1132](../../Internal/app/mumble.py#L1062-L1132)).
- Worker results are appended to `_stream_results`; no result is sent to the Island, main window, or target application as live partial text.
- After Stop, Mumble waits for in-flight work, joins recorded buffers, and either transcribes the remaining tail or performs one complete pass when the worker produced nothing ([mumble.py, lines 1787-1881](../../Internal/app/mumble.py#L1787-L1881); [mumble.py, lines 2114-2208](../../Internal/app/mumble.py#L2114-L2208)).
- Local `faster-whisper` decoding uses greedy search (`beam_size=1`), an explicit language, Silero VAD, and serialized access to the model ([mumble.py, lines 1956-2079](../../Internal/app/mumble.py#L1956-L2079)).
- Local mode normally loads its model at boot. Mumble deliberately runs one decoder warm-up and one VAD warm-up pass ([mumble.py, lines 4065-4207](../../Internal/app/mumble.py#L4065-L4207)). Cloud transcription intentionally leaves the local model unloaded until a local fallback is needed ([mumble.py, lines 6054-6071](../../Internal/app/mumble.py#L6054-L6071)).
- The boot audio warm-up calls `sounddevice.query_devices()`, which enumerates devices but does not open and start the user's selected recording stream ([mumble.py, lines 6033-6043](../../Internal/app/mumble.py#L6033-L6043)).
- Plain Text normally takes an `instant_text` path: deterministic transcript formatting replaces optional network polishing ([mumble.py, lines 2251-2309](../../Internal/app/mumble.py#L2251-L2309)). Prompt and other deliberate AI lanes can add local or cloud generation time.
- A history entry and statistics are persisted before paste. Web-interface refresh is asynchronous, then Mumble sets the clipboard, waits 40 ms, sends Ctrl+V, and waits at least another 180 ms before restoring the previous clipboard ([mumble.py, lines 2325-2400](../../Internal/app/mumble.py#L2325-L2400); [mumble.py, lines 2528-2595](../../Internal/app/mumble.py#L2528-L2595)). The latter wait protects clipboard correctness but occurs after Ctrl+V has been sent.
- Existing timing reports release-to-paste with drain, tail, shaping, and paste subspans. It does not cover hotkey-to-first-callback, first speech, first stable partial, partial rendering, or warm-state/resource evidence ([mumble.py, lines 2371-2392](../../Internal/app/mumble.py#L2371-L2392)).

### Why it appears to warm up

The following explanation is an **inference from verified Mumble behaviour**:

| Dictation pattern | What Mumble currently does | Likely user experience |
|---|---|---|
| One-to-three-second clip | The four-second worker threshold is never reached. The whole clip is decoded after Stop. | Noticeable wait after every short utterance. |
| Eight-second plain dictation | At least one four-second piece can decode while speech continues. Only in-flight work and the tail remain after Stop. | Faster final response despite more audio. |
| Sustained sequence while app/model/device remain active | OS caches, model allocations, decoder/VAD paths, device state, and processor performance states may remain favourable. | Later dictations may improve further, but this part is not yet measured. |
| Prompt/AI mode | Live worker is intentionally skipped and a full pass occurs at Stop; generation may follow. | Slower and more variable than plain Text by design. |
| Cloud mode followed by cloud failure | The local model is intentionally absent, so fallback can load and warm it on the critical path. | A severe one-off delay that can look like cold start. |

The four-second policy is not genuine streaming. It performs independent complete transcriptions with no retained acoustic state, no overlap, no confirmed stable prefix, and no incremental display. The Whisper-Streaming maintainers explain why naive fixed windows can split words and why consecutive hypotheses need a stable-prefix policy; their LocalAgreement policy confirms text only when successive updates agree ([Whisper-Streaming README](https://github.com/ufal/whisper_streaming)). The project now describes itself as superseded by SimulStreaming, so its most reusable value for Mumble is the algorithmic lesson rather than automatic wholesale adoption.

### File/function trace of Mumble's current path

| Pipeline concern | Current Mumble implementation | What is already good | Gap relevant to the reported symptom |
|---|---|---|---|
| Model lifetime | `Mumble._try_load`, `_ensure_local_model`, `_unload_local_model`, `_warm_model` in [`mumble.py`](https://github.com/mongre25-droid/mumble/blob/cde23725e0d9eaeffe218e02c225ff5f8a499312/Internal/app/mumble.py#L4065-L4208) | Local mode keeps one model resident; swap failure preserves the old model; decoder and Silero VAD paths are consumed during warm-up; CUDA failure retries on CPU. | There is no recorded warm-state health, last-use age, or exact-path comparison. Cloud mode deliberately unloads/defers local STT, so a cloud failure can put load and warm-up on the critical path. |
| Audio ownership and capture | `start_recording`, `_open_input_stream`, `_audio_cb` in [`mumble.py`](https://github.com/mongre25-droid/mumble/blob/cde23725e0d9eaeffe218e02c225ff5f8a499312/Internal/app/mumble.py#L990-L1049) and [`mumble.py`](https://github.com/mongre25-droid/mumble/blob/cde23725e0d9eaeffe218e02c225ff5f8a499312/Internal/app/mumble.py#L1320-L1424) | Listening feedback is scheduled before stream construction; missing saved devices fall back to the system default; sleep/resume gaps reset stale session state. | Every activation closes the wake-word lease, constructs and starts a new `sounddevice.InputStream`, and leaves block size/latency to PortAudio. Boot “warm-up” only enumerates devices. Hotkey-to-first-callback is unmeasured. |
| VAD and endpointing | `faster-whisper` is called with `vad_filter=True`, `no_speech_threshold=0.6`; the user manually ends foreground dictation. | VAD removes silence inside the recorded clip; confidence gating filters suspect decoded segments. | There is no live speech-start marker or automatic endpoint in ordinary push-to-dictate. VAD does not reduce the four-second first scheduling boundary, and its hidden upstream default silence policy is not recorded in a trace. |
| Scheduling | `_stream_worker` polls every 100 ms, waits for four seconds of new audio, runs one independent full decode under `_tx_lock`, and stores exact covered samples ([`mumble.py`](https://github.com/mongre25-droid/mumble/blob/cde23725e0d9eaeffe218e02c225ff5f8a499312/Internal/app/mumble.py#L1062-L1132)). | Exact sample accounting and same-session guards prevent lost or duplicated ranges; Stop harvests a same-session in-flight result. | No retained decoder state, overlap, backpressure metric, queue depth, or stable-prefix logic. Short clips never overlap recording and inference. Resource Saver and every deliberate mode skip the worker entirely. |
| Partial policy | `_stream_results` is an internal list joined only after Stop. | It safely reuses completed work for final output. | Mumble has no first partial, first stable partial, revision count, or Island partial display. It improves only final latency for long enough clips, not first-visible-text latency. |
| Finalization | `stop_recording` stops/closes the stream, waits up to 25 seconds for an in-flight decode, then `_process` decodes the exact tail or the whole clip ([`mumble.py`](https://github.com/mongre25-droid/mumble/blob/cde23725e0d9eaeffe218e02c225ff5f8a499312/Internal/app/mumble.py#L1787-L1881), [`mumble.py`](https://github.com/mongre25-droid/mumble/blob/cde23725e0d9eaeffe218e02c225ff5f8a499312/Internal/app/mumble.py#L2114-L2208)). | Bounded wait, stale-session rejection, micro-tail suppression, and fallback preserve correctness. | A wedged in-flight chunk cancels the whole dictation after 25 seconds rather than re-decoding on a healthy fallback. Final latency remains tail-size dependent. |
| Cleanup and durability | Plain Text uses deterministic formatting; vocabulary passes run before output; history and stats are synchronously saved before paste ([`mumble.py`](https://github.com/mongre25-droid/mumble/blob/cde23725e0d9eaeffe218e02c225ff5f8a499312/Internal/app/mumble.py#L2210-L2372)). | The ordinary path avoids a network LLM; refusing to paste when durable history fails avoids an unrecorded result. | Vocabulary, history, and stats time are inside release-to-paste. The design intentionally prefers durability to the lowest possible visible latency; the benchmark must measure that cost before moving anything. |
| Insertion | `_set_clipboard` confirms writes; `_paste_impl` serializes paste, releases modifiers, waits 40 ms before Ctrl+V, records Ctrl+V time, then waits before safe clipboard restoration ([`mumble.py`](https://github.com/mongre25-droid/mumble/blob/cde23725e0d9eaeffe218e02c225ff5f8a499312/Internal/app/mumble.py#L2505-L2609)). | Strong protection against busy-clipboard and overlapping-paste corruption. | “Visible” currently means Ctrl+V was sent, not that the target painted text. Partial insertion would need a separate ownership-aware protocol and must not be inferred safe from final paste. |
| Hardware selection | `_pick_device` selects NVIDIA CUDA/FP16 or CPU/configured compute; `recommended_stt_threads` uses a physical-core-scale budget. | Simple, tested fallback and per-decode real-time-factor logging. | Hardware tier is CPU/RAM based, not a measured STT probe. No production AMD/Intel GPU path is called by `_local_transcribe`. Thread count, power state, clocks, and contention are not captured per trace. |
| Dormant platform adapter | `transcription.get_platform_transcriber` and `platform.select_transcriber` can construct a sherpa-onnx SenseVoice adapter, but `_local_transcribe` does not call them. | There is useful interface and degradation scaffolding to reuse in a spike. | It is not part of ordinary dictation. The local `windows_dml.py` passes provider `dml` to sherpa-onnx while separately probing Microsoft's ONNX Runtime; that combination must be confirmed against the inspected upstream provider implementation rather than assumed valid. |

## Instrumentation plan

One `trace_id` should follow every dictation from activation to the target application. Timestamps should use a monotonic high-resolution clock. Logs should remain local by default and support an explicit export for benchmark sessions.

### Required stage spans

| Required stage | Start marker | End marker | What it distinguishes |
|---|---|---|---|
| 1. Activation/hotkey to microphone capture | Hotkey callback entered | First audio callback received | Binding/event-loop delay, wake-word lease, PortAudio/device opening, and stream start |
| 2. Microphone capture to first partial transcript | First speech frame, plus first callback as a secondary origin | First unstable and first stable partial separately | Audio buffering, VAD, chunking, resampling, inference scheduling, and streaming policy |
| 3. First partial to visible UI text | Stable partial emitted | Island render acknowledgement | Controller-to-UI dispatch and rendering delay |
| 4. Speech completion to final transcript | User Stop and detected end-of-speech separately | Final raw text available | Drain, endpointer delay, final tail, and decoder finalization |
| 5. Model inference | Decode queued | First token, stable prefix, and decode complete | Queueing/lock contention versus actual model compute |
| 6. Cleanup/formatting/insertion | Raw text ready | Formatted text, durable enqueue, clipboard set, Ctrl+V sent, and optional target acknowledgement | Formatting, disk persistence, IPC, clipboard contention, and target insertion |
| 7. Cold-start performance | Process/model/audio cold reason recorded | First successful visible text | Download, load, allocation, runtime initialization, and first-use kernels |
| 8. Warm/continuous performance | Same stages after controlled idle/use intervals | Same end points | Whether improvement follows app uptime, recent inference, recent microphone use, or sustained load |
| 9. Resource/power cost | Warm policy entered | Periodic samples and policy exit | Resident RAM/VRAM, idle CPU/GPU, clocks, energy use, and thermal impact |

### Required trace fields

- operating system, build and commit;
- backend, model identifier and model-file hash;
- CPU model, physical/logical core count, RAM, GPU, driver/runtime versions;
- selected audio device, requested/actual sample rate, callback block size, reported latency, first callback time, underflow/overflow status;
- local/cloud route, fallback reason, model already resident, VAD already initialized, process uptime, and seconds since prior dictation;
- audio duration, detected speech duration, silence before/after speech, language, mode, and whether the live worker ran;
- queue wait, lock wait, inference time, real-time factor, first token, first stable partial, final raw, formatting, persistence, clipboard and paste timings;
- process RSS, committed memory, GPU memory, CPU/GPU utilization and clocks where supported;
- AC/battery state, Windows power mode, thermal or contention flags;
- correctness measures: word error rate (WER), named-term error, clipped first/last words, duplicated seam text, hallucination, and partial revisions.

### Cold and warm classifications

Do not use one ambiguous `cold=true` flag. Record specific states:

1. **Machine cold:** first controlled run after reboot.
2. **Process cold:** first dictation after launching Mumble.
3. **Model cold:** weights not resident before activation.
4. **Runtime cold:** model object exists, but the real VAD/decoder/kernel path has not run.
5. **Audio cold:** selected device stream has not been opened in this process.
6. **Idle-cooled:** process and model remain resident after 30 seconds, 5 minutes, or 30 minutes without inference.
7. **Warm:** second, fifth, and twentieth consecutive controlled dictations.

This separation is essential. NVIDIA documents that CUDA lazy loading can move module initialization into first kernel use and skew a measured inference; it recommends a warm-up iteration or preloading the kernel. NVIDIA also states that eager loading may suit latency-sensitive applications, while trading higher startup time and memory for predictable launch overhead ([CUDA lazy loading](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/lazy-loading.html); [CUDA advanced host programming](https://docs.nvidia.com/cuda/archive/13.1.1/cuda-programming-guide/03-advanced/advanced-host-programming.html)). That is evidence that first-use GPU work is plausible, not evidence that it is Mumble's dominant delay.

Windows similarly documents adaptive processor performance states and QoS-dependent scheduling. Background or inactive work can run in more efficient performance modes, and automated benchmarks can be affected by user-inactivity QoS ([processor performance policies](https://learn.microsoft.com/en-us/windows/win32/power/processor-performance-control-policy-constants); [Windows Quality of Service](https://learn.microsoft.com/en-us/windows/win32/procthread/quality-of-service)). Power mode must therefore be a benchmark variable, not a presumed fix. Mumble should not silently force a machine-wide high-performance power plan.

## Open-source approaches and reusable lessons

### 1. Keep and improve faster-whisper/CTranslate2

`faster-whisper` is an MIT-licensed Whisper implementation using CTranslate2. Its maintainers publish CPU INT8 and NVIDIA GPU modes, batched inference, integrated Silero VAD, and comparative benchmarks. Their benchmark shows that batching substantially improves long-audio throughput but raises memory use; that does not imply batching will lower latency for one tiny utterance. They also note that transcription begins only when the returned segment generator is iterated ([faster-whisper README](https://github.com/SYSTRAN/faster-whisper)).

CTranslate2 recommends CPU INT8, warns that total inter-thread × intra-thread concurrency should not exceed physical cores, and recommends GPU batching for throughput ([CTranslate2 performance guide](https://opennmt.net/CTranslate2/performance.html)). Mumble's present logical-CPU heuristic should therefore be calibrated rather than assumed optimal.

**Reusable now:** existing Python integration, model set, CPU INT8 fallback, NVIDIA CUDA path, greedy decoding, explicit language, VAD, hotwords, and warm-up framework.  
**Missing:** a stateful streaming policy, live partial publication, exact warm-state health, and calibrated threading.  
**Fit:** excellent baseline; lowest integration risk.  
**Hardware:** strong on modern CPUs and NVIDIA GPUs; no current production acceleration for common non-NVIDIA Windows GPUs.

### 2. Whisper-Streaming and SimulStreaming policies

Whisper was not designed as a native frame-by-frame streaming recognizer. Whisper-Streaming reprocesses a rolling audio buffer and commits only a stable prefix agreed across consecutive updates. Its code is MIT-licensed and demonstrates adaptive latency, trimming, timestamps, and backend abstraction ([Whisper-Streaming repository](https://github.com/ufal/whisper_streaming)).

Its maintainers now recommend their newer SimulStreaming project, which supports streaming ASR, says it is about five times faster than its predecessor, and currently declares an MIT licence ([SimulStreaming repository](https://github.com/ufal/SimulStreaming)).

**Reusable code:** possible after pinning and reviewing an exact release and all dependencies.  
**Reusable idea:** stable-prefix commitment, rolling context, explicit finalization, and latency evaluation are immediately valuable.  
**Fit:** strong for preserving Whisper's language coverage; medium-to-high engineering effort.  
**Risk:** repeated rolling Whisper inference can cost more power than a true streaming transducer, and partials can revise.

### 3. sherpa-onnx with a true streaming model

`sherpa-onnx` is an Apache-2.0 deployment framework for offline speech models. Its official documentation lists streaming and non-streaming ASR, VAD, Windows/macOS/Linux, x86/ARM/RISC-V, many language bindings, and CPU/GPU deployment options ([sherpa introduction](https://k2-fsa.github.io/sherpa/intro.html); [sherpa-onnx repository](https://github.com/k2-fsa/sherpa-onnx)).

Unlike fixed Whisper windows, streaming transducer or CTC models retain recognition state and can accept small audio frames continuously. This can reduce first-result and final-tail latency. Accuracy, punctuation, multilingual breadth, model licence, and hardware behaviour must be tested model by model.

**Reusable code:** runtime API, endpointing/VAD, streaming recognizer, model packaging examples.  
**Reusable idea:** one persistent recognizer session per utterance with `accept_waveform`, interim result, endpoint detection, final result, and reset.  
**Fit:** strongest architectural candidate for immediate partials, especially CPU-only systems.  
**Risk:** a second model family and runtime increase download, QA, language, and packaging complexity.

### 4. whisper.cpp

`whisper.cpp` is an MIT-licensed C/C++ Whisper implementation with quantization and multiple native acceleration backends. Its repository includes real-time microphone and server examples and documents CPU-only, CUDA, Vulkan, OpenVINO, Metal, and Core ML paths ([whisper.cpp repository](https://github.com/ggml-org/whisper.cpp)).

**Reusable code:** C API or a long-lived sidecar, quantized model handling, backend probing, and native packaging patterns.  
**Reusable idea:** one portable backend can cover hardware missed by CUDA-only selection.  
**Fit:** high for broad desktop hardware and local-first distribution.  
**Risk:** its simple streaming example is not by itself a stable dictation algorithm; Mumble still needs prefix commitment and robust insertion semantics.

### 5. Handy

Handy is an MIT-licensed, cross-platform, fully offline dictation application. Its maintainers describe a Tauri/React frontend, Rust backend, `cpal` audio, VAD, resampling, Whisper through `whisper-rs`, and Parakeet through `transcribe-rs`. It exposes model selection and a model-unload policy and supports hidden/autostart operation ([Handy repository](https://github.com/cjpais/Handy)).

**Reusable code:** individual MIT components may be reviewed, but a wholesale port would conflict with Mumble's Python controller and platform seam.  
**Reusable idea:** explicit unload timeout, an optional always-on microphone policy, backend/model choice, debug timing, and native audio/inference separation.  
**Fit:** best used as product and architecture reference, not as a replacement application.

### 6. nerd-dictation and Vosk

nerd-dictation is a GPL-3.0 Linux dictation utility built on Vosk. Its maintainers explicitly acknowledge cold model load on slower disks, start recording before loading to hide some delay, and offer suspend/resume so the process and model remain in memory. Recognition and recording operate in parallel ([nerd-dictation repository](https://github.com/ideasman42/nerd-dictation)).

**Reusable code:** not suitable for direct copying unless Mumble deliberately accepts GPL-3.0 reciprocal distribution obligations.  
**Reusable idea:** keep recognition state resident, separate begin/end control from process lifetime, and overlap safe initialization with capture.  
**Fit:** a useful proof that warm-state policy is a long-standing dictation concern; weaker punctuation and desktop portability make it a poor default replacement.

## Deep source-code profiles

The profiles below follow the same ten questions for every serious candidate. “Not present” is intentional evidence: an inference engine should not receive credit for microphone handling, endpointing, partial UI, insertion, or recovery that an integrating application must still build.

### faster-whisper `1.2.1` at `65882eee9f5cdbeeb2d877f1131d48cf241b327d` and current source `ed9a06cd89a93e47838f564998a6c09b655d7f43`

Mumble pins `faster-whisper==1.2.1`. The current inspected source still reports 1.2.1; its transcribe/audio/model path is materially the same, while VAD options and its embedded ONNX asset changed. Claims below use current source links where the path is unchanged and flag the version distinction where it matters.

**1. Process and model lifetime.** `WhisperModel.__init__` synchronously resolves a model name or directory, constructs one long-lived `ctranslate2.models.Whisper`, feature extractor, and tokenizer. Name-based downloads use Hugging Face `snapshot_download` with an allowlist and optional revision/local-only controls, but Mumble passes only a model name/device/compute/thread count, so the downloaded model revision is not application-pinned. faster-whisper has no unload timer, warm scheduler, or crash restart; Mumble owns those policies ([`WhisperModel.__init__`](https://github.com/SYSTRAN/faster-whisper/blob/ed9a06cd89a93e47838f564998a6c09b655d7f43/faster_whisper/transcribe.py#L620-L723), [`download_model`](https://github.com/SYSTRAN/faster-whisper/blob/ed9a06cd89a93e47838f564998a6c09b655d7f43/faster_whisper/utils.py#L49-L115)).

**2. Audio capture.** The library has no microphone path. File input goes through PyAV/FFmpeg, is resampled to signed 16-bit mono 16 kHz, fully buffered, and converted to float; a NumPy input bypasses that decoder/resampler. Mumble already supplies mono float32 16 kHz arrays, so upstream file decode is not on the interactive path ([`decode_audio`](https://github.com/SYSTRAN/faster-whisper/blob/ed9a06cd89a93e47838f564998a6c09b655d7f43/faster_whisper/audio.py#L19-L76)).

**3. VAD and endpointing.** Current `VadOptions` defaults to threshold `0.5`, negative threshold `0.35`, 512-sample/32 ms frames, 2,000 ms minimum silence, and 400 ms padding. `get_speech_timestamps` computes probabilities over the complete supplied array and emits an open speech region at end-of-buffer; it is segmentation, not a continuously running endpoint controller. The Silero ONNX session is lazily constructed once and process-cached; each call resets recurrent state and scans the supplied buffer. Mumble warms that session, but ordinary foreground Stop still comes from the user ([`VadOptions` and segmentation`](https://github.com/SYSTRAN/faster-whisper/blob/ed9a06cd89a93e47838f564998a6c09b655d7f43/faster_whisper/vad.py#L15-L217), [`SileroVADModel`](https://github.com/SYSTRAN/faster-whisper/blob/ed9a06cd89a93e47838f564998a6c09b655d7f43/faster_whisper/vad.py#L322-L385)). Current VAD defaults differ from the exact bundled 1.2.1 code, so Mumble must record its installed version and effective VAD options in every benchmark.

**4. Inference scheduling.** `transcribe` fully obtains audio, optionally compacts it with VAD, computes Mel features, optionally detects language, and returns a generator; decoding occurs only when the generator is iterated. The feature extractor defaults to 16 kHz audio, a 400-sample/25 ms FFT window, and a 160-sample/10 ms hop, and recomputes the spectrogram/Mel frontend for the supplied waveform rather than preserving an incremental frontend cache across calls. The decoder then processes up-to-30-second feature windows sequentially, constructs token prompts, invokes CTranslate2 generation, and yields completed segments. Temperature fallback can decode a difficult region again. Mumble avoids major costs by fixing language, greedy beam/best-of 1, temperature `0`, no previous-text conditioning, and no timestamps for plain Text ([`FeatureExtractor`](https://github.com/SYSTRAN/faster-whisper/blob/ed9a06cd89a93e47838f564998a6c09b655d7f43/faster_whisper/feature_extractor.py#L13-L168), [`transcribe`](https://github.com/SYSTRAN/faster-whisper/blob/ed9a06cd89a93e47838f564998a6c09b655d7f43/faster_whisper/transcribe.py#L747-L1022), [window loop](https://github.com/SYSTRAN/faster-whisper/blob/ed9a06cd89a93e47838f564998a6c09b655d7f43/faster_whisper/transcribe.py#L1110-L1400), [fallback decode](https://github.com/SYSTRAN/faster-whisper/blob/ed9a06cd89a93e47838f564998a6c09b655d7f43/faster_whisper/transcribe.py#L1402-L1530)).

**5. Partial transcript policy.** There is no token callback, stable-prefix API, or revision contract. A yielded segment is a completed result after the current window has been encoded and decoded. `condition_on_previous_text` is prompt continuity, not retained acoustic or decoder state across `transcribe` calls. Mumble's four-second worker therefore cannot become true streaming merely by iterating faster-whisper's generator.

**6. Finalization and insertion.** faster-whisper returns model text/timestamps and applies decoder thresholds/fallbacks; it has no desktop punctuation policy, clipboard, typing, focus check, persistence, or insertion recovery. Those remain Mumble responsibilities.

**7. Hardware optimization.** Optimization comes mainly from CTranslate2 compute types, CPU threads, CUDA, and avoiding unnecessary decoding features. `BatchedInferencePipeline` VAD-chunks and materializes multiple feature batches before generation; it is designed for throughput and can add memory/wait for a single short dictation. It should not be presented as a first-visible-text optimization without evidence ([batched pipeline](https://github.com/SYSTRAN/faster-whisper/blob/ed9a06cd89a93e47838f564998a6c09b655d7f43/faster_whisper/transcribe.py#L111-L590)).

**8. Production resilience.** Invalid PyAV frames can be skipped and inference exceptions propagate. No per-request cancellation API, model-health watchdog, device failover, telemetry policy, or signed model manifest is supplied. Offline operation depends on the model/tokenizer already being cached or a caller deliberately using local-only resolution.

**9. Licensing.** faster-whisper source is MIT at both inspected revisions ([`LICENSE`](https://github.com/SYSTRAN/faster-whisper/blob/65882eee9f5cdbeeb2d877f1131d48cf241b327d/LICENSE)). CTranslate2, PyAV/FFmpeg distribution, the embedded Silero model/runtime, tokenizers, and downloaded model snapshots are separate compliance items. The default converted model card and its originating OpenAI model card do not make source-code MIT automatically govern the weights. Mumble must record exact model repository, revision, checksum, and model-card licence. Technical use does not grant the faster-whisper name or logo as Mumble branding.

**10. Transferable lesson.** Keep the existing backend and its optimized one-pass settings. The real experiment is a coordinator above it: one-to-1.5-second rolling windows, overlap, timestamp/stable-prefix confirmation, and Island partials. Also pin the Hugging Face snapshot. “Add model/VAD warm-up” is **not** a new recommendation: Mumble already consumes both generators after load. The report corrects Mumble's comment that this “JIT-compiles decoder kernels”; inspected CTranslate2 source supports lazy handle/allocator/library initialization, not runtime JIT of its compiled kernels.

### CTranslate2 at `0d8bcd362ac75ef860ef161d6f0efad0ae439ff0` (v4.8.1; Mumble environment observed at 4.8.0)

**1. Process and model lifetime.** Python's `ReplicaPoolHelper` releases the GIL and synchronously constructs a pool of model workers. Explicit `unload_model(to_cpu)` refuses while work is active and can retain a CPU clone; `load_model(keep_cache)` restores from that clone or disk. faster-whisper holds this object and does not call those methods between dictations. There is no automatic unload timer or keep-warm policy ([`ReplicaPoolHelper`](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/python/cpp/replica_pool.h#L38-L70), [load/unload methods](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/python/cpp/replica_pool.h#L105-L193)).

**2. Audio capture.** None. CTranslate2 receives precomputed Whisper features/tokens. It does not select devices, resample microphone audio, or preserve capture while a model loads.

**3. VAD and endpointing.** None. These are faster-whisper/application layers.

**4. Inference scheduling.** `inter_threads` creates workers/replicas; `intra_threads` sets OpenMP threads per worker. The queue defaults to four times the worker count and submission blocks when full; queued and active batch counts are exposed. Asynchronous calls return a future with `result`/`done`, not cancellation. Each Whisper generation creates a fresh decoder state and encoder memory. Within that call, transformer layers cache self-attention and projected encoder key/value tensors; those caches do not survive a separate generation call ([Whisper Python binding](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/python/cpp/whisper.cc#L177-L224), [`ReplicaPool` queue](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/include/ctranslate2/replica_pool.h#L99-L148), [`Whisper::generate`](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/src/models/whisper.cc#L232-L370), [decoder caches](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/src/layers/transformer.cc#L730-L822)).

**5. Partial transcript policy.** None. Generation returns completed token sequences. Async execution can overlap callers, but does not expose stable/revisable partial words.

**6. Finalization and insertion.** None beyond generation results/scores/no-speech probability.

**7. Hardware optimization.** Public devices are CPU, CUDA, or auto; auto chooses CUDA when detected, otherwise CPU. Effective compute type is resolved against backend capability. There is no DirectML, Vulkan, OpenVINO, Core ML, or Metal device in this path. The binding exposes integer/floating compute types, intra/inter threads, FlashAttention, and tensor parallelism; CPU code dispatches AVX/AVX2/optional AVX-512 or NEON. FlashAttention is off by default and restricted by device/type support, so it is an Ampere-or-newer A/B test rather than a default ([device selection](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/src/devices.cc#L15-L30), [compute fallback](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/src/types.cc#L156-L295), [FlashAttention validation](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/src/models/model.cc#L827-L859), [CPU ISA dispatch](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/src/cpu/cpu_isa.cc#L40-L76)).

**8. Production resilience.** Model loading validates binary/spec versions and payload sizes. Worker queues provide backpressure and futures propagate exceptions. Model unload is concurrency-guarded. No inference cancellation exists. The local model reader uses `ifstream`, not memory mapping; Mumble cannot assume mmap-based fast reload ([model reader](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/src/models/model_reader.cc#L18-L35), [model validation/load](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/src/models/model.cc#L551-L783)).

**9. Licensing.** CTranslate2 is MIT ([`LICENSE`](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/LICENSE)); optional CUDA/cuDNN/MKL and bundled third-party headers/binaries retain their own terms. Mumble should pin CTranslate2 directly rather than rely only on faster-whisper's broad `<5` dependency range: the live environment and latest inspected release already differ.

**10. Transferable lesson.** Expose effective device/compute, queued batches, and active workers in Mumble's trace. Keep one resident worker for ordinary dictation; extra workers and batched inference improve concurrency/throughput, not inherently one short request. Benchmark `flash_attention=True` only on compatible NVIDIA hardware. Do not unload-to-CPU between infrequent dictations if first-word latency is the goal. Mumble's blanket comment that faster-whisper is unsafe for concurrent calls is not established by upstream; foreground/meeting contention should be measured, then controlled through priority and bounded queues.

### whisper.cpp at `080bbbe85230f624f0b52127f1ae1218247989f9`

**1. Process and model lifetime.** The stream example opens/resumes SDL capture before model loading, then retains one `whisper_context` until exit. That can preserve early speech in its finite circular buffer while the model starts, but the example performs no representative inference warm-up. The server's reload path frees the old context before constructing its replacement and treats load failure as fatal—an unsafe swap policy for Mumble, whose current faster-whisper swap preserves the old model on failure ([`examples/stream/stream.cpp`](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/examples/stream/stream.cpp#L145-L175), [context/state lifetime](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/src/whisper.cpp#L3374-L3544), [server reload](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/examples/server/server.cpp#L1163-L1201)).

**2. Audio capture.** `audio_async` uses SDL at 16 kHz float32 mono. The callback requests 1,024 samples (64 ms), writes them under a mutex into a circular buffer, and the main thread polls/copies. No robust device-loss/reopen state machine was found in this path ([`examples/common-sdl.cpp`](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/examples/common-sdl.cpp#L17-L78), [capture buffer](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/examples/common-sdl.cpp#L138-L210)). This is a native capture reference, not a complete replacement for Mumble's selected-device recovery and privacy ownership.

**3. VAD and endpointing.** The stream example's default `--step 0` path uses a simple energy-ratio detector with threshold `0.6` and 100 Hz high-pass and waits for a pause; it is not the best component to copy ([`stream.cpp`](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/examples/stream/stream.cpp#L293-L313), [`vad_simple`](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/examples/common.cpp#L597-L646)). The core separately contains learned Silero VAD with defaults threshold `0.5`, 250 ms minimum speech, 100 ms minimum silence, and 30 ms padding. Its stateful no-reset API consumes 512-sample/32 ms frames and can retain recurrent VAD state; ordinary full transcription instead initializes and batch-runs VAD for the clip ([VAD options](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/include/whisper.h#L192-L199), [stateful VAD API](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/include/whisper.h#L699-L750), [VAD implementation](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/src/whisper.cpp#L5100-L5434)).

**4. Inference scheduling.** The stream sample defaults to a 3,000 ms step, 10,000 ms rolling length, and 200 ms keep/overlap. It waits for roughly three seconds before synchronous `whisper_full`; if backlog exceeds twice the step, it clears buffered audio, causing data loss. Each full call recreates Mel/decoder work. `whisper_full_parallel` partitions work for throughput and suppresses callbacks in subcontexts, so it is not a first-visible-text solution ([defaults and loop](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/examples/stream/stream.cpp#L18-L43), [backlog/drop/inference](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/examples/stream/stream.cpp#L244-L344), [`whisper_full_parallel`](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/src/whisper.cpp#L7795-L7905)).

**5. Partial transcript policy.** The example redraws rolling completed output, defaults `no_context=true`, and has no stable-prefix confirmation. The API exposes new-segment and abort callbacks, but segment callbacks fire decoded segments rather than every token. Its default earliest display is therefore approximately three seconds of audio plus synchronous inference—not competitive with the proposed sub-second first stable partial ([stream output](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/examples/stream/stream.cpp#L346-L427), [callback API](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/include/whisper.h#L484-L590)).

**6. Finalization and insertion.** The API produces segments/tokens and supports an abort callback. The example prints/redraws terminal output; it has no desktop cleanup, clipboard ownership, target-app revision, or durable history.

**7. Hardware optimization.** Context initialization registers CPU plus requested accelerator backends and places tensors by backend priority/operation support. Build options cover CPU SIMD/BLAS, CUDA, HIP, Vulkan, Metal, SYCL, OpenVINO, and Core ML; no DirectML path was observed. Quantization excludes unsuitable tensors such as embeddings/biases. Source confirms backend-specific cold work: Vulkan lazily creates/caches pipelines, Metal caches or compiles libraries/pipelines, and OpenVINO configures cache and compiles an encoder. Therefore a persistent process plus representative inference warm-up is justified; simply allocating the model is not a full warm state ([backend initialization](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/src/whisper.cpp#L1290-L1403), [quantization selection](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/examples/quantize/quantize.cpp#L39-L55), [build backends](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/ggml/CMakeLists.txt#L150-L254), [Vulkan pipeline cache](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/ggml/src/ggml-vulkan/ggml-vulkan.cpp#L3827-L3866), [OpenVINO compile/cache](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/src/openvino/whisper-openvino-encoder.cpp#L10-L48)).

**8. Production resilience.** The server serializes inference and aborts on client disconnect; core initialization catches backend exceptions, and model loading validates magic, shapes, tensor sizes, and counts. However, the example model download script writes directly to the destination and trusts a pre-existing file without a cryptographic manifest/atomic rename. Mumble should keep its safer download principles rather than copy that workflow ([server mutex/abort](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/examples/server/server.cpp#L632-L726), [loader validation](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/src/whisper.cpp#L1485-L1555), [download script](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/models/download-ggml-model.sh#L109-L140)). No in-flight failed-GPU-to-CPU recovery was found.

**9. Licensing.** whisper.cpp is MIT at this revision ([`LICENSE`](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/LICENSE)). The inspected OpenAI Whisper provenance revision and Silero revision are also MIT, but converted GGML models and optional SDL/CUDA/OpenVINO/etc. binaries remain separate inventory/notice items. Technical integration does not grant whisper.cpp branding.

**10. Transferable lesson.** Benchmark a **persistent** whisper.cpp backend primarily for quantized weak-PC tiers and AMD/Intel/Vulkan/OpenVINO/Metal coverage. Separately test its stateful learned VAD. Reuse its callback/abort and backend-probing ideas. Do **not** adopt the stream example's three-second poll, energy VAD, buffer-drop policy, or parallel full transcription as latency fixes. Mumble still needs its own stable-prefix coordinator and production recovery.

### Whisper-Streaming at `6da90b44b7e50d79695e68166d2a2c7609c75abb`

**1. Process and model lifetime.** `asr_factory` constructs the selected backend once, measures model-load time, then creates one `OnlineASRProcessor`; the server keeps those objects alive across chunks. The faster-whisper wrapper constructs `WhisperModel` in `FasterWhisperASR.load_model`. The MLX wrapper explicitly calls `ModelHolder.get_model` so the model becomes a static/global resource and is preloaded rather than reloaded per inference. No unload timer, idle eviction, crash restart, or model-download verification is implemented in this repository's online processor ([`whisper_online.py`, `FasterWhisperASR` and `asr_factory`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L100-L157), [`MLXWhisper.load_model`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L168-L201), [`asr_factory`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L782-L830)).

**2. Audio capture.** The core accepts NumPy audio through `insert_audio_chunk`; it does not own a microphone. Its fixed sample rate is 16 kHz. The server/simulation can feed small chunks, and the VAC wrapper's comment gives 40 ms as an example. Therefore device setup, channels, resampling, callbacks, loss recovery, and capture-before-model-ready are responsibilities of the caller, not demonstrated optimizations ([`OnlineASRProcessor`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L426-L456), [`VACOnlineASRProcessor`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L629-L649)).

**3. VAD and endpointing.** Two distinct modes exist. Passing `--vad` enables faster-whisper's batch VAD. Passing `--vac` loads Silero and wraps the online processor. The copied iterator uses threshold `0.5`, 512-sample frames at 16 kHz (32 ms), 500 ms minimum silence, and 100 ms speech padding; speech end immediately calls `finish`. While no voice is active, the wrapper retains only the most recent one second to bound memory ([`silero_vad_iterator.py`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/silero_vad_iterator.py#L9-L48), [`FixedVADIterator`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/silero_vad_iterator.py#L103-L130), [`VACOnlineASRProcessor.insert_audio_chunk`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L670-L709)).

**4. Inference scheduling.** The default minimum scheduling chunk is one second. Each `process_iter` retranscribes the current rolling audio buffer; it does not retain Whisper decoder/acoustic state. It supplies up to 200 characters of already-committed, scrolled-away text as a prompt. Once the audio buffer exceeds the default 15 seconds, it trims at a confirmed Whisper segment boundary; sentence trimming is optional. There is no queue, worker pool, backpressure policy, GPU stream selection, or batching in the online algorithm itself ([`add_shared_args`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L764-L780), [`OnlineASRProcessor.prompt` and `process_iter`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L458-L526), [`chunk_at`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L544-L575)).

**5. Partial transcript policy.** `HypothesisBuffer.flush` commits only the longest identical word prefix between the two latest timestamped hypotheses. `insert` drops up to five duplicate boundary words when their timestamps are close. Unmatched words remain incomplete and can revise on the next pass. `process_iter` returns only newly committed text; `finish` flushes the final unconfirmed tail. This is the concrete LocalAgreement policy that Mumble lacks ([`HypothesisBuffer.insert` and `flush`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L359-L417), [`OnlineASRProcessor.finish`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L603-L627)). First-partial latency is not fixed in code: it is at least one scheduling chunk plus decode and normally requires two agreeing hypotheses.

**6. Finalization and insertion.** The library returns timestamped text tuples. It has no punctuation pass beyond what the model emits, no desktop clipboard/type path, and no ownership-aware correction of text already inserted. The supplied line/server protocol is transport, not a dictation UI.

**7. Hardware optimization.** Its faster-whisper wrapper chooses CUDA/FP16 by default and contains commented CPU INT8 alternatives; MLX is a separate Apple path. The online policy itself adds repeated full-window work and does not introduce quantization, SIMD, device fallback, memory mapping, or power-state control. Those remain backend/application responsibilities ([`FasterWhisperASR.load_model`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/whisper_online.py#L100-L136)).

**8. Production resilience.** The VAC buffer is bounded and timestamps survive trimming, but there is no cancellation token, concurrent-session isolation, device-loss handling, model integrity check, privacy UI, telemetry policy, process watchdog, or packaged desktop recovery. Exceptions can terminate the serving loop. It is a research/server algorithm, not a ready-made Mumble backend service.

**9. Licensing.** The inspected repository is MIT at this revision ([`LICENSE`](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/LICENSE)). It also copies a modified Silero iterator and points to Silero's MIT licence in source. Backend, model, tokenizer, and optional dependency licences still require separate inventory. Directly copying `HypothesisBuffer` is legally plausible with notices, but a small Mumble-native implementation would reduce dependency and packaging surface.

**10. Transferable lesson.** Adopt the **two-hypothesis word-level stable-prefix contract**, timestamp-aware boundary deduplication, and bounded rolling buffer as a small experiment behind faster-whisper. Mumble already has exact sample seams, a resident model, and failure guards; it lacks overlap/redecode policy, confirmed versus volatile text, and live publication. Integration effort is medium. Risk is repeated compute and added time before the first *confirmed* word.

### SimulStreaming at `077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6`

**1. Process and model lifetime.** `PaddedAlignAttWhisper.__init__` loads one PyTorch Whisper model, installs cross-attention and key/value hooks, loads a learned continuous-integrate-and-fire (CIF) end-of-word detector, and retains model/token/audio state. `SimulWhisperASR.warmup` inserts representative audio, runs final inference, and resets the segment—an exact, executable warm-up rather than a README suggestion. No unload timer or process watchdog is present ([`PaddedAlignAttWhisper.__init__`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming/whisper/simul_whisper/simul_whisper.py#L35-L139), [`SimulWhisperASR.warmup`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming_whisper.py#L84-L121)).

**2. Audio capture.** Like Whisper-Streaming, the core has no microphone. `SimulWhisperOnline.insert_audio_chunk` accepts NumPy audio converted to Torch tensors at 16 kHz; the caller determines device, callback, resampling, and feed cadence ([`SimulWhisperOnline`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming_whisper.py#L128-L152)).

**3. VAD and endpointing.** `SimulWhisperASR.use_vad` explicitly prints “VAD not implemented.” The decoder does have Whisper no-speech gating and an optional learned CIF boundary detector. Attention-guided decoding stops before tokens whose strongest attention lies too near the end of available audio; at finalization it reduces that margin. These mechanisms govern safe token emission, not microphone-level utterance segmentation ([`use_vad`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming_whisper.py#L115-L124), [`infer` no-speech and attention boundary`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming/whisper/simul_whisper/simul_whisper.py#L395-L435), [`attention stop`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming/whisper/simul_whisper/simul_whisper.py#L511-L531)).

**4. Inference scheduling.** Audio chunks accumulate until the caller invokes `process_iter`. The model retains emitted token/context segments and a bounded audio buffer (default maximum 30 seconds). It pads the current audio to Whisper's 30-second input, re-encodes that buffer, then decodes incrementally token by token. Key/value caches are used within a generation call, but `_clean_cache` clears attention and KV caches after every call; retained acceleration comes from committed token/audio context, not an indefinitely retained decoder KV cache ([`process_iter`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming_whisper.py#L207-L254), [`insert_audio` and `_clean_cache`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming/whisper/simul_whisper/simul_whisper.py#L255-L295), [`infer` encoding`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming/whisper/simul_whisper/simul_whisper.py#L333-L380)). There is no application queue or backpressure policy.

**5. Partial transcript policy.** Rather than wait for agreement across two complete hypotheses, SimulStreaming emits tokens whose alignment attention is far enough behind the audio edge. Unless CIF fires or the caller finalizes, it drops the last incomplete space-delimited word. It also carries an incomplete Unicode token into the next update and makes word timestamps monotonic. On a detected attention rewind it abandons the suspect new segment and returns to prior committed tokens ([`hide_incomplete_unicode`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming_whisper.py#L190-L205), [`rewind and edge policy`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming/whisper/simul_whisper/simul_whisper.py#L511-L531), [`word-boundary emission`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming/whisper/simul_whisper/simul_whisper.py#L570-L602)).

**6. Finalization and insertion.** `finish` marks the next inference as last, emits the remaining tokens, then clears the segment. No desktop insertion, clipboard safety, deterministic formatting, or UI revision ownership is included.

**7. Hardware optimization.** This implementation is PyTorch Whisper, uses Torch CPU/GPU device selection, optional beam or greedy decode, KV cache hooks within a call, and optional Triton on Linux. It does not demonstrate CUDA/Metal/Vulkan/DirectML packaging for a cross-platform desktop app. The repository README's “about five times faster” statement is **documentation-only** in this report; the inspected code explains reduced repeated decoding, but Mumble must reproduce the comparison on named hardware.

**8. Production resilience.** It verifies official Whisper downloads with SHA-256 in its vendored loader, bounds audio/context, detects attention rewind, and suppresses incomplete Unicode. It does not implement device loss, cancellation, concurrency isolation, queue bounds, crash restart, signed helper updates, or a privacy/telemetry product boundary ([vendored model download verification](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming/whisper/simul_whisper/whisper/__init__.py#L52-L95)).

**9. Licensing.** The inspected repository declares MIT, copyright Charles University ([`LICENCE.txt`](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/LICENCE.txt)). It vendors modified Whisper code and depends on Torch, Triton, tokenization, and a CIF checkpoint; each binary and model licence must be preserved separately. Pin the exact checkpoint/model, not only the repository.

**10. Transferable lesson.** SimulStreaming offers a more direct low-latency Whisper design than two-pass LocalAgreement: **attention-distance commitment, last-word withholding, rewind rejection, and exact warm-up**. Mumble already has greedy Whisper, explicit language, warm-up, and final paste safety; it lacks token-level streaming and PyTorch packaging. A clean-room prototype is high effort/high research risk and should follow, not precede, the simpler stable-prefix experiment.

### sherpa-onnx at `9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e`

**1. Process and model lifetime.** The microphone examples construct an `OnlineRecognizer` and `OnlineStream` before capture and retain them for process lifetime. `OnlineStream` holds the feature extractor, decoder state, processed-frame count, and model-specific ONNX state; endpoint reset starts a new segment without reconstructing the recognizer. No library-owned idle unload, dummy warm-up, resume handler, or restart manager was found ([Python microphone setup](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/python-api-examples/speech-recognition-from-microphone-with-endpoint-detection.py#L134-L177), [`OnlineStream`](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/online-stream.cc#L18-L54), [endpoint reset](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/online-recognizer-ctc-impl.h#L234-L253)).

**2. Audio capture.** The C++ example uses a PortAudio callback and immediately calls `AcceptWaveform`. Its wrapper opens one float32 input channel with the device's suggested low latency and lets PortAudio choose `framesPerBuffer`. The Python example polls 100 ms of 48 kHz mono `sounddevice` audio and feeds a recognizer configured for 16 kHz, leaving sample-rate conversion to sherpa's feature path. Both examples wait for recognizer construction before capture; a Mumble integration could instead retain bounded preroll while readiness completes ([PortAudio callback](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/sherpa-onnx-microphone.cc#L24-L37), [stream configuration](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/microphone.cc#L45-L91), [Python read loop](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/python-api-examples/speech-recognition-from-microphone-with-endpoint-detection.py#L177-L195)).

**3. VAD and endpointing.** The online recognizer's default rules endpoint after 2.4 seconds of trailing silence with no decoded content, 1.2 seconds after decoded non-silence, or 20 seconds total. Timing comes from trailing blank decoder frames and model frame shift. A separate Silero VAD defaults to threshold `0.5`, 500 ms minimum silence, 250 ms minimum speech, 512-sample/32 ms windows, and 20-second maximum speech; its detector maintains circular preroll and a speech-segment queue. These affect utterance/final latency more than first-partial latency ([endpoint rules](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/endpoint.h#L14-L59), [endpoint evaluation](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/endpoint.cc#L15-L94), [Silero defaults](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/silero-vad-model-config.h#L13-L39), [VAD buffering](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/voice-activity-detector.cc#L28-L136)).

**4. Inference scheduling.** `IsReady` waits for model-defined chunk length. `DecodeStreams` can batch multiple streams, stack retained recurrent state, execute one model batch, and distribute updated states/results. The example drains all ready chunks, emits changed text, tests endpoints, and polls every 20 ms. This is genuine stateful online recognition, not repeated transcription of an expanding waveform. Queue bounds, worker ownership, cancellation, and backpressure are application duties ([ready/batch path](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/online-recognizer-ctc-impl.h#L114-L188), [microphone decode loop](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/sherpa-onnx-microphone.cc#L141-L180)). Batching chiefly benefits concurrency/throughput; it is not evidence of lower single-user first-partial latency.

**5. Partial transcript policy.** `GetResult` returns the current best recognizer result; examples display only changed text. There is no engine-wide LocalAgreement-style confirmation contract, so revision styling, deduplication, timestamps, and committed-versus-tentative UI are application responsibilities ([result construction](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/online-recognizer-ctc-impl.h#L191-L210), [changed-text display](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/sherpa-onnx-microphone.cc#L146-L175)).

**6. Finalization and insertion.** At an endpoint the example publishes the completed segment and resets the stream. One Paraformer branch feeds one second of zeros before the reset, adding explicit final-flush time. Inverse text normalization and homophone replacement can run synchronously while building a result. sherpa supplies no desktop clipboard, typing, history, or insertion recovery ([endpoint/final path](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/sherpa-onnx-microphone.cc#L146-L168), [result post-processing](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/online-recognizer-ctc-impl.h#L191-L210)).

**7. Hardware optimization.** The inspected provider layer includes CPU, CUDA, TensorRT, Core ML, XNNPACK, NNAPI, DirectML, and platform providers. Session setup controls ONNX threads/graph optimization; TensorRT supports engine/timing caches; CUDA sets cuDNN options; DirectML forces sequential execution and disables memory-pattern optimization. Unknown provider strings fall back to CPU, so Mumble's dormant `provider="dml"` adapter is especially risky unless exact binding behaviour is tested—upstream's canonical DirectML name is not that string. Quantization comes from selected int8 model files, not automatic runtime conversion ([provider names](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/provider.h#L16-L25), [provider parsing](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/provider.cc#L15-L37), [session/provider configuration](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/sherpa-onnx/csrc/session.cc#L135-L356)).

**8. Production resilience.** The core is local/offline after model installation and supports multiple streams, but the examples do not supervise capture, recover device hot-plug, bound queues, verify downloads, restore UI state, or restart a crashed helper. Mumble must own these behaviours.

**9. Licensing.** sherpa-onnx is Apache-2.0 at this revision. Redistribution requires the licence, preservation of applicable notices/attribution, and marking modified source where required. Dependencies and each model are separate; the build, for example, pins `kaldi-native-fbank` independently ([`LICENSE`](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/LICENSE), [dependency pin](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/cmake/kaldi-native-fbank.cmake#L1-L10)). Model cards and redistribution terms need approval per selected model.

**10. Transferable lesson.** Trial one persistent int8 online recognizer, feed bounded 50–100 ms PCM blocks, drain whenever `IsReady` is true, and publish changed text as tentative Island content. Mumble already has audio capture, lifecycle scaffolding, fallback, and final insertion; it lacks a production-called stateful online engine, queue/backpressure contract, and partial UI. A helper-process/binding spike is medium effort; accuracy, language coverage, packaging, and model terms are the main risks.

### Handy at `8a362e9eba59d4057fda79b7f38f5b0d5cbabf65`

**1. Process and model lifetime.** Activation starts model and VAD loading in parallel; the loaded engine is stored as a leased session. A watcher checks every ten seconds and unloads after a configurable idle interval, refreshes use while recording, and supports immediate unload. Audio can stay always open, or close lazily 30 seconds after on-demand use. No explicit dummy inference was found ([activation load](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/actions.rs#L463-L480), [session lease](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/transcription.rs#L169-L179), [model idle watcher](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/transcription.rs#L292-L459), [microphone lifetime](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/audio.rs#L332-L453)).

**2. Audio capture.** CPAL supplies callback audio at the preferred native device configuration. Handy converts channels to mono, passes samples through a channel, and starts with `play()` without requesting an explicit CPAL buffer size. A rubato FFT resampler consumes fixed 1,024-sample native blocks and emits 16 kHz audio in 30 ms frames. The command loop deliberately processes a recording command before an already-received audio chunk to avoid losing one device period; Stop drains with a two-second timeout ([CPAL setup](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/audio_toolkit/audio/recorder.rs#L139-L455), [resampler](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/audio_toolkit/audio/resampler.rs#L1-L114), [command-before-audio ordering](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/audio_toolkit/audio/recorder.rs#L600-L630), [drain](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/audio_toolkit/audio/recorder.rs#L632-L688)).

**3. VAD and endpointing.** Silero runs on 30 ms frames at threshold `0.3`. Smoothing uses 15 preroll frames, two voiced frames for onset, 15-frame offline hangover, and 55-frame streaming hangover: 450 ms preroll, 60 ms onset, 450 ms offline tail, and 1.65 seconds streaming tail. Audio shorter than one second is padded to 1.25 seconds for Whisper. The long streaming tail protects pauses but is an **inferred final-latency cost**, not a measured result ([VAD threshold](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/audio_toolkit/vad/silero.rs#L9-L57), [smoothing constants/logic](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/audio_toolkit/vad/smoothed.rs#L19-L109), [short-audio padding](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/audio.rs#L748-L765)).

**4. Inference scheduling.** Streaming may begin before loading completes, so 30 ms frames queue while the engine becomes ready. A worker leases one engine, feeds frames through `StreamOptions::default`, and emits only changed committed/tentative output. Audio and transcription use unbounded channels, so backlog can grow if inference falls behind. The exact `CommitPolicy::Auto` stability algorithm is in the external `transcribe-rs` dependency and was **not inspected here**; it is dependency-delegated, not verified ([channel/session path](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/transcription.rs#L98-L179), [load-while-queueing](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/transcription.rs#L768-L858), [stream feed](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/transcription.rs#L927-L973)).

**5. Partial transcript policy.** Each feed result contains committed and tentative text; Handy publishes only when either changes, and the overlay renders them separately. Update cadence is therefore driven by 30 ms frames plus inference/event delivery, not a fixed multi-second preview timer. Exact confirmation semantics remain unverified beyond the dependency boundary ([event emission](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/transcription.rs#L947-L965), [`RecordingOverlay`](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src/overlay/RecordingOverlay.tsx)).

**6. Finalization and insertion.** Stop uses streaming finalization where available or batch fallback. It waits for a concurrent WAV-save task, optional LLM cleanup, and configurable clipboard delays before insertion; these can sit on the final critical path. A 30-second finalization timeout avoids unsafe batch fallback while the worker may still own the engine ([stop/final path](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/actions.rs#L613-L763), [clipboard path](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/clipboard.rs#L611-L664), [timeout ownership guard](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/transcription.rs#L1057-L1085)).

**7. Hardware optimization.** Handy records requested and actually bound backend, supports whisper.cpp/ONNX-family engines, and enables Vulkan on Windows/Linux and Metal on macOS. The inspected Windows ONNX configuration is CPU-oriented; DirectML was not found. Quantization is model choice, not an automatic transformation ([backend binding](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/transcription.rs#L470-L704), [backend features](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/Cargo.toml#L72-L150)).

**8. Production resilience.** Source includes cached-device invalidation/retry, generation-based cancellation, VAD reuse, lazy mic closure, model-switch invalidation, panic containment, and later reload. Catalog models with expected hashes receive SHA-256 verification; custom models without a hash explicitly skip it. The principal scheduling risk is unbounded audio/worker channels ([audio recovery](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/audio.rs#L391-L585), [engine recovery](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/transcription.rs#L1187-L1389), [download hash verification](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/managers/model.rs#L1689-L1725)).

**9. Licensing.** Handy is MIT; copied source requires the copyright/licence notice. Its model catalog contains multiple terms, including MIT, Apache-2.0, CC-BY-4.0, other, and CC-BY-NC-4.0. The application licence cannot be generalized to models, Rust dependencies, backends, or the Silero weight ([`LICENSE`](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/LICENSE), [model catalog](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/src-tauri/src/catalog/catalog.json)).

**10. Transferable lesson.** Reuse design ideas: cache selected mic configuration, optionally keep it open briefly, capture while model readiness completes, lease one resident engine, publish committed/tentative text separately, and time pre-capture work. Do not port large Rust/Tauri modules into Mumble. Small experiments are a 30-second microphone/config keepalive and a bounded 30 ms PCM queue; never copy the unbounded queue or 1.65-second hangover without measurement.

### OpenWhispr at `ab201b3900caf582e9d70448414c83935fd7c595` (additional end-to-end candidate)

**1. Process and model lifetime.** OpenWhispr starts and reuses a persistent whisper.cpp HTTP server at app startup when the selected model exists. Startup is serialized by model/backend/thread/VAD signature. It explicitly stops/restarts the server after system sleep to reload GPU state. Its Parakeet/sherpa service is also serialized and persistent, runs a one-second silent inference after process readiness, and prewarms selected downloaded models at startup/after download ([Whisper startup](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/whisper.js#L97-L173), [serialized server reuse](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/whisperServer.js#L401-L450), [resume reload](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/whisper.js#L259-L292), [Parakeet warm-up](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/parakeetWsServer.js#L64-L191)).

**2. Audio capture.** Browser capture uses `getUserMedia`; a 250 ms `MediaRecorder` batch path starts before a 16 kHz AudioContext/AudioWorklet preview path. The worklet transfers fixed 800-sample/50 ms int16 blocks. Device IDs are cached and invalidated on `devicechange`, and `warmupMicDriver` briefly opens/closes the microphone. Starting the recorder first protects final batch audio from losing the beginning, although the incremental preview can start later ([recorder/worklet setup](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/audioManager.js#L752-L952), [worklet blocks](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/audioManager.js#L376-L418), [device cache](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/audioManager.js#L283-L374), [mic warm-up](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/audioManager.js#L697-L711)).

**3. VAD and endpointing.** Whisper-server defaults are threshold `0.5`, 250 ms minimum speech, 200 ms minimum silence, 30-second maximum speech, 100 ms padding, and 0.5 sample overlap, with bounds enforced before launch. Online Parakeet adds 600 ms tail audio to cover its stated 560 ms chunk; this is final-flush cost, not first-partial delay ([VAD defaults](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/constants/whisperVad.json), [VAD launch args](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/whisperServer.js#L125-L165), [Parakeet constants](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/parakeetWsServer.js#L17-L27)).

**4. Inference scheduling.** Two paths differ. Whisper preview sends disjoint accumulated PCM every 1.5 seconds if no request is active and RMS exceeds `0.002`; it has no overlap, retained decoder state, or stable reconciliation. Online Parakeet queues and forwards 50 ms worklet blocks through a websocket; pending chunks use an unbounded array with no explicit `bufferedAmount` backpressure. Offline Parakeet uses 0.5-second float32 blocks ([Whisper preview](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/ipcHandlers.js#L5893-L5994), [online websocket](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/ipcHandlers.js#L6620-L6700), [websocket queue](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/parakeetWsServer.js#L379-L518)).

**5. Partial transcript policy.** Chunked Whisper blindly appends each 1.5-second result, so it can produce boundary errors and cannot emit before timer plus inference. Online Parakeet retains finalized segment IDs and one replaceable trailing partial. Renderer updates follow changed websocket messages without another fixed throttle. This is stronger than blind append but depends on server final flags, not generic token-level stable-prefix confirmation ([segment/partial reducer](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/parakeetWsResult.js#L11-L54)).

**6. Finalization and insertion.** A clean, non-truncated online flush can skip full batch inference; otherwise OpenWhispr falls back to its complete-audio path. Whisper batch first converts recorded WebM to a 16 kHz mono temporary WAV with FFmpeg, putting conversion/disk I/O on final latency. Windows paste uses a small native helper that releases held modifiers, calls `SendInput` for Ctrl+V, waits, restores modifiers, and waits again ([clean flush/fallback](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/ipcHandlers.js#L6734-L6760), [FFmpeg conversion](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/whisperServer.js#L705-L778), [Windows native paste](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/resources/windows-fast-paste.c#L136-L228)).

**7. Hardware optimization.** The whisper server selects CPU, CUDA, or Vulkan binaries and chooses roughly 75% of logical CPU capacity, bounded to 4–12 threads. Startup and inference failures can fall through alternate GPU backends to CPU and retry once. Parakeet uses int8 encoder/decoder/joiner weights, up to four CPU threads, two workers, and a 2 ms loop. The explicit after-sleep GPU reload is especially relevant to consistency, but its latency/power effect remains unmeasured ([backend/thread selection](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/whisperServer.js#L16-L103), [fallback/retry](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/whisperServer.js#L450-L609), [Parakeet launch](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/parakeetWsServer.js#L86-L177)).

**8. Production resilience.** Observed features include device-change recovery, recording-segment salvage, mic warm-up, sidecar supervision, startup serialization, health polling, GPU-to-CPU fallback, retry after crash, completion timeouts, truncated-result marking, and batch recovery after an unclean stream. The model-download weakness is important: the Parakeet registry has expected sizes but no cryptographic hash, and an archive at least 90% of expected size may be accepted before extraction. Local modes can run offline after installation, but the product also contains cloud/account paths; this was not a complete telemetry audit ([audio recovery](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/audioManager.js#L1026-L1061), [server health](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/whisperServer.js#L620-L703), [registry](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/models/modelRegistryData.json#L1-L90), [download acceptance](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/helpers/parakeet.js#L277-L378)).

**9. Licensing.** OpenWhispr is MIT, but bundled whisper.cpp/sherpa-onnx/FFmpeg/Electron/native helpers and each model remain separate notice/term items. Its registry does not record model licences, so original model cards must be reviewed before Mumble ships weights ([`LICENSE`](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/LICENSE), [model registry](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/src/models/modelRegistryData.json#L1-L90)).

**10. Transferable lesson.** This is the closest end-to-end architecture reference: resident services, real silent warm-up, mic-driver warm-up, explicit resume rewarm, 50 ms online PCM, trustworthy-final-only-after-clean-flush, batch recovery, and separately timed native paste. Mumble already has stronger durable history and safe final paste; it lacks resume-specific inference health, mic prewarm/keepalive, true online helper state, and clean-stream/batch-fallback contract. Test each mechanism separately; do not copy the disjoint 1.5-second preview or unhashed model-download pattern.

### nerd-dictation at `41f372789c640e01bb6650339a78312661530843` with Vosk API at `e61c01d4968b6efe6abe72909860554a3eba1c24`

**1. Process and model lifetime.** `nerd-dictation begin` starts a long-running process, opens capture *before* importing Vosk and loading the model, then constructs one `vosk.Model` and `KaldiRecognizer`. Suspend finalizes/reset the recognizer and kills only the capture subprocess; the Python process and model remain resident under `SIGSTOP`. Resume restarts capture without reloading weights. A normal end exits the process; there is no timed unload cache ([`text_from_vosk_pipe`](https://github.com/ideasman42/nerd-dictation/blob/41f372789c640e01bb6650339a78312661530843/nerd-dictation#L928-L989), [`do_suspend_pause` / resume](https://github.com/ideasman42/nerd-dictation/blob/41f372789c640e01bb6650339a78312661530843/nerd-dictation#L1109-L1184)).

**2. Audio capture.** Capture is an external `parec`, SoX, or `pw-cat` subprocess producing 16-bit signed mono PCM. The default sample rate is 44.1 kHz; PulseAudio requests latency `10`, SoX uses buffer `1000`, and stdout is nonblocking. Because capture starts before Vosk import/model load, the first loop can read a large backlog accumulated during initialization—an explicit source-level implementation of “record first, load second” ([`recording_proc_with_non_blocking_stdout`](https://github.com/ideasman42/nerd-dictation/blob/41f372789c640e01bb6650339a78312661530843/nerd-dictation#L867-L925), [`main_begin` defaults](https://github.com/ideasman42/nerd-dictation/blob/41f372789c640e01bb6650339a78312661530843/nerd-dictation#L1269-L1298), [backlog comment and read loop](https://github.com/ideasman42/nerd-dictation/blob/41f372789c640e01bb6650339a78312661530843/nerd-dictation#L1215-L1233)).

**3. VAD and endpointing.** nerd-dictation delegates segmentation to `KaldiRecognizer.AcceptWaveform`: a true result means the recognizer's endpoint rules fired. The application can also end after unchanged recognition output exceeds a user timeout. Vosk's C++ `Recognizer::AcceptWaveform` internally splits received data into 200 ms steps, advances the incremental decoder, and calls `EndpointDetected`; its public API exposes endpoint modes and exact start/end/max delays ([`AcceptWaveform`](https://github.com/alphacep/vosk-api/blob/e61c01d4968b6efe6abe72909860554a3eba1c24/src/recognizer.cc#L395-L420), [`SetEndpointerMode` / delays](https://github.com/alphacep/vosk-api/blob/e61c01d4968b6efe6abe72909860554a3eba1c24/src/recognizer.cc#L225-L262)). nerd-dictation does not itself set those delays, so model defaults apply.

**4. Inference scheduling.** The recognizer retains an online feature pipeline, silence weighting, and incremental Kaldi decoder. The app repeatedly reads whatever bytes are available and calls `AcceptWaveform`; Vosk advances decoding in 200 ms waveform slices. Vosk's cleanup restarts the frontend/decoder after finalization or roughly ten minutes to bound frontend memory while keeping the referenced model alive ([`Recognizer` construction](https://github.com/alphacep/vosk-api/blob/e61c01d4968b6efe6abe72909860554a3eba1c24/src/recognizer.cc#L26-L49), [`CleanUp`](https://github.com/alphacep/vosk-api/blob/e61c01d4968b6efe6abe72909860554a3eba1c24/src/recognizer.cc#L154-L188)). There is no explicit application queue or backpressure cap; pipe buffering and optional loop idle time are the flow-control mechanisms.

**5. Partial transcript policy.** After every non-endpoint `AcceptWaveform`, nerd-dictation requests `PartialResult` but skips unchanged JSON. In progressive mode it computes the character-level common prefix with the text previously typed, backspaces the changed suffix, and writes the new suffix. In continuous mode each final result resets that edit baseline. This is low latency, but it can rewrite a user's target field on every revision and assumes simulated key ownership ([`handle_fn_wrapper`](https://github.com/ideasman42/nerd-dictation/blob/41f372789c640e01bb6650339a78312661530843/nerd-dictation#L1024-L1060), [`PartialResult` deduplication](https://github.com/ideasman42/nerd-dictation/blob/41f372789c640e01bb6650339a78312661530843/nerd-dictation#L1082-L1093)). Vosk obtains partial text from the decoder's current best path; it is provisional, not a stable-prefix guarantee ([`Recognizer::PartialResult`](https://github.com/alphacep/vosk-api/blob/e61c01d4968b6efe6abe72909860554a3eba1c24/src/recognizer.cc#L893-L913)).

**6. Finalization and insertion.** Endpoint results and the final flush pass through lightweight capitalization, numbers, and optional user Python configuration. Output can go to stdout or simulated typing through xdotool, ydotool, dotool, or wtype. Progressive revisions delete prior characters before typing replacement text. There is no clipboard snapshot/restore, target-field verification, or crash-safe transcript history. Mumble's final insertion is safer; nerd-dictation's progressive method should be treated as **ideas-only** for the Island, not copied into arbitrary target fields.

**7. Hardware optimization.** nerd-dictation contains no GPU/provider selection. Vosk is a native Kaldi online decoder and models are generally CPU-oriented, but SIMD/build optimization and model size live in Vosk/Kaldi packaging, not this app. The inspected app exposes sample rate, grammar restriction, and idle time rather than quantization or accelerator controls.

**8. Production resilience.** It handles rapid begin/end through a cookie timestamp, cancel/end through file state, empty `FinalResult` immediately after resume, suspend/resume signal races, multiple Linux input/output tools, and reloadable user configuration. Weak points for Mumble are platform scope, subprocess/device loss handling, unbounded pipe backlog during very slow inference, no model download/integrity workflow, no signed packaging, and no durable recovery.

**9. Licensing.** nerd-dictation is GPL-3.0 at the inspected revision ([`LICENSE`](https://github.com/ideasman42/nerd-dictation/blob/41f372789c640e01bb6650339a78312661530843/LICENSE)); direct copying into MIT Mumble requires a deliberate reciprocal-licensing decision. Vosk API is Apache-2.0 ([`COPYING`](https://github.com/alphacep/vosk-api/blob/e61c01d4968b6efe6abe72909860554a3eba1c24/COPYING)); Vosk models remain separate artefacts whose terms must be checked individually. The resident-process idea is not copyright-protected code and can be independently implemented.

**10. Transferable lesson.** Reuse the design idea of **capture-before-model-ready**, a resident model with explicit suspend/resume, unchanged-partial suppression, and a true incremental recognizer. Mumble already keeps faster-whisper resident and has safer final insertion; it lacks capture backlog during asynchronous readiness and live provisional UI. The smallest experiment is to timestamp and retain audio immediately when activation arrives even if STT readiness is still changing, then display Vosk-like provisional text only in the Island. Vosk itself is a possible weak-CPU benchmark, not the leading accuracy/default recommendation.

## Mumble implementation gap matrix

This matrix separates first-visible-text latency from final latency and throughput. “Reusable code” means the inspected licence is broadly compatible subject to notices and dependency/model review; “design idea” means independent implementation is safer or simpler.

| Candidate / difference | What Mumble already implements | What the inspected project does differently | Likely effect | Reuse class | Smallest safe Mumble experiment |
|---|---|---|---|---|---|
| faster-whisper coordinator | Resident faster-whisper model, real decoder/VAD warm-up, greedy settings, exact sample seams | Upstream itself is still whole-window; Mumble's four-second pieces neither overlap nor display | Smaller rolling windows plus stability could improve first visible and final tail; batching is throughput-only for this use | Existing code + design idea | Run 1.0, 1.5, and 2.0-second overlapping windows off recorded PCM; calculate two-hypothesis stable prefixes without changing paste |
| CTranslate2 lifecycle | One persistent model and CPU/CUDA fallback | Bounded worker queue, intra/inter thread controls, lazy CUDA handles/streams/allocator state; no cross-call decoder state or request cancellation | Exact warm-up/thread tuning may reduce cold variance; queue/batch tuning mainly throughput | Existing dependency; configuration reuse | Trace queue/lock/decode plus process age; sweep physical-core thread counts and one real warm inference on named CPU/CUDA PCs |
| whisper.cpp native backend | Whisper accuracy/model family and final insertion | Persistent native context; broad CPU/CUDA/HIP/Vulkan/Metal/SYCL/OpenVINO/Core ML backends; sample stream drops backlog and has no stability | Broader acceleration may improve inference/first partial on non-NVIDIA hardware; example chunking alone is unsuitable | MIT reusable code through sidecar/C API; stream policy ideas only | Long-lived helper on one AMD/Intel and one CPU-only machine, same model/corpus, no production routing |
| Whisper-Streaming LocalAgreement | Independent exact chunks and internal final reuse | Retranscribes a bounded rolling buffer; commits longest identical word prefix across two hypotheses; timestamp-aware seam dedupe | High first-visible benefit with added confirmation delay/compute; moderate final-tail benefit | Small MIT algorithm reusable with notice, or clean local implementation | Offline replay existing recordings; compare seam errors, revision count, first tentative and first stable times |
| SimulStreaming attention commitment | Greedy Whisper decode and model warm-up | Attention-edge token commitment, incomplete-word withholding, rewind rejection, CIF boundary model, exact warm-up | Potentially lower first-stable latency than two-pass agreement; not proven on Mumble | MIT source conceptually reusable; dependency-heavy implementation is high risk | Research-only replay prototype on strong GPU after the simpler LocalAgreement baseline |
| sherpa-onnx online recognizer | Dormant adapter/interface, packaged sherpa dependency, audio capture/fallback scaffolding | Persistent model-defined chunks and retained acoustic/decoder state; changed partials every ready chunk; decoder endpoints | Potentially very high first-visible and moderate final benefit; stream batching mainly throughput | Apache-2.0 code/API reusable; chosen model separate | One persistent int8 English helper, bounded 50–100 ms queue, 20–50 ms poll, Island-only tentative text |
| Handy warm audio lifecycle | Device fallback, stream per activation, resident model, safe final paste | Cached device/config, optional always-open/30-second lazy-close mic, capture during model load, committed/tentative overlay | Mic keepalive can improve activation-to-capture; partial UI improves first visible; hangover changes final only | Design ideas; Rust/Tauri modules unsuitable wholesale | A/B actual selected-device open with zero, 30-second, and always-ready policies; show explicit mic status and record power |
| Handy queue/final pipeline | Exact worker ownership, bounded 25-second wait, history before paste | Unbounded 30 ms frame channels; WAV and optional LLM may block insertion; 30-second ownership-safe finalize timeout | Queue can worsen tail under contention; moving optional work helps final/paste only | Anti-pattern plus design idea | Add queue-depth/age measurement first; benchmark persistence/format stages before moving any durable work |
| OpenWhispr readiness/recovery | Boot model warm-up, device enumeration, CUDA-to-CPU retry, sleep-gap state reset | Persistent sidecars, silent exact inference, mic-driver open/close warm-up, explicit GPU server reload after resume | High consistency/cold benefit if measured; no guaranteed steady-state speed gain | Design ideas; selected MIT helper code reusable with notices | On resume, run backend health + short inference; separately pre-open/close selected mic; compare next-dictation trace |
| OpenWhispr online final contract | Final-only target insertion with strong clipboard safeguards | 50 ms online sherpa frames; use streamed final only after clean flush, otherwise full batch fallback | High first-visible and potentially final benefit; recovery improves reliability | Design idea atop sherpa API | Mark online session complete only after explicit flush; inject truncation/timeout and prove existing full-audio fallback wins |
| OpenWhispr 1.5-second Whisper preview | Four-second disjoint hidden pieces | Shorter disjoint, visible appends, still without overlap/stability | Earlier first text but boundary duplication/loss risk; not a correctness-safe solution | Unsuitable as implemented | Use only as a negative-control benchmark against stable-prefix overlap |
| nerd-dictation/Vosk | Model residency, session guards, local-only option, safe final clipboard paste | Starts capture before model import/load; persistent suspended model; incremental Kaldi state; provisional common-prefix typing | Capture backlog helps cold readiness; Vosk can improve weak-CPU first partial; direct target revisions are risky | Vosk Apache code possible; nerd-dictation GPL code ideas-only by default | Record from activation even if model state is changing; show provisional text only in Island; benchmark Vosk as a weak-tier control |
| Production model supply chain | Mumble downloads/caches existing selected models and has a notice file | Handy verifies catalog hashes; SimulStreaming verifies official Whisper SHA-256; whisper.cpp/OpenWhispr examples have weaker checks | Reliability/security only, not steady-state latency; prevents corrupt/changed model surprises | Design idea | Pin repository revision and SHA-256 for one benchmark model; test interrupted/corrupt download recovery |
| Partial insertion ownership | Final clipboard insertion is serialized, restores modifiers/clipboard, and preserves durable history | nerd-dictation rewrites target text; Handy/OpenWhispr keep tentative text in owned UI | Island partials improve perceived latency without corrupting external edits | Reuse design boundary | Add tentative/committed Island renderer with no target-app mutation; test rapid revisions and cancellation |

## Concise comparison matrix

Ratings are planning judgements, not measured Mumble results.

| Candidate | First-result architecture | Likely latency impact | Mumble effort | Licence fit | Hardware fit | Main risk | Recommendation |
|---|---|---:|---:|---|---|---|---|
| Instrument current pipeline | Adds evidence rather than a new engine | Very high diagnostic value | Low | No new dependency | All | Measurement overhead if poorly designed | **Do first** |
| `faster-whisper` + stable streaming policy | Rolling/state-managed Whisper with confirmed prefixes | High | Medium | MIT | CPU + NVIDIA CUDA | Repeated decoding and partial revision | **Primary near-term path** |
| `sherpa-onnx` streaming model | Stateful transducer/CTC frames and endpointing | Potentially very high | Medium–high | Apache-2.0 engine; models vary | Broad CPU/ARM; GPU options vary | Accuracy/language/model fragmentation | **Benchmark second** |
| `whisper.cpp` | Native Whisper backend plus Mumble streaming policy | Medium–high | Medium | MIT | Broad CPU/GPU/Apple | Backend packaging and stable partials | **Benchmark for hardware coverage** |
| SimulStreaming | Newer simultaneous policy around foundation models | High if claims reproduce | High | Repository currently MIT; pin/review | GPU-favoured | Young project and dependency complexity | **Research spike, not first integration** |
| Handy components/ideas | Warm/cached mic, persistent leased model, queued 30 ms frames, committed/tentative UI | High for readiness/first visible if reproduced | High wholesale; low idea reuse | MIT app; dependencies/models vary | Cross-platform | Architecture mismatch and unbounded queues | **Copy lifecycle/UI ideas, not wholesale code** |
| OpenWhispr components/ideas | Resident sidecars, exact silent warm-up, mic prewarm, resume recovery, 50 ms online path | High consistency and first-visible potential | Medium–high | MIT app; dependencies/models vary | CPU/CUDA/Vulkan plus sherpa | Unbounded websocket queue and weak model hashes | **End-to-end reference; test mechanisms separately** |
| nerd-dictation/Vosk | Streaming recognizer in retained process | Medium on weak CPUs | Medium | GPL-3.0 app; Vosk pieces differ | CPU-friendly | Licence and accuracy/punctuation trade-offs | **Ideas only by default** |

## Ranked implementation shortlist

### 1. End-to-end tracing and a controlled cold/warm benchmark

- **Latency impact:** indirect but highest confidence; prevents optimizing the wrong stage.
- **Effort:** low.
- **Licence fit:** no new external component.
- **Hardware need:** all supported machines.
- **Risk:** low.

This must precede backend replacement. The first report should include a waterfall for every dictation and distributions by cold category, duration, model, and hardware—not only mean inference time.

### 2. Persistent streaming session with stable Island partials

- **Latency impact:** very high for perceived response and post-Stop tail.
- **Effort:** medium.
- **Licence fit:** implement natively from published ideas or reuse pinned MIT code with notices.
- **Hardware need:** current baseline works; benefits all tiers.
- **Risk:** medium, chiefly partial revision and boundary accuracy.

Start with the existing `faster-whisper` engine and a tested stable-prefix policy. Feed smaller audio increments, retain overlap/context, and distinguish unstable from committed text. Render committed and provisional text in the Island. Do not type provisional text into another application until edit/revision semantics are safe.

### 3. Exact-path warm-state manager

- **Latency impact:** high for first/infrequent dictation if measurements confirm runtime cold costs.
- **Effort:** low–medium.
- **Licence fit:** no new dependency.
- **Hardware need:** tiered memory/VRAM budgets.
- **Risk:** battery, heat, RAM/VRAM occupancy, and privacy if microphone residency is included.

Keep the selected model and VAD resident by default when within tier budget. Warm the exact production options and representative audio shapes, verify that the warm-up consumed the lazy generator, and record successful health. Compare CUDA lazy versus eager loading and real kernel warm-up rather than setting an environment variable blindly. Use an idle timeout only where resource measurements justify it.

### 4. Audio-engine readiness and microphone ownership redesign

- **Latency impact:** unknown to medium; instrumentation decides.
- **Effort:** medium.
- **Licence fit:** unchanged.
- **Hardware need:** audio-device-specific testing.
- **Risk:** exclusive-device conflicts, device changes, privacy expectations, and power.

At minimum, benchmark opening the actual selected stream instead of merely enumerating devices. An optional retained stream can provide immediate capture, but only with an obvious “microphone ready/active” state, a clear privacy explanation, and automatic recovery when the default device changes. Never conceal continuous microphone access.

### 5. Native backend benchmark: sherpa-onnx and whisper.cpp

- **Latency impact:** potentially high, especially on CPU-only and non-NVIDIA hardware.
- **Effort:** medium–high.
- **Licence fit:** good at engine level; each model remains a separate review.
- **Hardware need:** representative weak, average, strong, NVIDIA, AMD/Intel, and Apple machines.
- **Risk:** packaging, model quality, language support, larger test matrix.

Do not remove `faster-whisper` until a standard corpus proves a better latency/accuracy/resource point. The winning backend may differ by tier.

### 6. Move noncritical work outside the visible-text critical path

- **Latency impact:** low–medium median; potentially meaningful tail reduction on slow disks or busy systems.
- **Effort:** low–medium.
- **Licence fit:** unchanged.
- **Hardware need:** all.
- **Risk:** durability if asynchronous persistence is not carefully designed.

Paste rules-only Text as soon as output is ready, then complete UI refresh and noncritical statistics. History should use a fast durable enqueue or atomic journal before paste rather than risking lost dictation. Measure before moving anything.

## Fully local product direction

### Strong PC

- Recommend a larger accurate local model and the fastest proven native GPU backend.
- Keep STT, VAD, and required kernels resident.
- Produce stable partials continuously.
- Keep plain Text entirely local; use a local LLM only for deliberately selected shaping.
- Allow a “maximum accuracy” model when VRAM/RAM is sufficient.

### Average PC

- Default to a balanced model such as a base/small INT8 Whisper variant or a proven quantized streaming model.
- Keep STT resident but use a conservative memory limit.
- Show Island partials; insert only the final text.
- Offer a larger model as an informed download, not an automatic burden.

### Weaker PC

- Default to a tiny/quantized local model or a small true-streaming recognizer.
- Prefer bounded memory, fewer calibrated threads, and low-cost deterministic formatting.
- Offer four explicit choices rather than silently changing privacy:
  1. **Fastest local:** smaller model, lower accuracy ceiling.
  2. **Balanced local:** slower but more accurate.
  3. **Ask before cloud fallback:** sends only the disclosed clip after confirmation.
  4. **Always local:** never uploads, even if transcription is slow or fails.

Cloud should remain optional. A weak PC is not evidence that the user accepts uploading audio. A hybrid mode must show which provider receives audio, when fallback occurs, and whether the local model is currently available.

## Model selection and setup UX

On first setup, Mumble should probe hardware and run a very short local benchmark after the chosen model is downloaded. Present plain-language choices:

- **Fastest** — smallest download and memory use;
- **Balanced** — recommended for this computer;
- **Most accurate** — larger download and higher RAM/VRAM use.

Each option should show download size, approximate memory range measured on supported reference machines, language coverage, privacy boundary, and expected speed class. Avoid presenting an invented “PC class” as fact; show the detected hardware and the measured recommendation, with an override.

Model downloads should occur before the first dictation, with progress, size, checksum verification, pause/cancel, and resumable partial download. If the model is not ready, activation should explain that plainly rather than appearing to listen and then downloading on the critical path. Fallback must never silently change from local to cloud.

## Licensing, attribution, models, and branding

Open source does not mean “no conditions,” and one licence check is not enough. Mumble must review three separate layers:

1. **Application or example code** being copied, modified, linked, or launched.
2. **Runtime dependencies** included in the installer or loaded dynamically.
3. **Model weights, tokenizers, datasets, and model cards**, whose terms can differ from the engine.

### Practical obligations

- MIT components, including the inspected revisions of `faster-whisper`, CTranslate2, `whisper.cpp`, Handy, OpenWhispr, Whisper-Streaming, and SimulStreaming, require preservation of their copyright and permission notice in copies or substantial portions. The pinned evidence is: [faster-whisper](https://github.com/SYSTRAN/faster-whisper/blob/65882eee9f5cdbeeb2d877f1131d48cf241b327d/LICENSE), [CTranslate2](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/LICENSE), [whisper.cpp](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/LICENSE), [Handy](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/LICENSE), [OpenWhispr](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/LICENSE), [Whisper-Streaming](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/LICENSE), and [SimulStreaming](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/LICENCE.txt).
- Apache-2.0 components such as `sherpa-onnx` and Vosk API require the licence text, preservation of applicable notices and attribution, marking modified files, and attention to NOTICE and patent provisions. Review the pinned [sherpa-onnx licence](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/LICENSE) and [Vosk API licence](https://github.com/alphacep/vosk-api/blob/e61c01d4968b6efe6abe72909860554a3eba1c24/COPYING).
- GPL-3.0 code such as nerd-dictation carries reciprocal source and licensing obligations when copied into or distributed as a combined/derived work. Mumble may learn from its public architecture without copying protected expression. Direct reuse requires a deliberate project/legal decision and compliance plan. See the pinned [nerd-dictation licence](https://github.com/ideasman42/nerd-dictation/blob/41f372789c640e01bb6650339a78312661530843/LICENSE).
- A permissively licensed engine does not make every compatible model permissive. Record the exact model repository, revision, licence, required attribution, acceptable-use terms, and redistribution permission before offering or bundling it.

Mumble should maintain a shipped third-party notice file and an About-screen acknowledgement generated from a pinned dependency/model inventory. Notices are a compliance mechanism, not product branding.

Copyright permission also does not automatically grant trademark, logo, or product-name rights. Mumble can truthfully state that it uses a technical component and retain required attribution without renaming itself after that component, using its logo, or implying endorsement. Any public co-branding needs a separate trademark review.

This is engineering research, not legal advice. Components or model terms that are unclear, reciprocal, noncommercial, attribution-heavy, or changed since the reviewed revision require an explicit owner/legal product decision before implementation.

## Mumble benchmark plan

### Reference corpus

Use fixed, versioned recordings and physical-device sessions:

- durations: approximately 1, 3, 8, and 20 seconds;
- clean near-field, ordinary room noise, weak microphone, and competing system load;
- everyday prose, punctuation, numbers, personal vocabulary, first/last-word stress cases, accent variation, and supported foreign terms;
- scripted playback through a virtual audio device for repeatability;
- final confirmation with at least two physical microphones because driver and device-open latency cannot be validated virtually.

Run at least 30 trials for each important cell and randomize candidate order to reduce thermal/order bias.

### State matrix

- first run after reboot;
- first run after app launch and model-ready state;
- second, fifth, and twentieth consecutive dictation;
- idle intervals of 30 seconds, 5 minutes, and 30 minutes;
- AC and battery;
- Windows Balanced and Best Performance modes;
- idle machine, CPU contention, GPU contention, and low-memory pressure;
- each supported backend/model/tier;
- local Text, local deliberate AI mode, and separately disclosed cloud mode.

### Metrics

Report p50, p90, p95, and maximum—not only averages—for every pipeline span. Pair latency with WER, term error rate, clipped first/last words, hallucination rate, duplicated/missing seam rate, partial revision count, RAM/VRAM peak, five-minute idle CPU/GPU use, energy estimate, and thermal throttling indicators.

### Proposed targets

These are **proposal targets**, not current product claims. Validate and adjust them on named reference hardware.

| Metric | Strong PC | Average PC | Weaker PC |
|---|---:|---:|---:|
| Activation → Listening UI, p95 | ≤100 ms | ≤100 ms | ≤150 ms |
| Activation → first audio callback, p95 | ≤150 ms | ≤200 ms | ≤300 ms |
| First speech → first stable partial, p50 / p95 | ≤500 / 900 ms | ≤900 / 1,500 ms | ≤1,500 / 2,500 ms |
| Stable partial → Island visible, p95 | ≤100 ms | ≤100 ms | ≤150 ms |
| Stop → final raw text, p50 / p95 | ≤350 / 750 ms | ≤650 / 1,200 ms | ≤1,200 / 2,500 ms |
| Raw text → paste sent, rules-only p95 | ≤250 ms | ≤250 ms | ≤350 ms |
| Total Stop → paste sent, p95 | ≤1.0 s | ≤1.5 s | ≤3.0 s |
| Cold penalty after declared model-ready | ≤25% or 300 ms | ≤25% or 400 ms | ≤35% or 750 ms |
| Drift between 2nd and 20th warm trial | <15% | <15% | <20% |

The product-level goal should be that the first spoken words receive visible acknowledgement in under one second on capable hardware, while final insertion remains accurate and predictable.

### Warm-state resource guardrails

Also provisional:

- warm STT idle CPU should average no more than 1% over five minutes;
- record resident RAM and VRAM budgets by measured model rather than marketing name;
- planning ceilings are roughly ≤0.8 GB weak, ≤1.5 GB average, and a user-configurable ≤3 GB strong tier, pending real measurements;
- the retained microphone option must show a persistent privacy indicator and its measured power impact;
- fall back to a lighter local model before unloading/reloading repeatedly when that gives a better latency/resource balance.

## Architecture question checklist after the issue #3 decision

The issue #3 contract above resolves first-visible text as committed Island text, permits separately styled tentative Island revisions, keeps target-app insertion final-only, requires durable History before paste, defines a content-free local trace, and sets latency/accuracy/privacy/recovery release gates. Items 1–3, 13–14, and 20 below are therefore answered by this report. The other items remain inputs to later specification or owner decisions; keeping the checklist here prevents the duration/latency contract from being mistaken for a complete implementation specification.

1. What precisely counts as “first visible text”: provisional Island text, committed Island text, or text already inserted into the target application?
2. May provisional words revise, and how will revisions be shown without visual instability?
3. Will Mumble ever insert partial text into another application? If so, how will it avoid overwriting user edits, cursor moves, undo history, or unsupported fields?
4. Should the selected microphone remain open between dictations? What indicator, timeout, privacy explanation, and exclusive-device recovery are required?
5. What RAM and VRAM budget may a warm model occupy in each tier, and when may it unload?
6. Is the first implementation a stable-prefix Whisper session, a true streaming transducer, or both behind one backend interface?
7. What accuracy, punctuation, language, and terminology floor must every alternative model pass?
8. May backend/model selection change automatically after benchmark results, and what must be disclosed to the user?
9. What is the exact local failure policy? Smaller local fallback, wait and retry, user choice, or explicit cloud offer?
10. Which model files may Mumble download or redistribute, under which revisions and licences?
11. Where will third-party copyright notices, model attribution, modifications, and source offers live?
12. Will smart modes wait for final shaped text, or can Text paste immediately followed by a safe, user-approved transformation?
13. Which persistence work must complete before paste to guarantee no lost dictation, and which work can move after it?
14. What local telemetry schema and retention period are acceptable, and how can users export/delete it?
15. Which named Windows, macOS, and Linux machines form the supported benchmark tiers?
16. Must all platforms ship the same backend, or may each use a native implementation behind a shared contract?
17. How will model/helper updates be signed, verified, rolled back, and kept compatible with the packaged app?
18. What happens when the selected device disappears, the machine sleeps, the GPU resets, or power mode changes during dictation?
19. Are reduced battery life and higher idle memory acceptable for a “keep ready” option, and what is the default on battery?
20. What release gates prevent latency gains from shipping with worse WER, clipped words, hallucinations, or privacy regressions?

## Source-code inspection appendix

All upstream repositories were inspected from local clones at the commits below, then cited with permanent GitHub links. “Important files” lists the execution paths actually read; it is not a claim that every file in every repository was audited.

| Repository | Inspected revision | Important files and symbols inspected | Inspection result |
|---|---|---|---|
| Mumble | [`cde23725`](https://github.com/mongre25-droid/mumble/tree/cde23725e0d9eaeffe218e02c225ff5f8a499312) | [`Internal/app/mumble.py`](https://github.com/mongre25-droid/mumble/blob/cde23725e0d9eaeffe218e02c225ff5f8a499312/Internal/app/mumble.py): `start_recording`, `_open_input_stream`, `_audio_cb`, `_stream_worker`, `stop_recording`, `_process`, `_local_transcribe`, `_try_load`, `_warm_model`, `_paste_impl`; `Internal/app/transcription.py`; `Internal/app/formatting.py`; `Internal/app/platform/__init__.py`; `Internal/app/platform/windows_dml.py`; `Internal/app/perf/optimizer.py`; `Internal/app/settings.py`; `Internal/app/requirements.txt`; `Internal/app/THIRD_PARTY_NOTICES.md` | Complete trace of the currently called Windows dictation path, dormant adapter, warm-up, persistence, and paste path. One unrelated working-copy startup-cleanup edit was excluded from the analysis. |
| Mumble issue #3 revalidation | [`6f12ed73`](https://github.com/mongre25-droid/mumble/tree/6f12ed73fd9350692eab8c55b705f9910aae7e77) | Windows controller, duration constants, trace, History, stats, cloud transcription, tests and Web UI; macOS/Linux controllers, duration constants, cloud transcription, tests and Web UI; Astro homepage privacy and duration wording | Revalidated the current called Windows path plus both maintained port seams. The checkout has the ten-minute exact cap and an opt-in Windows trace, while retaining full float32 accumulation and four-second independent chunks. No uncommitted source change affected the pass. |
| faster-whisper | Mumble tag [`65882eee`](https://github.com/SYSTRAN/faster-whisper/tree/65882eee9f5cdbeeb2d877f1131d48cf241b327d) and current [`ed9a06cd`](https://github.com/SYSTRAN/faster-whisper/tree/ed9a06cd89a93e47838f564998a6c09b655d7f43) | `faster_whisper/transcribe.py`: `WhisperModel`, `transcribe`, window/fallback loops, `BatchedInferencePipeline`; `audio.py`: `decode_audio`; `feature_extractor.py`; `vad.py`: `VadOptions`, `get_speech_timestamps`, `SileroVADModel`; `utils.py`: `download_model`; `LICENSE` | Full reachable model/VAD/decode path. No microphone, token callback, stable prefix, or application lifecycle/recovery exists in this library. Exact Mumble tag and newer source were kept separate where VAD assets/defaults differ. |
| CTranslate2 | Current v4.8.1 [`0d8bcd36`](https://github.com/OpenNMT/CTranslate2/tree/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0); Mumble environment v4.8.0 [`54a546ce`](https://github.com/OpenNMT/CTranslate2/tree/54a546cec4262f9770d4674a0bfb4ac3c4f05698) | `python/cpp/replica_pool.h`, `python/cpp/whisper.cc`, `include/ctranslate2/replica_pool.h`, `src/models/whisper.cc`, `src/layers/transformer.cc`, `src/devices.cc`, `src/types.cc`, `src/models/model.cc`, `model_reader.cc`, CPU/CUDA utilities, `LICENSE` | Worker/queue, load/unload, decoder-cache, device/compute, lazy runtime and validation paths inspected. No source support found for Mumble's runtime “JIT-compiles kernels” wording. |
| whisper.cpp | [`080bbbe8`](https://github.com/ggml-org/whisper.cpp/tree/080bbbe85230f624f0b52127f1ae1218247989f9) | `examples/stream/stream.cpp`, `examples/common-sdl.cpp`, `examples/common.cpp`, `examples/server/server.cpp`, `include/whisper.h`, `src/whisper.cpp`, `examples/quantize/quantize.cpp`, `ggml/CMakeLists.txt`, Vulkan/Metal/OpenVINO backend sources, `models/download-ggml-model.sh`, `LICENSE` | Capture-to-decode example, core VAD/context, server reload/cancel, backend pipeline/cache and download paths inspected. The sample stream is not production dictation and lacks stable partials/device recovery. |
| sherpa-onnx | [`9d15a282`](https://github.com/k2-fsa/sherpa-onnx/tree/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e) | Python/C++ microphone examples; `microphone.cc`; `online-stream.cc`; `online-recognizer-ctc-impl.h`; `endpoint.h/.cc`; `silero-vad-model-config.h`; `voice-activity-detector.cc`; `provider.h/.cc`; `session.cc`; dependency CMake; `LICENSE` | Stateful online stream, batching, endpoints, VAD, provider/session configuration and example capture inspected. Application supervision, UI, downloads, backpressure and insertion remain integrator work. |
| Handy | [`8a362e9e`](https://github.com/cjpais/Handy/tree/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65) | `src-tauri/src/actions.rs`; managers `audio.rs`, `transcription.rs`, `model.rs`; recorder/resampler/VAD modules; `clipboard.rs`; `Cargo.toml/lock`; `catalog.json`; `RecordingOverlay.tsx`; `LICENSE` | End-to-end app lifecycle, CPAL capture, resampling, VAD, queue, overlay, finalization, download and recovery paths inspected. The external `transcribe-rs`/`transcribe-cpp` `CommitPolicy::Auto` implementation was not followed, so its exact confirmation algorithm remains dependency-delegated. |
| Whisper-Streaming | [`6da90b44`](https://github.com/ufal/whisper_streaming/tree/6da90b44b7e50d79695e68166d2a2c7609c75abb) | `whisper_online.py`: backend holders, `HypothesisBuffer`, `OnlineASRProcessor`, `VACOnlineASRProcessor`, factories/options; `silero_vad_iterator.py`; server/simulation entry paths; `LICENSE` | Rolling buffer, LocalAgreement confirmation, dedupe, trimming, VAD/VAC and lifecycle inspected. It is a research algorithm/server, not a complete desktop app. |
| SimulStreaming | [`077ea37d`](https://github.com/ufal/SimulStreaming/tree/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6) | `simulstreaming_whisper.py`; `simulstreaming/whisper/simul_whisper/simul_whisper.py`; vendored Whisper model loader; licence and package files | Exact warm-up, buffer/context, attention-edge commitment, CIF, rewind/incomplete-token logic, cache clearing, download verification and lack of VAD inspected. Published speed claim was not treated as measured proof. |
| nerd-dictation | [`41f37278`](https://github.com/ideasman42/nerd-dictation/tree/41f372789c640e01bb6650339a78312661530843) | `nerd-dictation`: capture subprocess creation, `text_from_vosk_pipe`, begin/end/suspend/resume, partial handler, progressive output; `LICENSE` | Linux capture-before-load, resident suspend, partial dedupe/rewrite and failure handling inspected. GPL source remains ideas-only by default for Mumble. |
| Vosk API | [`e61c01d4`](https://github.com/alphacep/vosk-api/tree/e61c01d4968b6efe6abe72909860554a3eba1c24) | `src/recognizer.cc`: construction, endpoint modes/delays, `AcceptWaveform`, `CleanUp`, `PartialResult`; `COPYING` | Incremental Kaldi state, 200 ms waveform stepping, endpoints, cleanup and provisional results inspected. Individual downloadable Vosk models were not selected or legally approved. |
| OpenWhispr | [`ab201b39`](https://github.com/OpenWhispr/openwhispr/tree/ab201b3900caf582e9d70448414c83935fd7c595) | helpers `audioManager.js`, `ipcHandlers.js`, `whisper.js`, `whisperServer.js`, `whisperVadConfig.js`, `parakeet.js`, `parakeetWsServer.js`, `parakeetWsResult.js`, `clipboard.js`; `whisperVad.json`; model registry; Windows paste helper; `LICENSE` | End-to-end startup, mic/worklet, preview/online, resume/fallback, clean final, insertion and downloads inspected. No full telemetry/data-retention audit was attempted; model registry terms remain incomplete. |

### Inspection gaps that remain deliberate

- No candidate was built, packaged, or latency-benchmarked on Mumble's target machines in this research-only task. Source-backed mechanisms are not performance results.
- Handy's external streaming dependency was not recursively inspected; its exact auto-commit algorithm is explicitly unverified.
- OpenWhispr was traced through the local Whisper and Parakeet paths, but not audited as a whole product for cloud telemetry, accounts, or retention.
- No specific sherpa, Parakeet, Vosk, or converted Whisper model has yet passed Mumble's accuracy, licence, redistribution, checksum, and packaging gates.
- OS/driver GPU clock scaling, microphone-driver cold behavior, power cost, and wake-from-sleep penalties cannot be proven from application source. They remain named measurement work.

## Recommended decision

For current 0.95, state the existing ten-minute limit plainly and direct longer recording to Meetings. For the target architecture, remove ten minutes as the normal foreground-dictation ceiling: keep one logical dictation running until the user stops across bounded queues and immutable local recovery segments, with sample/timestamp-based overlap merging, append-only committed prefixes, replaceable Island-only tentative text, exact-tail finalization, one durable History entry, and one final target-app paste.

Protect the longer-running design with a visible, configurable safety guard and measured storage/free-space/backpressure limits. Do not choose or market a default safety duration until physical tests and owner approval establish it. A reached guard or capacity limit must warn, preserve the exact prefix, stop safely, and remain recoverable.

Do not replace Mumble's transcription engine first. The verified immediate problem remains orchestration: short speech misses the four-second overlap, current “partials” are invisible independent chunks, the complete float32 recording remains resident, foreground crash recovery is not durable, and the trace does not yet classify every cold/warm state or prove UI/target visibility.

Extend and validate the content-free trace, then run the stable-prefix faster-whisper experiment against the defined fault, latency, accuracy, resource, and platform gates. Keep `faster-whisper` as the reference while running bounded `sherpa-onnx` and `whisper.cpp` comparisons on named weak, average, and strong machines. Adopt another backend only where measured latency, accuracy, resource use, packaging, recovery behaviour, platform support, and licence compliance are all better for a defined tier.

## Primary sources

### Mumble repository evidence

- [Issue #3 revalidation checkout](https://github.com/mongre25-droid/mumble/tree/6f12ed73fd9350692eab8c55b705f9910aae7e77)
- [Current Windows dictation controller](../../Internal/app/mumble.py)
- [Current recording limits](../../Internal/app/recording_limits.py)
- [Current privacy-safe trace](../../Internal/app/dictation_trace.py)
- [Current History persistence](../../Internal/app/history.py)
- [Existing Mumble optimization research](../Archive/Reports/Mumble%20Optimisation%20Research%20-%202026-07-10.html)
- [Cloud transcription implementation](../../Internal/app/transcription.py)
- [Deterministic transcript formatting](../../Internal/app/formatting.py)

### Upstream architecture and performance

- SYSTRAN, [faster-whisper repository and benchmarks](https://github.com/SYSTRAN/faster-whisper)
- OpenNMT, [CTranslate2 repository](https://github.com/OpenNMT/CTranslate2)
- OpenNMT, [CTranslate2 performance guide](https://opennmt.net/CTranslate2/performance.html)
- ggml-org, [whisper.cpp repository](https://github.com/ggml-org/whisper.cpp)
- k2-fsa, [sherpa-onnx repository](https://github.com/k2-fsa/sherpa-onnx)
- k2-fsa, [sherpa deployment framework introduction](https://k2-fsa.github.io/sherpa/intro.html)
- ÚFAL, [Whisper-Streaming repository](https://github.com/ufal/whisper_streaming)
- ÚFAL, [SimulStreaming repository](https://github.com/ufal/SimulStreaming)
- cjpais, [Handy repository and architecture](https://github.com/cjpais/Handy)
- OpenWhispr, [OpenWhispr repository](https://github.com/OpenWhispr/openwhispr)
- ideasman42, [nerd-dictation repository](https://github.com/ideasman42/nerd-dictation)
- Alpha Cephei, [Vosk API repository](https://github.com/alphacep/vosk-api)
- NVIDIA, [CUDA lazy loading](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/lazy-loading.html)
- NVIDIA, [CUDA guidance for latency-sensitive eager loading and warm-up](https://docs.nvidia.com/cuda/archive/13.1.1/cuda-programming-guide/03-advanced/advanced-host-programming.html)
- Microsoft, [processor performance control policies](https://learn.microsoft.com/en-us/windows/win32/power/processor-performance-control-policy-constants)
- Microsoft, [Windows thread Quality of Service](https://learn.microsoft.com/en-us/windows/win32/procthread/quality-of-service)
- Groq, [Speech-to-Text limits, preprocessing, and overlapping-chunk guidance](https://console.groq.com/docs/speech-to-text)
- OpenAI, [Audio API FAQ and legacy `whisper-1` upload limit](https://help.openai.com/en/articles/7031512-whisper-audio-api-faq)
- Python, [`os.replace` and `os.fsync`](https://docs.python.org/3/library/os.html#os.replace)
- Microsoft, [`ReplaceFile` semantics](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilea)
- Apple, [Application Support directory](https://developer.apple.com/documentation/foundation/url/applicationsupportdirectory)
- Apple, [microphone usage description](https://developer.apple.com/documentation/bundleresources/information-property-list/nsmicrophoneusagedescription)
- freedesktop.org, [XDG Base Directory Specification 0.8](https://specifications.freedesktop.org/basedir/)

### Licence texts

- [faster-whisper MIT licence](https://github.com/SYSTRAN/faster-whisper/blob/65882eee9f5cdbeeb2d877f1131d48cf241b327d/LICENSE)
- [CTranslate2 MIT licence](https://github.com/OpenNMT/CTranslate2/blob/0d8bcd362ac75ef860ef161d6f0efad0ae439ff0/LICENSE)
- [whisper.cpp MIT licence](https://github.com/ggml-org/whisper.cpp/blob/080bbbe85230f624f0b52127f1ae1218247989f9/LICENSE)
- [sherpa-onnx Apache-2.0 licence](https://github.com/k2-fsa/sherpa-onnx/blob/9d15a282aa79e60e0d4e5b68cdc0afb5d6b2ef9e/LICENSE)
- [Handy MIT licence](https://github.com/cjpais/Handy/blob/8a362e9eba59d4057fda79b7f38f5b0d5cbabf65/LICENSE)
- [OpenWhispr MIT licence](https://github.com/OpenWhispr/openwhispr/blob/ab201b3900caf582e9d70448414c83935fd7c595/LICENSE)
- [Whisper-Streaming MIT licence](https://github.com/ufal/whisper_streaming/blob/6da90b44b7e50d79695e68166d2a2c7609c75abb/LICENSE)
- [SimulStreaming MIT licence](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/LICENCE.txt)
- [Vosk API Apache-2.0 licence](https://github.com/alphacep/vosk-api/blob/e61c01d4968b6efe6abe72909860554a3eba1c24/COPYING)
- [nerd-dictation GPL-3.0 licence](https://github.com/ideasman42/nerd-dictation/blob/41f372789c640e01bb6650339a78312661530843/LICENSE)
