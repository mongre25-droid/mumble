# MUMBLE — MASTER SYSTEM DOCUMENTATION

> Single authoritative specification of both Mumble's approved target contract and its current source inventory.
> **Precedence:** normative sections `N0`-`N12` define what future implementation and release evidence MUST satisfy. The numbered source-inventory sections that follow describe the current code and are non-normative where they conflict with `N0`-`N12`.
> Scope: delivery, Windows lifecycle, release/VPS truth, dictation responsiveness, local/cloud routing, UI state, open-source compliance, architecture boundaries, data, and the existing product inventory.
> Version in source: `branding.VERSION = "0.9.1"` · App name: **Mumble** · Tagline: **"Speak. It types."**

---

# PART I — APPROVED NORMATIVE SPECIFICATION

## N0. STATUS, PURPOSE, AND CONFORMANCE

This part converts the completed [Mumble Wayfinder map](https://github.com/mongre25-droid/mumble/issues/1) into one buildable specification. It defines the outcome and the evidence required to claim it. It does not authorize a broad rewrite, public release, VPS deployment, paid service, or merge of another pull request.

Mumble's target is a reliable, easy-to-run, open-source, local-first application that starts reliably on Windows; has one correct launcher, shortcut, icon, and Flow identity; has one GitHub source/release authority; can promote an approved release to a VPS without dirty-checkout drift; provides prompt stable partial and final text; uses capable hardware locally; provides a smaller local route on weaker PCs; discloses cloud use; and satisfies exact open-source obligations.

### Normative language and evidence labels

- **MUST / MUST NOT** means required for conformance.
- **SHOULD / SHOULD NOT** is the default unless an evidence-backed exception is approved.
- **MAY** means permitted, not required.

| Evidence label | Meaning |
|---|---|
| **Existing evidence** | A named test, issue, commit, pull request, receipt, or measurement proves only the stated narrow claim. |
| **Implementation acceptance** | The behaviour must be built and pass its tests before Mumble may claim it. |
| **Release gate** | The exact approved release files must pass; cached/local tests are insufficient. |
| **Deferred external qualification** | The required environment was unavailable. This is an accepted limitation, never a pass. |
| **Prohibited claim** | Mumble must not make the statement under current evidence. |

Mumble is not qualified as `1.0`. No public hardware-wide latency SLA, signed publisher claim, installer-lifecycle certification, VPS parity claim, stable-partial shipping claim, or hardware-parity claim is permitted. Issues [#12](https://github.com/mongre25-droid/mumble/issues/12) and [#17](https://github.com/mongre25-droid/mumble/issues/17) are deferred, not passed. Open gates [#7](https://github.com/mongre25-droid/mumble/issues/7), [#8](https://github.com/mongre25-droid/mumble/issues/8), and [#9](https://github.com/mongre25-droid/mumble/issues/9) remain binding.

---

## N1. UBIQUITOUS LANGUAGE

UI copy, tests, manifests, deployment records, and support output MUST use these terms consistently.

| Term | Exact meaning |
|---|---|
| **Accepted Source** | Reviewed code merged into GitHub `main`. It may be newer than the latest release. |
| **Release Candidate** | One exact file set built once by required CI from one exact Accepted Source commit, with checks and provenance. |
| **Approved Release** | The exact verified candidate bytes explicitly owner-approved, bound to an immutable `vX.Y.Z` tag, and published as durable GitHub Release assets. |
| **Release Identity** | The SHA-256 fingerprint of the canonical signed release manifest. Equal version text alone is not identity. |
| **Installation Identity** | Approved Release identity plus the installed-file receipt for one Windows installation. |
| **Program Files** | Replaceable application files in the one active stable portable/source folder. |
| **User Data** | Preserved user-owned state outside the program folder, including settings, history, models, logs, and statistics. |
| **Canonical Launcher** | The one Mumble-owned launch identity whose executable, one app argument, working directory, and icon belong to the active installation. |
| **Ready** | Selected route usable, selected microphone available, activation registered, required health passed, and microphone capture off. |
| **On-device** | Active speech processing keeps microphone audio on the PC. Earlier model download is disclosed separately. |
| **Cloud** | The disclosed payload is sent to the named provider using the user's credential and consent. |
| **Stopped** | Dictation cannot safely start/continue and no undisclosed route change occurs. |
| **Tentative Partial** | Revisable text shown subdued and never inserted into another application. |
| **Stable Partial** | Text confirmed by the streaming stability policy, still subject to finalization. |
| **Final Text** | Finalized transcript after Stop and bounded processing. |
| **Saved** | Recoverable text is durably committed; insertion is not implied. |
| **Pasted** | Target insertion is confirmed by the best supported acknowledgement. |
| **Local Light** | Smallest approved pinned local profile for an unknown/weaker PC: `base.en` for English or `base` for multilingual use. It is not a hardware marketing class. |
| **Local Standard** | Explicit larger pinned `small.en`/`small` choice; never assigned solely from device name. |
| **Release Slot** | Immutable VPS directory containing one verified approved component release. |
| **Active Version** | Single atomic VPS pointer to one verified Release Slot. |
| **Deployment Record** | Append-only receipt connecting release, approval, slots, checks, health, and rollback. |
| **Parity** | Components share one Release Identity and each component digest matches its manifest entry. |

---

## N2. MODULES, INTERFACES, AND SYSTEM BOUNDARIES

The specification defines capability boundaries rather than brittle paths. Files MAY move if these contracts and tests remain stable.

| Module | Responsibility | Required boundary |
|---|---|---|
| **Release Authority** | Builds once, verifies, records provenance, receives owner approval, publishes exact bytes. | A merge, tag alone, local archive, rebuild, or temporary CI artifact is never a release. |
| **Release Identity Verifier** | Verifies manifest signature, tag, commit, role, length, digest, SBOM, notices, and compatibility. | Read-only verification is independently testable. |
| **Windows Installation Manager** | Owns the active stable folder, receipt, manual replacement, data boundary, and repair. | Program replacement and user-data deletion are separate. |
| **Launcher Reconciler** | Creates/verifies Mumble-owned Desktop, Start-menu, Startup, and uninstall shortcuts; removes exact legacy records. | Idempotent; never mutates unrelated records. |
| **Lifecycle Controller** | Owns single instance and Starting/Checking/Loading/Warming/Ready/Paused/Stopped. | UI consumes snapshots and cannot infer state from settings. |
| **Audio Capture Service** | Opens the selected mic only for active dictation and emits bounded PCM/device events. | Microphone ownership is separate from model warmth. |
| **Dictation Coordinator** | Session-scopes audio, inference, partials, finalization, save, paste, cancel, and recovery. | Stale async work cannot affect a new session. |
| **Transcription Backend** | Reports identity/capabilities; prepares; accepts audio; emits hypotheses; finalizes/cancels. | It does not decide cloud policy. faster-whisper/CTranslate2 is baseline. |
| **Partial Stabilizer** | Separates tentative/stable prefixes, revisions, and seam ownership. | Never inserts partial text into another app. |
| **Model Catalogue/Store** | Signed manifests, immutable source, hashes, licences, downloads, activation, rollback, removal. | Moving, partial, corrupt, or unclear files fail closed. |
| **Route/Consent Policy** | Chooses only approved On-device, Cloud, or Stopped states. | Local-to-cloud is never automatic; consent and capture are separate. |
| **Output/Recovery Pipeline** | Bounded formatting, durable save, target insertion, clipboard recovery, truthful result. | Saved precedes Pasted; uncertainty remains Saved. |
| **UX Projection** | Projects controller truth to Island, main window, tray, diagnostics, support. | No surface invents readiness, route, success, or performance. |
| **Performance Trace** | Emits private local activation-to-paste/resource evidence. | Off by default; no content, secrets, payloads, or personal paths. |
| **VPS Promotion Controller** | Transfers an Approved Release through protected approval to a restricted deployer. | Cannot self-approve or deploy checkouts/arbitrary inputs. |
| **VPS Slot/Health Manager** | Verifies, stages, activates atomically, probes, records, rolls back once. | Dirty development checkout is never involved. |
| **Compliance Inventory** | Produces exact artefact SBOM, licences, notices, provenance, and branding checks. | A hand-written summary alone cannot approve distribution. |

### Versioned interface records

- **ReleaseManifest:** product/version/tag/commit, owner approval, build run, asset roles/names/lengths/SHA-256/signatures or attestations, SBOM/notices/model manifests, compatibility versions.
- **InstallReceipt:** Release Identity, source-asset digest, installed-file digest, installer/repair version, install time, previous release, verification result.
- **ReadinessSnapshot:** lifecycle state, route, model/backend/device identities, microphone-open truth, activation truth, health reason, timestamp.
- **TranscriptHypothesis:** session, monotonic sequence, tentative/stable text, audio coverage, revisions, route/backend, timings. Ordinary logs redact text.
- **InsertionResult:** session, durable-save state, insertion attempt/acknowledgement, recovery location, user-visible outcome.
- **ModelManifest:** immutable repository/revision, allowed files, lengths/hashes, licence/provenance/notices, languages, runtime compatibility.
- **DeploymentRecord:** deployment ID, release/component identity, workflow/approver, prior/new slots, checks, activation, rollback, final health.
- **SanitizedStatus:** component, release fingerprint, state, health, last verification, prior known-good, GitHub Release link; no private infrastructure data.

### End-to-end flows

1. **Launch:** Canonical Launcher -> installation verification -> lifecycle checks -> local model health -> Ready snapshot.
2. **Dictation:** activation -> Listening -> PCM -> backend hypotheses -> Island partials -> Stop -> final -> processing -> durable save -> insertion -> Pasted or Saved.
3. **Release:** accepted `main` commit -> green required CI -> one candidate -> owner approval -> immutable tag/GitHub Release -> manifest/receipts.
4. **Deployment:** owner-selected Approved Release -> protected promotion -> VPS verification -> fresh slot -> health -> record or one rollback.

---

## N3. WINDOWS USER STORIES AND ACCEPTANCE CRITERIA

### WIN-01 — Reliable manual launch

**Story:** As a Windows user, I want to open Mumble from its supported entry point and reach truthful Ready or an actionable error without knowing its internal layout.

1. Given one supported stable installation, manual launch starts exactly one controller and shows the first named state within **500 ms p95** on each qualified Windows reference.
2. Thirty closed-process trials produce **30/30** Ready states or the expected injected error; no orphan, duplicate tray, hidden prompt, or stale working directory is allowed.
3. A second launch signals/reveals the authenticated existing instance in **20/20** trials and never kills or duplicates it.
4. The launcher resolves the intended Installation Identity and never guesses among old copies.
5. Missing/moved files, invalid receipt, or failed model health removes Ready and offers one primary repair action.

### WIN-02 — Reliable Windows-login startup

**Story:** As a user who enables Start with Windows, I want Mumble to start once from the same installation I launch manually.

1. The current-user Startup-folder shortcut is the sole supported login-startup owner.
2. Exact known Run-key/finalizer records are removed idempotently; cleanup and canonical shortcut creation are independent attempts with honest errors.
3. A disposable Windows matrix MUST achieve **20/20** login trials across enable/disable, repair, update, uninstall, and retained-data reinstall with at most one controller.
4. Startup never steals focus, opens the microphone, displays false Ready, or launches from a deleted experiment.
5. Until #12 passes, only normal-profile startup evidence may be stated; destructive lifecycle certification is prohibited.

### WIN-03 — Correct Flow result, shortcut target, and icon

**Story:** As a Flow Launcher user, I want one Mumble result with the real icon that opens the supported installation.

1. The Start-menu Mumble shortcut is Flow's sole supported discovery source; no second Flow database/plugin is needed.
2. The verifier checks normalized executable target, exactly one app argument, working directory, icon resource, Startup Apps state, path existence, and competing records.
3. Desktop, Start-menu, Startup, and uninstall shortcuts share one Installation Identity and icon source. Generic/deleted-test icons or mismatched paths fail.
4. After repair and Flow refresh, **20/20** launches resolve the canonical Start-menu result and same controller.
5. Fixtures with unrelated shortcuts/registry values remain byte-for-byte unchanged after repair.

### WIN-04 — Safe repair, replacement, and data preservation

**Story:** As a beginner, I want repair/update actions to preserve my data and retain a known route back.

1. User Data remains outside Program Files and is preserved by default.
2. Current supported replacement is manual: place an Approved Release in a stable folder, run setup/repair, verify Ready and shortcuts, then explicitly retire the old folder.
3. The known-good old folder remains until the replacement passes Ready and verification.
4. Automatic production updates remain disabled until signed update/publisher/lifecycle gates pass.
5. User-data deletion is separate, explicit, and irreversibly warned; developer checkouts are never auto-deleted.
6. Clean install/update/uninstall/reinstall/rollback/multi-user/elevation/antivirus/signing/publisher checks remain #12 gates.

---

## N4. RELEASE, VPS, HEALTH, ROLLBACK, AND PARITY STORIES

### REL-01 — Canonical accepted source

**Story:** As an owner, I want accepted code clearly separated from released code.

1. GitHub `main` is the only Accepted Source branch.
2. Merging advances source only; automated tests prove merge, push, tag creation, or branch movement cannot publish/deploy.
3. Dirty, detached, ahead, or unknown source is `Developer source — not an approved release`, even with matching version text.

### REL-02 — Approved immutable release

**Story:** As a user, I want the exact downloaded bytes to be the bytes that passed checks and received approval.

1. All required automated checks pass on one exact commit before candidacy.
2. CI builds once; publication promotes exact candidate bytes with no rebuild.
3. Owner approval, immutable tag, durable GitHub Release, commit/run, asset roles/names/lengths/hashes, provenance, SBOM, notices, and notes form one unit.
4. One changed byte/field/tag relationship/model hash/SBOM or notice digest fails verification.
5. Temporary artifacts, branch archives, local ZIPs, different website copies, and mutable tags are rejected.
6. `1.0` is prohibited until clean lifecycle, physical-device, signing, publisher, compliance, rollback, and supported-platform gates pass.

### REL-03 — Beginner-visible version truth

**Story:** As a user/supporter, I want to tell whether installation, GitHub, and VPS share one approved release.

1. About/Status shows version, Approved/Developer, short fingerprint, install verification, route, update state, and last check.
2. Full technical evidence is copyable without secrets/private infrastructure data.
3. States are exactly: `Current — approved and healthy`, `Behind — approved older release`, `Ahead — not approved`, `Changed locally`, `Unverified`, `Unhealthy`, `Rolled back`, `Unavailable`.
4. Stale/unavailable comparison never remains green.

### DEP-01 — Controlled GitHub-to-VPS promotion

**Story:** As the owner, I want deliberate deployment without server-side Git or manual copying.

1. A protected workflow is manually started with an owner-selected Approved Release tag/digest.
2. Protected production approval is explicit; automation cannot approve itself.
3. Durable Release bytes are verified before transfer through a restricted non-root deployer, then independently verified on VPS.
4. A fresh immutable slot is used; checkout, branch, rebuild, temporary artifact, arbitrary URL/file, and dirty Hostinger workspace are rejected.
5. Merge, tag, release publication, scheduled parity, or `latest` never auto-deploys.

### DEP-02 — Health-gated activation

**Story:** As the owner, I want an unhealthy candidate rejected and any post-activation failure detected.

1. Pre-checks verify identity/role, ownership, fresh slot, disk, permissions, configuration/schema/data compatibility, rollback slot, and an offline smoke test.
2. Only a passing slot activates through one atomic pointer change.
3. Post-checks verify pointer, process/static target, identity, one named safe functional operation, and sanitized endpoint when present.
4. **Three consecutive probes over at least 60 seconds** pass before healthy is recorded.
5. Missing required evidence cannot produce success.

### DEP-03 — Bounded rollback and drift

**Story:** As the owner, I want one safe rollback and visible drift.

1. Post-activation failure performs at most one automatic atomic rollback to the retained prior Approved Release and repeats health checks.
2. The receipt names failed identity/check, previous identity, rollback slot/time, restored evidence, final identity, workflow/trigger, and timestamps.
3. Failed rollback becomes `Unhealthy — manual recovery required`; no retry loop follows.
4. Active and latest known-good slots survive retention cleanup.
5. Scheduled parity is read-only; mismatch/missing/stale proof alerts but never deploys.

## N5. DICTATION READINESS, PARTIALS, FINAL TEXT, AND PERFORMANCE

The Lifecycle Controller owns: `Starting`, `Checking`, `Loading model`, `Warming`, `Ready · On-device`/`Ready · Cloud`, `Listening`, `Transcribing`, `Finalizing`, `Processing`, `Saving`, `Pasting`, `Pasted`, `Saved — paste failed`, `Paused`, `Stopped`, and `Needs action`. Short transitions MAY be visually quiet but MUST remain in the local trace. Invented progress is prohibited.

### DIC-01 — Truthful cold start and Ready

**Story:** As a user, I want to know when Mumble is genuinely ready before speaking.

1. For local mode, the selected faster-whisper model, exact decoder options, and VAD path complete representative health/warm work before Ready.
2. The model remains resident for process lifetime unless explicit Resource Saver or recorded memory pressure unloads it. Time-only idle unload is prohibited.
3. The microphone remains closed before/between dictations. Enumeration is not an open-stream claim.
4. Model/runtime/device change, sleep/resume uncertainty, backend failure, or unload invalidates Ready.
5. Trace classification distinguishes machine, process, model, runtime, and audio cold; idle-cooled; and warm. One `cold=true` is insufficient.
6. No absolute public cold-launch promise exists until named qualification. Each candidate still records p50/p90/p95/max time-to-Ready on every reference.

### DIC-02 — Immediate activation and capture

**Story:** As a user, I want immediate evidence that activation was received and the selected microphone opened.

1. `Activation -> Listening UI` and `Activation -> first audio callback` are separate monotonic spans.
2. Internal target A (normal local profile) is **<=100 ms p95** to Listening and **<=200 ms p95** to first callback.
3. Internal target B (Local Light on a named weaker reference) is **<=150 ms p95** and **<=300 ms p95**.
4. These are internal candidate targets, not public hardware-wide SLAs. Support requires a published machine, OS/power mode, model/runtime, mic method, corpus, and trial count.
5. Capture failure closes the microphone and enters Needs action; Listening cannot remain without callback evidence.

### DIC-03 — Stable first partial without unsafe insertion

**Story:** As a user, I want useful words while speaking without unstable guesses overwriting my work.

1. A persistent per-utterance session consumes bounded increments and preserves explicit overlap/context or native state. Hidden independent four-second chunks do not qualify.
2. Tentative and stable text are distinct; revisions affect tentative content only; stable seams cannot duplicate/drop words.
3. Partials appear only in Mumble. They are never pasted/typed into the target app.
4. `First speech -> first stable partial` target A is **<=900 ms p50 / <=1,500 ms p95**; target B is **<=1,500 ms p50 / <=2,500 ms p95**.
5. `Stable partial -> Island render acknowledgement` is **<=100 ms p95** (A) and **<=150 ms p95** (B).
6. At least 30 randomized trials per important corpus/duration/state cell co-measure WER, term error, first/last clipping, hallucination, seam loss/duplication, and revisions.
7. Current four-second chunks and prototypes are evidence only. No visible-partial claim is allowed until this passes.

### DIC-04 — Responsive final text and truthful insertion

**Story:** As a user, I want prompt final text after Stop without lost words or false paste success.

1. `Stop -> final raw`, `raw -> durable save`, `raw -> paste sent`, and total Stop-to-insertion are separate spans.
2. Rules-only local target A: Stop-to-final **<=650 ms p50 / <=1,200 ms p95**, total Stop-to-paste-sent **<=1,500 ms p95**.
3. Target B: Stop-to-final **<=1,200 ms p50 / <=2,500 ms p95**, total **<=3,000 ms p95**.
4. Raw-to-paste-sent is **<=250 ms p95** (A) and **<=350 ms p95** (B), excluding separately disclosed optional AI shaping.
5. Recoverable text is durable before Pasted. Save failure blocks normal insertion and exposes a warned copy-from-memory recovery.
6. Clipboard set, insertion, acknowledgement capability, and restoration are separate facts.
7. Failed/uncertain insertion is `Saved — paste failed` with Copy/Open history/Retry, never Pasted.

### DIC-05 — Warm consistency and regression prevention

**Story:** As the owner, I want apparent speed gains rejected if they worsen first use, accuracy, privacy, durability, or long sessions.

1. Runs include first use, uses 2/5/20, and 30-second/5-minute/30-minute idle-cooled states.
2. After Ready, use 1 versus median 2/5/20 blocks for investigation when penalty is both **>25% and >300 ms**.
3. A critical p95 blocks for investigation when both **>15% and >100 ms** slower than same-machine baseline.
4. Repeated `RTF > 1` or rules-only Stop-to-paste over **3 seconds** prompts Local Light/diagnostics but never changes route.
5. A speed gain fails if accuracy, terminology, clipping, duplication, hallucination, persistence, paste, privacy, memory, or power crosses its bound.
6. Candidate build, model hash, runtime, device, corpus, power state, and trace version are recorded. Cached tests do not prove clean CI.

### DIC-06 — Failure recovery

**Story:** As a user, I want device/sleep/model/route failures to preserve my words and explain what happened.

1. Device loss, sleep/resume, backend reset, and model error invalidate affected readiness with a reason.
2. Safe captured audio is preserved, stale results rejected, and local reload attempted once.
3. Recovery order is same local profile -> Local Light -> stopped repair/download -> separately consented Cloud if configured.
4. Every error states failure, capture truth, egress truth, preservation truth, and primary safe action.

---

## N6. LOCAL MODELS, HARDWARE, PRIVACY, AND CLOUD

### MOD-01 — Local-first support on ordinary and capable hardware

**Story:** As a capable-PC user, I want full local dictation without a cloud account.

1. faster-whisper/CTranslate2 remains baseline; CPU INT8 is conservative supported compute until exact accelerator qualification.
2. After approved acquisition, local dictation works with all provider endpoints/key checks/telemetry/background model updates blocked.
3. NVIDIA CUDA MAY be offered only after exact packaged runtime probe, warm health, inference, recovery, packaging, and named hardware tests. It never auto-selects a larger model.
4. AMD/Intel integrated graphics are not called supported CTranslate2 GPU acceleration under current evidence; CPU local remains.
5. UI reports actual backend/device/compute from controller truth, not marketing tiers.

### MOD-02 — Deliberate weaker-PC behaviour

**Story:** As a weaker/overloaded-PC user, I want a smaller local experience before cloud is offered.

1. Existing installations keep their selected approved model. New/unknown Windows starts with pinned `base.en`/`base` Local Light according to language need, not device label.
2. `small.en`/`small` Local Standard is explicit with measured speed/memory/accuracy information; never auto-assigned.
3. Larger models and alternative runtimes remain non-default until licence, manifest, accuracy, memory, packaging, and hardware gates pass.
4. Resource Saver is explicit, visible, reversible, retains Local Light where possible, and disables only optional background work.
5. Poor performance recommends Local Light/diagnostics and never silently enables cloud.

### MOD-03 — Verified model acquisition and recovery

**Story:** As a beginner, I want model setup understandable, cancellable, and safe from corrupt or unclear files.

1. Before download, UI shows profile/model, language, exact size, source, licence/provenance, local privacy, storage category, and speed/accuracy trade-off.
2. Download uses immutable revision and app-owned manifest; writes temporary data; supports progress/cancel/retry; verifies length/hash; activates atomically.
3. Mismatch, missing/extra/partial file, moving branch, missing notice, unclear licence, low disk, or incompatibility preserves last-known-good and explains recovery.
4. Large weights are first-run downloads or separately verified offline packs, not silent base-archive growth.

### PRIV-01 — Persistent route truth and explicit cloud consent

**Story:** As a privacy-conscious user, I want to know before/during/after dictation where audio is processed.

1. `On-device`, `Cloud`, or `Stopped` remains visible before capture, during dictation, and at completion/recovery.
2. Before first Cloud session/provider change/material term change, Mumble names provider, exact payload, credential owner, possible billing, retention/training/region facts, and stop control.
3. Consent is session-only by default. Remembering Cloud is a separate setting and route stays visible.
4. Enabling Cloud and opening microphone are separate. Missing key, unknown charges, or incomplete disclosure keeps Cloud unavailable.
5. Forced-local-failure tests observe zero cloud requests until checkpoint acceptance. A post-switch toast is not consent.
6. Cloud failure stops further transmission, records what was sent, preserves recoverable material, and offers local recovery without another provider switch.

---

## N7. READINESS, MODEL, MICROPHONE, ERROR, UPDATE, AND STATUS UI

| Surface | Must show | Must not become |
|---|---|---|
| **Island** | Current action, route, microphone-on truth, qualified partials, one short recovery action, brief Pasted/Saved. | Permanent setup rail, raw dashboard, package list, fabricated partials. |
| **Main window** | Mic/model/profile/integrity, route/privacy, startup health/repair, download/recovery, Resource Saver, history recovery, update verification. | Settings-only route disclosure absent during dictation. |
| **Tray** | Ready/Paused/Needs action, route, Open, Pause/Resume, Repair, Exit. | Transcript content, stack trace, benchmark claims. |
| **Diagnostics** | Exact app/release/model/runtime/backend/device IDs, transitions, verifier, content-free trace, export/delete. | Automatic telemetry or content/secrets/payloads/personal paths. |

### UX-01 — Clear readiness and microphone state

1. Use concrete copy: `Loading local model`, `Warming local speech engine`, `Ready · On-device`, `Listening · microphone on`, `Finalizing`, `Stopped — model needs repair`.
2. Ready means mic available/off; Listening means open with callback evidence.
3. Controller changes reach visible surfaces within **250 ms p95**.
4. Default UI stays quiet; detail expands only for waits, setup, diagnostics, or action.

### UX-02 — Actionable errors and recovery

1. Model, accelerator, mic, inference, Cloud, save, paste, moved install, update, and receipt failures map to named error codes and tested recovery.
2. Each error states failure, capture/egress/preservation truth, one primary action, and at most one compact Retry.
3. Fault tests never observe false Ready, Offline/Private, Saved, Pasted, Approved, Current, or Healthy.

### UX-03 — Understandable update/version state

1. States: `No approved feed configured`, `Checking`, `Current`, `Approved update available`, `Behind`, `Unverified`, `Failed`, `Rolled back`.
2. Before activation show current/new Release Identities, GitHub source, verification, data preservation, rollback.
3. Unsigned/mismatched/mutable/unavailable/incomplete updates cannot install or appear green.
4. Until a qualified updater exists, direct users to the manual Approved Release procedure.

---

## N8. OPEN-SOURCE, LICENSING, ATTRIBUTION, AND BRANDING

### OSS-01 — Fail-closed adoption

**Story:** As the owner, I want open-source leverage without unknown distribution/privacy/patent/model obligations.

1. Every production dependency, model, binary, asset, font, dataset, and copied fragment is pinned and mapped to intended use/distribution.
2. Permission, copyright, licence text, NOTICE, modifications, patents, source/relinking, provenance, privacy, packaging, security, and removability are recorded.
3. Exact built artefact generates SBOM and notices; a hand-written summary is insufficient.
4. Each downloaded file has immutable source, length, hash, provenance, licence/model-card links, and notices in a signed manifest.
5. Strong-copyleft, custom/source-available, non-commercial, behavioural, auto-changing, unclear, unofficial-binary, or mismatched terms remain research-only pending clearance.
6. One omitted notice, changed binary, unpinned package, unknown model, or incomplete obligation fails release.

### OSS-02 — Candidate-specific boundaries

1. faster-whisper/CTranslate2 is the MIT-notice baseline; transitive runtime/native/model obligations are separate.
2. whisper.cpp may be benchmarked under MIT; sherpa-onnx under Apache-2.0 obligations. Neither is production fallback without full qualification.
3. SenseVoice remains research-only. Distil-Whisper provenance, FFmpeg/PyAV contents, unofficial GPU DLL bundles, and redistributed vendor runtimes remain specialist gates.
4. Engine code and model weights/training provenance are reviewed separately.

### OSS-03 — Independent Mumble branding

1. Another project's name/logo/icon/art/screenshots/colours/slogans/trade dress/model branding never becomes Mumble identity.
2. Required upstream names appear factually and subordinately in About/notices/diagnostics/model details.
3. Required notice text remains verbatim and visible offline.
4. Asset inventory/snapshots detect copied upstream branding.

## N9. INSTRUMENTATION, TEST SEAMS, AND ACCEPTANCE SUITES

`mumble.dictation-trace.v1` is the normative performance evidence source. It MUST be opt-in, local-only, bounded, fail-open for product use, exportable, and deletable. It excludes audio, transcript/partial words, clipboard text, prompts, credentials, provider payloads, personal paths, and exception messages.

Required spans are activation-to-Listening; activation-to-first callback; first callback/speech-to-tentative/stable partial; stable-partial-to-render acknowledgement; Stop/end-of-speech-to-final; queue/lock/inference milestones; raw-to-format/save/clipboard/paste/acknowledgement; cold/warm reason; and safe resource/queue/underflow/RTF/power data.

### Required testing seams

| Seam | Controlled conditions | Required proof |
|---|---|---|
| **Clock** | Monotonic time/deadlines. | Deterministic state/latency tests without sleeps. |
| **Audio capture** | Devices, callbacks, deny/busy/disconnect, underflow/overflow, sleep. | No false Listening; no clipped edge; recovery. |
| **Virtual speech playback** | Versioned corpus/timing/noise. | Repeatable WER, partial, seam, final latency. |
| **Physical microphones** | At least two devices. | Device-open, clipping, permission, callback qualification. |
| **Backend fake** | Delayed/revising hypotheses, pressure, crash/cancel/capability changes. | Stability, stale-session rejection, recovery, final-only paste. |
| **Egress sentinel** | Every network/provider attempt. | Local has no speech/provider egress; Cloud requires consent. |
| **Model store** | Partial/corrupt/wrong-hash/missing-notice/incompatible/low-disk. | Last-known-good and atomic activation. |
| **Durable store** | Disk full, permissions, crash, concurrent access. | Saved truth and no lost transcript. |
| **Insertion adapter** | Editable/non-editable, focus move, clipboard contention, no acknowledgement. | Pasted/Saved truth and restoration. |
| **Windows shell** | Shortcuts, Startup, registry, Flow, icons, move/delete. | Allow-listed idempotent repair. |
| **Release verifier** | Tag/commit/assets/signature/SBOM/notices/models. | One-byte/one-field tamper rejection. |
| **Deployment target** | Dirty/fresh slots, pointer/health/rollback/retention failures. | Atomic activation, one rollback, receipt, no checkout mutation. |

### Acceptance suites

1. **Fast contract suite** on every change.
2. **Clean isolated application suite** using the authoritative offline runner plus fatal lint/static/security gates.
3. **Windows lifecycle suite** in a disposable profile/VM across install, repair, login, Flow, update, uninstall, retained-data reinstall, rollback.
4. **Dictation benchmark** with >=30 randomized trials for important cells across 1/3/8/20-second speech, clean/noisy/weak-mic/terminology/accent/language cases, cold/warm/idle, power/contention, and supported routes.
5. **Release artefact suite** for clean-build SBOM/notices, manifests/hashes, package layout, signing/publisher, antivirus, installed receipt.
6. **Deployment rehearsal** for slots, health, drift, failed activation, rollback, status, and redaction.
7. **Real-platform suite** on named Windows hardware and macOS/Linux devices before support claims.

---

## N10. TRACEABILITY

| Area | Primary decision/evidence |
|---|---|
| Source/release | Wayfinder #3; repository/GitHub audit; open CI gate #7. |
| VPS | #4, research #13, preserved evidence #14/closed unmerged PR #24, parity/health/rollback #15. |
| Windows | Installer research #11; startup repair evidence in closed unmerged PR #10/`583dad51`; contract #2; deferral #12. |
| Latency | Dictation research report; trace #16/closed unmerged PR #25; pilot/deferral #17; policy #6. |
| Readiness/route UX | Prototypes #18/#20; final UX #21. |
| Models/fallback | Candidate research #19; decision #5; deferral #17. |
| Open source/branding | #19 and #22. |
| Spec boundary | #23. |

---

## N11. IMPLEMENTATION ORDER

1. Release identity foundation: green CI, build-once candidate, manifest, inventory/SBOM/notices, read-only verifier.
2. Windows identity/repair: portable identity, launcher reconciliation/verifier, manual replacement, external data.
3. Pinned local readiness: model manifests/download integrity, faster-whisper health, Local Light/Standard, route projection.
4. Session/durability: lifecycle states, session IDs, audio events, recovery, durable save, truthful insertion, trace.
5. Stable partials: backend contract, bounded queue, stabilizer, Island tentative/stable projection, final-only insertion.
6. Quiet Centre UX: real snapshot-driven main/tray/diagnostics, cloud checkpoint, action-led failures.
7. VPS promotion only after an Approved Release and concrete deployable component/functional probe exist.
8. External qualification: disposable Windows lifecycle, named hardware/microphones, signing/publisher, macOS/Linux.

Each slice is independently reviewable, preserves data, keeps route changes explicit, remains reversible, and cannot claim a later slice's outcome.

---

## N12. OUT OF SCOPE AND DEFERRED GATES

This specification publication does not implement/refactor production code; merge PR #10/#25; publish/move a release; deploy/clean the VPS; enable update/cloud billing; enter/buy credentials; adopt another engine; copy branding; or promise platform/hardware parity.

Deferred qualification remains:

1. **#12 Windows lifecycle:** clean install, destructive update/uninstall, retained-data reinstall, rollback, transition stale-path cleanup, multi-user/elevation, antivirus, signing, publisher.
2. **#17 hardware/devices:** named weak/modest/strong references, NVIDIA/non-NVIDIA paths, natural/accent/noise/multilingual corpus, two physical mics, power/thermal/contention, target paint acknowledgement, real partials, alternative engines.
3. **#7/#8/#9:** reproducible clean CI, release signing/publisher/compliance, and real macOS/Linux evidence.

These gates limit which implemented behaviours may be called supported/released; they do not leave a product decision unresolved.

---

# PART II — CURRENT SOURCE INVENTORY (DESCRIPTIVE)

The remaining sections preserve the detailed source-derived inventory for version `0.9.1`. They explain what exists today. Where current behaviour—such as automatic model tiers, hidden four-second chunks, automatic route fallback, or update assumptions—conflicts with Part I, it is implementation debt and MUST NOT override the normative contract.

---

## 0. EXECUTIVE SYSTEM OVERVIEW

### What Mumble is (current definition)
Mumble is a **cross-platform (Windows-first) desktop voice-to-text application** that turns spoken audio into clean, optionally AI-shaped text and **pastes it into whatever window the user is focused on**. It runs continuously in the background as a system-tray app with a floating "island" status overlay, a global hotkey to start/stop dictation, and a rich web-based main window (the **Deck**) for history, statistics, reading, and settings.

It is functionally inspired by **Wispr Flow**. It pairs **local speech recognition** (faster-whisper / CTranslate2) with **cloud or local LLM post-processing** (Cerebras `gpt-oss-120b` by default) and degrades gracefully to a **deterministic offline formatter** when no AI is available.

### Core purpose
Replace typing with speaking — anywhere on the OS — while producing text that reads as if it were carefully written, not dictated. The product's thesis: *transcription is a solved commodity; the value is in the cleanup, the modes, the context, and the zero-friction paste.*

### Primary user value proposition
1. **Global dictation** — press a hotkey, speak, press again; text appears in the active app.
2. **Clean output** — fillers removed, punctuation/capitalisation restored, Whisper hallucinations/loops stripped.
3. **Smart Modes** — the same speech can become a polished **Prompt**, **Email**, **Reply**, **Foreign**-aware text, or plain **Text**. (The dedicated **List** mode was retired — the text/email lanes bullet dictated lists natively.)
4. **The Deck** — a searchable history of transcripts, clipboard captures (text + images), and generated prompts, with favourites, presets, and AI "jobs" over selected items.
5. **Reader Mode** — paste any document and have it read aloud with AI TTS voices, with resumable position, bookmarks, collections, and reading stats.
6. **Privacy posture** — audio is transcribed locally by default; only text (never API keys) optionally syncs to the cloud.

### High-level system architecture
Mumble is a **two-process desktop app** plus background workers:

```
┌──────────────────────────────────────────────────────────────────────┐
│  CONTROLLER PROCESS  (mumble.py — the orchestrator)                    │
│   • Tk root (hidden) + main event pump (_pump)                         │
│   • System tray icon (pystray)                                         │
│   • Floating Island overlay (overlay.py / island_render.py)            │
│   • Global hotkeys + mouse buttons (bindings.py → keyboard/mouse)      │
│   • Mic capture (sounddevice) → faster-whisper (local) / cloud STT     │
│   • AI dispatch (ai.py → Cerebras/OpenAI/Anthropic/…/local LLM)        │
│   • Offline formatter (formatting.py, model_free.py, local_engine.py)  │
│   • Persistence: settings/history/clipboard/stats/favorites/presets/…  │
│   • Command server on 127.0.0.1:49519 (token-authenticated)           │
│   • Single-instance lock on 127.0.0.1:49517                            │
└───────────────▲───────────────────────────────────────────┬──────────┘
                │  JSON-line IPC (token auth)                 │
                │  CMD_PORT 49519  ◄────────────────────────► │ WEBUI_PORT 49520
┌───────────────┴───────────────────────────────────────────▼──────────┐
│  WEB UI PROCESS  (webui_shell.py — pywebview + Edge WebView2)          │
│   • Hosts webui/index.html + app.css + enhanced.css + app.js          │
│   • window.pywebview.api.*  →  Python Api bridge (~120 methods)        │
│   • Screens: Home · Deck(History) · Stats · Reader · Settings          │
│   • Talks to controller for live status, paste, record, deck jobs      │
└───────────────────────────────────────────────────────────────────────┘

Cloud (optional):  Supabase (auth + sync)  ·  LLM providers  ·  TTS providers
Update feed (optional):  hosted update.json manifest (SHA256 + optional ed25519)
```

A **fallback classic Tk window** (`app_window.py`) is used only when pywebview/WebView2 is unavailable. A lighter snapshot, **Mumble Lite**, can be launched in its place.

---

## 1. PRODUCT PHILOSOPHY & DESIGN PRINCIPLES

These are *implicit rules* extracted from the code, comments, and owner directives embedded in source.

### UI philosophy — "Golden Black"
- A single warm-gold accent (`#D4AF37` native / `#EBC35C` web) on near-black, warm-tinted surfaces. Gold means *brand, action, primary*. Functional colours (red/amber/green) are used *only* for status meaning.
- **Per-mode colour identity** is consistent everywhere — the island pill, the mode chips, the Deck rows, and the stats breakdown all light up in the mode's colour (Prompt = purple, Email = blue, Reply = coral, Foreign = sand, Convert = rose, Text = gold). *(List, formerly teal, was retired.)*
- **Glass surfaces**: layered gradient + inset highlight (top) + inset shadow (bottom) + faint gold outer rim. Used uniformly for cards, rows, tiles, the island, and the mode bar.
- **Content-hugging**: the island pill is sized to its content (no phantom padding); the main window lazy-builds heavy sections.

### Interaction philosophy
- **Zero focus theft**: the island is click-through and never activates (`WS_EX_NOACTIVATE | WS_EX_TRANSPARENT`), so dictation focus stays in the user's app. The Deck can run as a non-activating palette so clicking it never steals focus either.
- **The hotkey is the product**: press to start, press to stop, text pastes. Everything else is secondary.
- **Never lose the user's words**: ambiguous mode detection shows a picker that *auto-resolves to plain text after a timeout* rather than discarding input; failed AI calls fall back to the offline builder and never dump raw material.
- **Sticky toggles over held keys** ("The Big Shift"): the old held-Right-Shift "mode key" was retired; Prompt is now a sticky island toggle.

### Motion / animation philosophy
- **Premium, breathing, never jarring.** Transient elements (hints, suggestion chips) *dissolve* over their final frames instead of snapping off.
- **Respect attention**: island animation pauses when the main window is unfocused and resumes from the exact same frame counter (no jitter).
- **Performance-tiered**: three visual tiers — `lite` / `standard` / `enhanced` — plus a `resource_saver` mode and `body.paused` state that halt ambient animations.

### Accessibility approach
- `prefers-reduced-motion` and `body.paused` disable ambient animation.
- High warm-cream text on black for contrast (`--text #F4EFE3`).
- Responsive scale-down (`applyResponsiveScale`) shrinks the UI on narrow windows but never scales up.
- Keyboard-first: every Deck and Reader action has a key; arrow-key navigation in lists.

### Implicit engineering rules (found in code)
- **Atomic writes everywhere** (`.tmp` → `os.replace`, `.bak` for settings) so a crash never corrupts state.
- **Read-merge-before-write** for settings (controller and web UI both write; neither clobbers the other).
- **Resurrection-bug guard**: history/clipboard reconcile with disk before mutation so web-UI deletions aren't re-added.
- **Fail-closed security** for update signatures; **token-authenticated** local IPC; **API keys never sync**.
- **UI appears before the slow model loads** (boot Phase 1 vs Phase 2).

---

## 2. FULL APPLICATION STRUCTURE

### Directory breakdown
```
Internal/app/
  mumble.py            Orchestrator / main Mumble class (~4500 lines)
  ai.py                Cloud + provider LLM dispatch, prompts, TTS
  transcription.py     Optional cloud STT (Groq/OpenAI/OpenRouter)
  bindings.py          Keyboard+mouse hotkey abstraction
  formatting.py        Deterministic offline text formatter + mode detection
  prompt_constitution.py  Master Constitution v5 (Prompt-mode system prompt)
  islamic_terms.py     Islamic/Arabic vocabulary table + correction/annotation
  foreign_boost.py     Local phonetic foreign-term boosting engine
  model_free.py        Strict formatting-only pipeline (no model, no semantics)
  local_engine.py      Routing policy (local / local_llm / cloud) + GBNF grammars
  overlay.py           Island overlay controller + mode bar (Win32 layered window)
  island_render.py     Pure-Pillow island renderer (headless-capable)
  app_window.py        Fallback classic Tk main window
  ui.py                Tk widget toolkit (RoundButton, Switch, labels, round_rect)
  mode_select.py       "hold key + say keyword" token state machine (legacy)
  webui_shell.py       pywebview host + Python↔JS Api bridge
  webui/               index.html, app.css, enhanced.css, app.js, mumble.png
  settings.py          Settings store (DEFAULTS, migrations, cross-process lock)
  history.py           Transcript history + cumulative totals + word stats
  clipboard.py         Clipboard monitor (text + images), dedup, own-output exclusion
  context_store.py     Conversation/AI-reply store (auto-classify, retroactive promote)
  reader_store.py      Reader library (docs, position, bookmarks, collections, sessions)
  stats.py             Independent dictation + reader statistics
  favorites.py         Starred items (copies, capped 100)
  presets.py           20 built-in Deck intent presets + 5 custom slots
  prompt_memory.py     Prompt revision thread (windowed) + uncapped prompts log
  cloud_sync.py        Supabase auth + 6-type LWW sync
  cloud_schema.sql     Supabase table DDL + RLS
  update.py            Auto-update (manifest, SHA256, optional ed25519, atomic swap)
  autostart.py         Windows startup/shortcut management
  branding.py          Constants, paths, colours, hardware tiers, model selection
  brand_exe.py         Brand the venv pythonw → "Mumble.exe" (name/version/icon)
  assets_gen.py        Generate the gold-waveform app icon
  tips.py              Spaced, self-retiring island tips
  Ports/Linux, Ports/macOS   Per-OS adaptations of the runtime
  test_*.py            ~30 unit/integration test modules (run with venv, PYTHONUTF8=1)
```

### Module responsibilities (data flow architecture)
```
Mic ─sounddevice→ frames ─┬─(live)→ _stream_worker → partial transcripts
                          └─(stop)→ _process ─→ _transcribe ─┬→ local faster-whisper
                                                             └→ cloud STT (transcription.py)
   raw text ─→ vocab/foreign annotation (formatting.py, foreign_boost.py, islamic_terms.py)
            ─→ mode resolution (active island mode | detect_mode)
            ─→ _generate ─┬→ cloud LLM (ai.py: polish/prompt/email/reply/foreign/intent)
                          └→ offline builder (formatting.build_* / model_free.process)
            ─→ history.add + stats.record + webui refresh
            ─→ _paste (clipboard + Ctrl+V) → "Pasted!" / "Saved"
```

### Core dependencies (why they exist)
- **faster-whisper / ctranslate2** — local STT engine.
- **sounddevice + numpy** — mic capture and audio assembly (16 kHz mono float32).
- **pywebview + Edge WebView2** — host the HTML main window.
- **bottle** (vendored in venv) — lightweight HTTP inside the webui shell.
- **pystray + Pillow** — tray icon + island/icon rendering.
- **keyboard + mouse** — global hotkeys / mouse-button bindings.
- **pyperclip + PIL.ImageGrab** — clipboard text/image monitoring.
- **supabase-py** — optional cloud account + sync.
- **cryptography** (optional) — ed25519 update-manifest verification.
- **llama-cpp-python** (optional) — on-device small-model smart modes.

### State management system
There is no single global store; each concern owns a thread-safe persistent store (RLock + atomic JSON). Live UI state lives in:
- **Controller**: `self.recording`, `self.busy`, `self._processing`, `self.paused`, `self.active_mode`, `self.prompt_mode_enabled`, `self.state` (loading/idle/listening/transcribing/error).
- **Web UI**: JS globals `SET` (settings mirror), `HX` (Deck state), `READER` (reader state).
- **Island**: an internal snapshot dict driven by thread-safe setters (`set_state`, `set_level`, `set_building`, `flash`, `hint`, `set_armed`, `set_bar_state`).

---

## 3. UI SYSTEM (CRITICAL)

Mumble has **two UI surfaces**: (A) the **native always-on overlay** (island + mode bar + tray, drawn with Win32 layered windows / Pillow), and (B) the **web main window** (HTML/CSS/JS in pywebview). They share the design language but are technically separate.

### 3.1 Global Layout System

**Web main window** (`#app`): flex column, `width:100%`, `max-width: min(1280px, 100%)`, centered. Vertical rhythm of `gap:16px` between cards. Structure top-to-bottom:
```
.titlebar  (brand badge + name + status chip)
.navbar → .nav  (pill nav: Home · Deck · Stats · Reader · Settings)
.view[data-view="…"]  (exactly one visible; others [hidden])
```
- **Grid system**: utility classes `.grid`, `.g2` (2-col), `gap16`; responsive `.modules-grid` (3-col), `.stats-split` (50/50), `.stats-strip` (3 mini-cards), `.tiles` (6-col stat tiles).
- **Spacing logic**: tokenised radii `--r-sm 10 / --r-md 14 / --r-lg 18 / --r-xl 24`; card padding `.pad`; consistent `gap` utilities (gap6/8/12/16).
- **Container rules**: `.card` is the universal container (glass). Everything sits inside cards; views are flex columns.

**Native island layout**: a single capsule ("pill") anchored to the **bottom-center of the active monitor**, with an optional **mode bar** capsule stacked directly above it (bottoms touching).

### 3.2 Visual Design System

#### Colour palette — native (`branding.C`, "Golden Black")
| Token | Hex | Use |
|---|---|---|
| bg | `#0A0A0B` | window background |
| surface | `#121110` | panels |
| surface2 | `#1A1813` | inputs/chips |
| elevated | `#241F16` | hover |
| border | `#2B2519` | borders |
| border_soft | `#221E16` | soft dividers |
| text | `#F3EEE1` | primary text |
| text_dim | `#B6AE99` | secondary |
| text_mute | `#7C745F` | tertiary |
| gold / accent | `#D4AF37` | brand/primary |
| gold_hi | `#EBCB65` | hover/active gold |
| gold_dim | `#8C7320` | dim gold |
| gold_deep | `#B8941F` | gradient deep |
| amber | `#E0A92E` | transcribing/warn |
| red | `#D9544D` | error |
| track | `#2A2418` | toggle track |

#### Colour palette — web (`app.css :root`)
Surfaces (neutral charcoal): `--bg #08080A`, `--surface #131316`, `--surface-2 #18181C`, `--elevated #1E1E23`, `--elevated-2 #28282F`, `--border #2F2E37`, `--border-soft #242329`, `--track #2A2931`.
Gold: `--gold #EBC35C`, `--gold-hi #F8DD8A`, `--gold-dim #9C8030`, `--gold-deep #C99A38`.
Text: `--text #F4EFE3`, `--text-dim #B8B0A0`, `--text-mute #807A6C`.
Status: `--red #D9544D`, `--amber #E0A92E`, `--green #5AB85A`, `--green-deep #3A8A3A`.

#### Per-mode accent colours (used in BOTH surfaces)
| Mode | native (`MODE_COLORS`) | web (`--mode-*`) |
|---|---|---|
| text | `#D4AF37` gold | `#EBC35C` |
| prompt | `#A855F7` purple | `#A855F7` |
| email | `#5AA9E6` blue | `#5AA9E6` |
| reply | `#E8825A` coral | `#E8825A` |
| foreign | `#C7A36B` sand | `#C7A36B` |
| convert | `#D86E9A` rose | `#D86E9A` |
| context | `#D4AF37` | `#7ABFB0` teal (Deck source) |

#### Typography
- Native: `FONT "Segoe UI"`, `FONT_SB "Segoe UI Semibold"`. Island monospace timer uses Consolas/Monaco.
- Web: `--font: "Segoe UI","Inter",system-ui,…`; `--mono: "Cascadia Code","Consolas",ui-monospace,…`.
- Scale (web): hero h1 32px/700; section h1 ~20–22px; body 13px; labels 12px/600; meta 10–11px; keycaps 11px mono.

#### Shadows / depth / glass
- Web shadows: `--shadow-sm 0 2px 10px rgba(0,0,0,.35)`, `--shadow-md 0 6px 26px rgba(0,0,0,.5)`, `--shadow-lg 0 16px 50px rgba(0,0,0,.6)`.
- Glass recipe: `--glass-grad linear-gradient(145deg, rgba(34,34,40,.9), rgba(20,20,24,.94) 58%, rgba(26,26,31,.91))` + `--glass-hi inset 0 1px 0 rgba(245,238,222,.085)` + `--glass-lo inset 0 -1px 0 rgba(0,0,0,.32)` + `--glass-edge 0 0 0 1px rgba(235,195,92,.045)`.
- Easing: `--ease-spring cubic-bezier(.34,1.56,.64,1)`.

### 3.3 Component Library

#### Native widgets (`ui.py`)
- **RoundButton** — fully-rounded pill (radius = height/2), 1px top highlight hairline. Kinds: `primary` (gold fill, dark text), `ghost` (surface2), `danger` (#3A1D20 fill, red text), `subtle` (transparent). Hover lightens fill. Used for every action button.
- **Switch** — 46×26 (web 40×22) rounded track + knob. Off = `track`; On = `accent`, knob slides right.
- **RoundCard** — rounded container (18px), dark glass, 1px top highlight; `.body` holds children; redraws on content resize.
- **TabPill** — 32px pill nav; inactive (transparent/dim) → hover (surface2) → active (gold fill, dark text).
- **label()** — semantic text (size/colour/weight/wrap). `round_rect()` — polygon rounded rectangle primitive. `_lighten()` blends toward white. Dark titlebar applied via `DwmSetWindowAttribute`.

#### Island visual states (see §7 for timings)
Each state = dot colour + animation + label (+ optional sub-label/hint), with the rim blended toward the state colour:
| State | Dot | Animation | Label |
|---|---|---|---|
| listening | gold (pulsing) | 7-bar audio waveform + expanding ripple ring + glow | "Listening" + `M:SS` gold timer |
| transcribing | amber | 3 breathing dots | "Transcribing" |
| building | mode colour | 5 pulsing dots (+ optional gold absorption motes when gathering context) | "Building · <Mode>" / "Gathering · <Mode>" |
| done | mode colour | waveform-checkmark flash then fade | "Pasted!" / "Saved" (+ mode or paste-hint tail) |
| hint | gold/purple | pulsing text | e.g. "Ctrl + Alt + H", "Which mode?" |
| error | red | — | "Error" (+ sub-message e.g. "Check mic") |
| idle | — | pill withdrawn | — |
A small hollow ring at 11 o'clock marks **offline builder**; a purple ring + "✦" marks **Prompt sticky toggle on**.

#### Mode bar chips (`_WidgetBar`)
Glass capsule above the island — now a **compact, expandable selector** (refinement pass §1): collapsed by default to the active mode chip (or a neutral "Modes" opener) plus an expand chevron, so the bar occupies only the space it needs; tapping the chevron reveals the full **mode chips** (Prompt/Email/Reply, active one filled in its colour, the whole bar rings in the active mode's colour). Also a **Deck** button (gold, stack glyph → opens History) and an optional **Foreign** toggle. Hit-testing via `bar_layout(snap)`.

#### Web components (`app.css`/`enhanced.css`)
- **.btn** family: `.btn` (elevated), `.btn-gold` (near-black text on gold gradient), `.btn-ghost`, `.btn-outline-gold`, `.btn-danger`, `.btn-sm`, `.btn-icon`. Hover lift −1px, active scale .985, optional `btnSheen` glint.
- **.card** / `.card.lift` (hover translateY(−3px), gold rim, radial after-glow). **.tile** (stat tile, big gold `.num` + muted `.lab`). **.row** (Deck rows, glass, hover lift, action buttons fade in).
- **.input/.select/.textarea** (surface-2, focus → gold border + glow). **.switch**, **.segment** (segmented control). **.field** (label+control column).
- **.subtab**, **.chip**, **.badge**, **.tag**, **.kbd** (keycap, mono gold), **.icon-tile** (gold gradient 28px square + 13px svg).
- Charts: **.heatmap** (13×7 cells `.hm1–.hm4`), **.chart** (bars; `.bar.today` brighter), **.tod-chart** (24 hourly bars), **.weekday-chart** (7 bars), **.modebars** (horizontal per-mode bars).
- States: hover (lift+shimmer), active (gold fill), disabled (dim, not-allowed), focus (gold ring), loading (animated waveform/"Synthesising…"), error (red toast/message). All ambient motion stops under `body.paused` / reduced-motion.

---

## 4. SCREEN-BY-SCREEN BREAKDOWN

### 4.1 Native overlay screens

**Island (always-on)** — see §3.3 / §7. Purpose: live dictation status. Click-through; follows the foreground window's monitor; pauses when main window unfocused; auto-releases a 30s "suspended" safety valve.

**Mode bar** — pinned above the island; chips select output mode, Deck button opens History, Foreign toggles foreign-awareness. Disappears with the island when idle.

**Mode picker pop-up (`show_mode_picker`)** — small frameless panel above the island when AI mode confidence is low. Shows the original transcript (quoted, one line) + mode chips (number keys 1–9). Suggested mode pre-highlighted. Click chip or press number → process in that mode. **Auto-resolves to plain text after 8 s**; Esc/✕ also = plain text. Never loses the words.

**History Deck (native `show_deck`, used by Lite/fallback)** — searchable list of favourites + recents. Normal mode: click/Enter pastes. Job mode (preset/mode selected): Ctrl+Click/Ctrl+Space toggles a ✓ on items to feed the AI job; star zone on the right toggles favourite; arrow keys navigate; "Go" runs the job; Esc closes.

**Tray menu** — 8 most-recent transcripts (copy on click), pause/resume, check-for-updates, open window, quit.

### 4.2 Web main window screens (`index.html`)

**HOME** (`[data-view="home"]`)
- Hero card: animated 20-bar waveform (`.wb .wv-a…e`), headline "Just speak. Mumble types it for you.", a Record button (`#record-btn`, shows the activation hotkey) and Open History button, value badges.
- Cards: Global Shortcuts (4 hotkey rows), "Speak — get clean text".
- "What's inside" 3-up grid: **The Deck**, **Reader Mode**, **Smart Modes** (with mode pills).
- Instant Web Search card; **Pro Mode** card (key status + button) + **Tips** card.
- States: always populated (no empty state). Status chip top-right reflects live state (green idle / gold recording / red error) via `pollStatus` every ~180 ms.

**DECK / HISTORY** (`[data-view="history"]`)
- Toolbar: sub-tabs **Transcripts · Clipboard · Prompts · ★ Favourites**; action buttons (Paste latest, Copy, Capture selection, Capture chat, Pin, Refresh, Show Pre-/Post-AI, Clear all); filter input; sort (Newest/Oldest/Most words); item count.
- **Toolbox** (always visible gold-rimmed card): **Smart Mode** selector (shape of output) + **Presets** selector (what the AI should do).
- **Action bar** (`#hist-actionbar`, appears with ≥1 selected): selection count, Merge & Copy, Merge & Paste, Run, Clear.
- **List** (`#hist-list`): date-grouped rows. Transcript row = mode dot+chip · time · word count · text preview · Copy · delete. Clipboard row = IMG/CLIP badge · thumbnail (images) · time/size · preview · Copy/Open/delete. Prompt row = PROMPT badge · time · preview · Copy.
- Empty states per tab ("No transcripts yet…", "No clipboard items…", "No saved prompts yet", "Nothing starred yet").
- **User journey**: select items → pick Mode and/or Preset → Run → window minimises → AI job runs → result pastes into prior app and is saved to history.

**STATS** (`[data-view="stats"]`)
- 6 headline tiles: total words, transcripts, today words, time saved, current streak, best streak.
- Split: **Activity** (13-week heatmap + 7 insight rows: busiest day, active days, avg/active day, time speaking, pace, weekly trend, month) | **Reading** (3 tiles: total time, pages, docs completed).
- Strip: words-per-day chart (7/30/90d), "When you dictate" (hourly) + "Weekly rhythm" (weekday), Smart-Mode breakdown (mode · count · words).
- Reset stats button (scope all/dictation/reader). Empty state: "Start dictating to see statistics".

**READER** (`[data-view="reader"]`)
- Voice settings card: Provider / Model / Voice / Speed + voice tags. "Add your key" prompt if no key.
- Library: tabs **All / Collections / History**, search + starred filter, "Continue reading" card, document rows (star · title/meta/progress/collections · Resume · delete).
- New document: textarea (paste) or open `.txt`; "Add & read".
- Player (when a doc is open): back · title · progress %; controls play/pause, stop, bookmark, font ±, speed, summarize; bookmark list; AI summary card; reading pane with `<span class="rw" data-wi>` per word (current word highlighted, auto-scroll comfort band), chunked into `<div class="rw-chunk">`.
- States: synthesising ("▶ Synthesising voice… (N of M parts) · ~Xs left"), TTS failure toast, empty library/collections/history.

**SETTINGS** (`[data-view="settings"]`) — scrolling cards:
Your name · Your Processing Setup (Transcription/AI/Hardware/Model summary) · Shortcuts (4 hotkey capture buttons + search engine + browser) · How Mumble works (Prompt toggle, auto-format toggle, foreign language chips) · Prompt & polish preferences (Tone/Detail/Structure/Audience/Reasoning/Polishing + prompt memory) · Personal vocabulary · Preset Adder (5 custom) · AI Provider (Pro Mode toggle, main provider + per-provider key/model for Cerebras/OpenAI/Anthropic/DeepSeek/Groq/OpenRouter; separate Prompting provider) · Microphone (device + test + level meter) · Transcription (local/cloud engine, hardware tier, language, model picker, cleanup toggle, cloud provider) · Deck & clipboard (sizes, capture toggle) · System (start with Windows, visual effects tier, brand mark, resource saver, reset to defaults) · About/links/version · Data folder / switch to Lite / fullscreen / reset window · Cloud sync (sign up/in/out + per-type toggles).

---

## 5. FEATURE SPECIFICATION

### 5.1 Global dictation (the core loop)
- **Input**: activation hotkey (default `ctrl+windows`). `on_hotkey()` toggles; spawns `_safe_start`/`_safe_stop` so the keypress returns instantly.
- **How it works**: `start_recording` opens a 16 kHz mono float32 `sounddevice` input stream; `_audio_cb` accumulates frames and throttles island waveform updates; a `_stream_worker` transcribes stable 4-second ranges live (skipped in Resource Saver). `stop_recording` harvests same-session in-flight work, discards clips < `min_seconds` (0.3 s), assembles audio, and calls `_process` for only the uncovered tail or the authoritative full pass.
- **Output**: text is pasted via clipboard + Ctrl+V. "Pasted!" if a text field was editable, else "Saved".
- **Edge cases**: paused/busy/processing veto a new recording; mic unavailable → self-heals to default device; clip too short → silent return to idle.

### 5.2 Speech-to-text
- **Local (default)**: faster-whisper, `beam_size=1` (greedy; AI repairs accuracy), `vad_filter=True`, `hotwords=vocabulary_terms`, `word_timestamps` only when needed, `no_speech_threshold=0.6`, `log_prob_threshold=-1.0`. Serialized by `_tx_lock` with a 25 s wedge bail; process priority boosted to HIGH during decode; RTF logged for diagnostics.
- **Cloud (opt-in)**: `transcription.py` → Groq (`whisper-large-v3-turbo`, multipart), OpenAI (`gpt-4o-mini-transcribe`, multipart), OpenRouter (`groq/whisper-large-v3-turbo`, JSON+base64). Skipped when word timestamps are needed. One-time toast on first failure, then local fallback.
- **Confidence gating**: segments dropped/flagged by `gate_segment` thresholds (`no_speech_prob .6`, `compression_ratio 2.4`, `avg_logprob −1.0`); quality chip = good/fair/bad from weighted `avg_logprob`.

### 5.2a Dictation performance trace
- **Contract**: `dictation_trace.py` owns schema `mumble.dictation-trace.v1`. The controller creates one session per activation only when `MUMBLE_DICTATION_TRACE=1`; normal builds leave it off.
- **Measured boundaries**: activation, first Island render, audio open/record/close, first audio, first stable partial when available, stream drain, transcription/paste lock waits, model load/warm-up when they occur inside the session, inference, final transcript readiness, formatting, persistence, clipboard readiness, paste dispatch, finish outcome, and local CPU/RAM/thread samples.
- **Cold/warm identity**: the session records model/runtime settings, whether the model was already resident, and a process-local inference ordinal. The first inference can therefore be separated from reuse without recording speech.
- **Privacy and storage**: a strict field allow-list excludes audio, transcript/partial text, clipboard and prompt content, paths, provider payloads, keys, and exception messages. Completed JSONL records are flushed to the app-data `diagnostics` directory, privately permissioned where supported, capped at 5 MB with two backups, and exportable as a standalone JSON array. Trace failure never changes dictation behaviour.
- **Partial-result truth**: the current local worker produces finalized four-second chunks, not tentative tokens. The context therefore declares `stable_chunks_only`; `unstable_partial_ready` remains a schema capability for a future engine but is not emitted by the present one.

### 5.3 Smart Modes (output shaping)
Modes: **text, prompt, email, reply, foreign, convert** (`convert` is a router → email/reply/prompt). The dedicated **list** mode was retired (refinement pass §5): the text/email lanes bullet dictated lists natively, so `guess_mode`/`detect_mode` no longer yield 'list', the `LIST_SYSTEM`/`cerebras_list`/`build_list` lane is gone, and 'list' is removed from every mode map/UI. Mode comes from the island toggle (`active_mode`) or, for plain dictation, from `detect_mode()` / a second-opinion AI tag. Each cloud lane has a focused system prompt (`TEXT/EMAIL/REPLY_SYSTEM`, `FOREIGN_SYSTEM`, Prompt = full Constitution). Offline, each maps to a deterministic builder (`build_prompt/build_email/format_transcript`).

### 5.4 Prompt Architect (Prompt mode)
The flagship. On Cerebras it sends the **Master Constitution v5** (see §10) and runs a hidden multi-step pipeline (classify → method → draft → critique → rewrite → self-review → final check → `FINAL_PROMPT_START…END`). Output extracted by `_extract_final_prompt`. **Prompt memory** feeds the last ≤3 exchanges within 15 min so "make it shorter" revises the previous prompt. Standing **prompt_prefs** (tone/detail/structure/audience/reasoning) are appended via `render_prefs`. Other providers get a lightweight constitution.

### 5.5 The Deck (history/clipboard/prompts + AI jobs)
Unified newest-first list across transcripts, clipboard (text+images), and prompts, with favourites pinned. Selecting items + a **preset** (one of 20 built-ins or 5 custom) + optional **mode** and pressing Run executes `run_deck_job` → `ai.cerebras_intent` (high reasoning, 32K budget): the preset instruction is the system message, the labelled source items + spoken words are the user content. Result pastes into the prior app. Retries once on 429; falls back to offline builder; never dumps raw material.

### 5.6 Clipboard capture & context
Background monitor (`clipboard.py`) captures text and images (deduped by hash; images stored as PNG in `clip_images/`), excludes Mumble's own paste output, throttles image grabs (~5.6 s) and X11 text reads. Copied AI replies are auto-classified into the **conversation store** (`context_store.py`) by 9 heuristics; saying "context" within 30 s **retroactively promotes** the last copy. The conversation feeds Reply/Prompt context.

### 5.7 Reader Mode
Paste/open a document → library entry keyed by content hash (resumable). Build word + ~400-char sentence-aligned chunks; synthesize each via `reader_tts` (OpenRouter/OpenAI TTS, male-voice-curated), prefetch next chunks, highlight words in sync, auto-scroll. Features: bookmarks, collections, continue-reading, AI summarize, font scale, speed, sleep timer, reading sessions logged to stats.

### 5.8 Vocabulary, foreign & Islamic terms
Three-layer vocabulary: STT hotwords → high-confidence auto-fix (phonetic key + edit distance) → medium-confidence `heard//Term` annotation for the AI. `foreign_boost.py` boosts/offers foreign tokens by phonetic signature + gateway context; `islamic_terms.py` supplies ~250 Arabic/Islamic terms with gateway-gated correction and `annotate_foreign`.

### 5.9 Quick paste, search, capture
- **Quick paste** (`ctrl+alt+v`): paste the latest transcript instantly.
- **Search** (`ctrl+alt+s`): if recording, search the result; else search the highlighted selection (or voice-search). Engines: google/perplexity/brave.
- **Capture selection / conversation**: grab highlighted text or a full AI chat into the Deck.

### 5.10 Cloud account & sync, auto-update, autostart
See §10/§2. Optional Supabase sign-in syncs 6 data types (LWW); auto-update verifies SHA256 (+ optional ed25519) and atomic-swaps; autostart manages Startup-folder shortcuts and cleans legacy Run-key entries.

### 5.11 Tips, Pro Mode, Resource Saver, Lite
- **Tips**: a **usage-aware** library of ~18 spaced, self-retiring island hints (refinement pass §2). Each tip is gated by an `eligible(usage)` predicate — it's offered only while its feature is still UNUSED and the user has enough dictations to benefit, and RETIRES the moment the feature is adopted (so it never nags about something already in use). Spacing (≥8 min apart, ≥4 dictations between) + per-tip appearance caps remain. Usage is read from existing stores plus coarse `stats.feature_usage` adoption counters.
- **Pro Mode**: master toggle for cloud AI; falls back to offline builder if the key fails.
- **Resource Saver**: forces `tiny.en`, disables live streaming + AI warm-up.
- **Mumble Lite**: a lighter Tk snapshot launched in place of the web UI.

---

## 6. SYSTEM LOGIC & BEHAVIOUR

### Event handling
Global hotkeys/mouse via `bindings.py` (`register_hotkey`, `register_hold`, `capture`, `validate`, `pretty`). Bare modifiers are rejected (would fire on every tap) and healed. The web UI sends commands over the token-authenticated CMD port; the controller's `_start_cmd_server` handles `status/record/quit/paste/paste_image/reload/deck_job/grab_selection/capture_conversation/focused`.

### Input processing pipeline
`_process(audio, duration, mode_active, windows)`: assemble streamed base + tail → vocab/foreign annotation → mode resolution → `_generate` (cloud or offline) → second-opinion handling (auto re-run or picker) → history + stats + webui refresh → paste → island flash → maybe tip.

### Speech-to-text flow
Covered in §5.2. Streaming worker keeps a precise sample seam (`_stream_processed_samples`) so no audio is dropped/repeated at chunk boundaries.

### AI interaction layers
`ai.py` speaks OpenAI-compatible chat for most providers and **native Anthropic Messages API** for Claude. Streaming yields deltas then `FULL_MARKER`/`TRUNC_MARKER`. Reasoning-effort is Cerebras-scoped; reasoning models omit temperature; `max_completion_tokens` for OpenAI vs `max_tokens` elsewhere. Long text is chunked (`polish_text`) with continuation loops — **no silent truncation**. 429s surface the server's Retry-After.

### Background processes / async
Threads: main Tk + `_pump`; tray; boot (Phase 1/2); audio callback; stream worker; hotkey handlers; instance-signal server; clipboard monitor; background sync; update auto-check; AI warm-up; model load. Locks: `lock`, `_tx_lock`, `_paste_lock`, `_deck_job_lock`; `_tk_queue` marshals to the Tk thread; `_stream_done` Event stops the worker.

### Caching / persistence layers
Island caches static pill backgrounds (LRU 8) and glow sprites. AI uses byte-stable system prompts for provider prompt-prefix caching. All stores use atomic JSON with RLocks; settings use a cross-process O_EXCL lock with 5 s stale-steal.

---

## 7. ANIMATIONS & MOTION SYSTEM

### Native island (22 fps on-screen / 5 Hz idle)
| Animation | Cycle | Expression |
|---|---|---|
| dot pulse | ~6.2 s | `sin(frame*0.16)` |
| listening ripple | 1.6 s | `(frame%36)/36` |
| listening waveform | per-bar | `sin(frame*0.4 + i*0.8)`, profile (0.55,0.75,1,1,0.85,0.65,0.45) |
| transcribing dots | staggered | `sin(frame*0.45 − i*0.9)` |
| building dots | ~24 s | `sin(frame*0.26 − i*0.7)` |
| rim breathe | ~3 s | `(sin(frame*0.094)+1)/2` |
| travelling sheen | continuous | `(frame*1.6)%span`, clipped to pill |
| armed ring | ~5.7 s | `0.55+0.45*(sin(frame*0.22)+1)/2` |
| hint pulse | ~6.2 s | `0.65+0.35*(sin(frame*0.16)+1)/2` |
| gold absorption motes | 38-frame | inward-streaming, alpha fadeout |
State timeouts: done = `DONE_FRAMES 30` + `FADE_OUT_FRAMES 22`; hints = `HINT_FRAMES 90`; suggestion chips = `SUGGEST_FRAMES 46` (frozen while armed). Transient chips dissolve over their final 22 frames. Animation pauses on unfocus and resumes from the same counter.

### Web keyframes
Base (always): `pulse` (status dot), `waveA–E` (hero bars, 0.8–1.45 s staggered), `glowPulse`, `savedRing` (setting-saved confirm), `selectPop` (spring tab/preset select).
Enhanced (tier `enhanced`, paused under `body.paused`): `dustA`, `heroBreathe`, `heroSweep`, `invite` (record-button ring), `barPulse` (today bar), `navShine`, `pageHeaderShine`, `bokehDrift`, `goldSheen` (headline gradient), `heroParticlesA/B`, `hoverSweep`, `widgetShimmer`, `btnSheen`, `blobDrift`.
Transitions: cards/buttons transform 0.25 s spring + shadow 0.25 s ease; controls 0.15 s.

### Global motion philosophy
Tiered (`lite` strips enhanced anims; `standard` middle; `enhanced` full), reduced-motion + `body.paused` honoured, dissolve-don't-snap, never steal attention.

---

## 8. DATA MODEL

Root: `branding.DATA_DIR` — Windows `%APPDATA%/Mumble`, macOS `~/Library/Application Support/Mumble`, Linux `$XDG_DATA_HOME/Mumble`. All files atomic JSON unless noted.

| File | Shape | Cap |
|---|---|---|
| `settings.json` (+ `.bak`, `.lock`) | `{key:value}` (100+ keys) | — |
| `history.json` | `[{time,stamp,mode,text,raw,words,duration,quality,via}]` | `history_max` 5000 |
| `history_cumulative.json` | `{total_words,total_transcripts}` (monotonic) | — |
| `transcripts.txt` | `[stamp] (mode)\ntext\n\n` append log | — |
| `clipboard.json` (+ `clip_images/*.png`) | `[{type:text\|image,time,stamp,text\|path,hash,size}]` | `clipboard_max` 5000 |
| `conv_store.json` | `[{time,stamp,source,confidence,text,length,role}]` | 100 |
| `reader_library.json` | `[{id,title,text,length,position,added,opened,starred,bookmarks[],collections[],reading_sessions[]}]` | 100 docs |
| `favorites.json` | `[{key(sha256),text,source,mode,time,added}]` | 100 |
| `presets.json` | `[{slot,title,description,instruction}]` (custom slots 21–25) | 5 |
| `prompt_thread.json` | `[{t,request,prompt}]` (15-min window) | 10 |
| `prompts.json` | `[{t,time,request,prompt}]` (uncapped) | — |
| `stats.json` | `{total_words,total_transcripts,spoken_seconds,best_wpm,wpm_words,wpm_seconds,wpm_ema,days{},modes{},hours[24],reader_*}` | — |
| `.session` | supabase session (never plaintext keys) | — |
| `cmd_token` | per-session IPC auth token | — |

**Settings defaults (selected, full list in `settings.py`)**: `hotkey ctrl+windows`, `quick_paste_hotkey ctrl+alt+v`, `history_hotkey ctrl+alt+h`, `search_hotkey ctrl+alt+s`, `search_engine perplexity`, `model small.en`, `language en`, `hardware_tier auto`, `transcription_mode local`, `cloud_transcription_provider groq`, `pro_mode true`, `llm_provider cerebras`, `cerebras_model gpt-oss-120b`, `ui_effects enhanced`, `autostart true`, `first_run true`, `history_max/clipboard_max 5000`, `prompt_prefs {tone Neutral, detail Balanced, structure "Bullets & headings", audience General, reasoning "Just the answer"}`, `polish_aggressiveness Light`, plus migration flags (`big_shift_applied`, `stt_tts_dead_default_healed`, etc.) and `sync_*` toggles.

**Stats computations**: WPM only from Text mode, duration > 0.4 s, 0 < wpm ≤ 300; EMA = `0.25*sample + 0.75*prior`; time-saved vs 45 wpm baseline; reader pages = words/250; streaks = consecutive active days (≥1 transcript/session).

**Cloud schema (`cloud_schema.sql`)**: 6 RLS-protected tables (`user_settings`, `user_stats`, `user_history`, `user_reader_library`, `user_favorites`, `user_presets`), each `id/user_id/payload(jsonb)/updated_at/created_at`, owner-only CRUD via `auth.uid() = user_id`.

---

## 9. EDGE CASES & FAILURE MODES

- **No/short audio** → discard < 0.3 s; island "Error"/idle; "Check mic".
- **Mic unavailable** → self-heal to default device, persist; Settings shows red status.
- **Model won't load** → roll back to previous model; "✕ couldn't load <model>"; if no model and no cloud → error state.
- **AI key rejected (401/402/403)** vs network failure → distinguished; one-time toast; **offline builder fallback** (never dumps raw text).
- **Cloud STT failure** → one-time toast, local fallback.
- **429 rate limit** → retry once honouring Retry-After (capped 25 s).
- **Token-limit truncation** → continuation loop (no silent truncation); `TRUNC_MARKER` notifies.
- **Whisper hallucinations** ("thanks for watching" etc.) → blocklist; **loops** → `_deloop`.
- **Clipboard write contention** → confirmed write with retries (`_set_clipboard`).
- **Pillow/numpy/ctypes missing** → island falls back to Tk canvas; monitor-detect failure → screen-center bottom; font cascade → bundled default; GDI handles freed by finalizer.
- **pywebview/WebView2 missing** → classic Tk window / Lite.
- **Two launches** → single-instance lock signals the first to open; **suspended island** auto-releases after 30 s.
- **Corrupt JSON** → keep in-memory copy / restore `.bak` / rename `prompts.json.corrupt`; cross-process deletions reconciled to avoid resurrection.
- **Update integrity** → missing SHA256 always fails; signature fail-closed once a public key is configured; zip-slip protected; previous version kept for rollback.
- **Empty UI states** → every Deck/Reader/Stats list has a dedicated empty card.
- **Input validation** → bare-modifier hotkeys rejected; API keys masked on read, bullet-only writes rejected, never synced.

---

## 10. AI / EXTERNAL INTEGRATION LAYER

### LLM providers (`ai.py` PROVIDERS)
Cerebras (`gpt-oss-120b`, default), OpenAI (`gpt-5.4-mini`, `max_completion_tokens`), Anthropic (`claude-opus-4-8`, native Messages API), OpenRouter (`openai/gpt-5.4-mini`, credits endpoint), DeepSeek (`deepseek-v4-flash`), Groq (`llama-3.3-70b-versatile`), local (Ollama/LM Studio, `http://localhost:11434/v1`). A separate **prompt provider** can route Prompt mode to a stronger model.

### Prompt structure
- **Master Constitution v5** (`prompt_constitution.py`, full on Cerebras / lightweight elsewhere): a Prompt-Architect doctrine — Role & Mandate; the anti-pattern template to avoid; 5 worked transformation examples (creative/planning/execution/analysis/persuasive); core rules (**expand intent, never expand scope**; never defer to the user; task classification; signal extraction; complaint translation; hidden-requirement discovery; ambiguity is a bug; depth; success-as-outcome; directional freedom; examples are highest-value; British English scoped to prose); per-task-type build guides; a 7-point self-check; and the standard. `render_prefs` appends standing preferences.
- **Universal/focused systems**: `UNIVERSAL_SYSTEM`, `POLISH_SYSTEM` (+ `POLISH_LEVELS` Light/Standard/Thorough), `SECOND_OPINION_TAIL` (AI appends `MODE: … CONF: …`), `FOREIGN_SYSTEM`, `TEXT/EMAIL/REPLY_SYSTEM`. UK English rules injected (except Prompt mode).
- **Intent lane**: preset instruction = system; labelled sources + spoken words = user; high reasoning, 32K budget.

### Response parsing / streaming
Streaming deltas → `_collect_text` detects `FULL_MARKER`/`TRUNC_MARKER`; `_clean` strips quotes/echoed instructions/sections; `_extract_final_prompt` pulls the `FINAL_PROMPT_START…END` block; `split_mode_tail` parses the second-opinion tag.

### TTS (Reader)
`TTSProvider` abstraction over OpenRouter (Gemini/Mistral/Microsoft voice catalogue, PCM/L16→WAV framing) and OpenAI direct (`gpt-4o-mini-tts`, 13 voices). Male-curated, quality-tagged; `synthesize_with_fallback` switches provider on failure.

### Local AI routing (`local_engine.py`)
Cloud-dominance hierarchy: **cloud key present & local-only off → cloud is primary** (local does only raw STT + `//` uncertainty marking). **No key or local-only on → local engine**, never hard-blocks: `text`→model-free formatting; smart lanes→local LLM (llama.cpp + GBNF grammars for email/json) if ready, else offline builder + an appended upgrade notice. The local-LLM lane is now **wired end-to-end** (refinement pass §4): boot-time `discover_model()` (a GGUF in `DATA_DIR/models/` or the `local_llm_model` setting) + `build_backend()` install a backend — `LlamaCppBackend` (the `llama-cpp-python` lib) if present, else the new **`LlamaCliBackend`** driving the bundled `llama-cpp-bin/llama-cli` binary via subprocess (no pip dependency) — and `mumble._generate` dispatches cloud → local-LLM → deterministic builder. With no GGUF present the backend stays Null and shaping is unchanged; the actual model file is owner-supplied. `model_free.py` enforces a strict *surface-only* contract (no semantics, no guessing).

### Cloud sync (`cloud_sync.py`)
Supabase auth (sign up/in/out, `.session` persistence) + `SyncManager` for 6 data types, last-write-wins, background daemon every 30 s. **API keys are excluded from sync.**

### Update feed (`update.py`)
HTTPS manifest (`version,url,sha256,signature?,min_supported,notes,mandatory`); SHA256 mandatory; optional ed25519 (fail-closed); atomic folder swap via `apply_update.bat`; `.venv` carried + pip-refreshed; backup kept for rollback.

### Local IPC
`CMD_PORT 49519` (controller commands, HMAC token from `cmd_token`), `WEBUI_PORT 49520` (web window listener), single-instance lock `49517`.

---

## 11. HTML VISUAL REPRESENTATION LAYER

### Home (structural mock)
```html
<div id="app">
  <div class="titlebar">
    <div class="brand"><div class="brand-row">
      <div class="brand-badge"><!-- gold mic icon --></div>
      <div class="brand-name">Mumble</div></div>
      <div class="status-chip"><span class="status-dot"></span><span class="status-text">READY</span></div>
    </div>
  </div>
  <nav class="navbar"><div class="nav">
    <button class="nav-btn active" data-nav="home">Home</button>
    <button class="nav-btn" data-nav="history">Deck</button>
    <button class="nav-btn" data-nav="stats">Stats</button>
    <button class="nav-btn" data-nav="reader">Reader</button>
    <button class="nav-btn" data-nav="settings">Settings</button>
  </div></nav>
  <section class="view" data-view="home">
    <div class="hero card">
      <div id="hero-waveform" class="glass"><span class="wb wv-a"></span>…(×20)</div>
      <h1>Just speak. <em>Mumble types it for you.</em></h1>
      <div class="flex gap12">
        <button id="record-btn" class="btn btn-gold">Record · Ctrl+Win</button>
        <button id="open-history-btn" class="btn">Open History · Ctrl+Alt+H</button>
      </div>
    </div>
    <div class="grid g2 gap16">
      <div class="card pad lift">Global Shortcuts …</div>
      <div class="card pad lift">Speak — get clean text …</div>
    </div>
  </section>
</div>
```
**CSS intent**: `#app` flex column max-width 1280; `.card` = `--glass-grad` + `--shadow-md` + glass insets + faint gold edge; `.btn-gold` = near-black text on gold gradient; hero headline animates `goldSheen`; waveform bars run `waveA–E`.

### Deck row (structural mock)
```html
<div class="row" data-stamp="2026-06-29 14:32:45">
  <span class="mode-dot" style="background:var(--mode-email)"></span>
  <span class="tag" style="color:var(--mode-email)">EMAIL</span>
  <span class="t-mute">2:34 PM · 47 words</span>
  <div class="row-text">Hi Sarah, following up on …</div>
  <div class="row-actions"><button class="btn-icon">★</button>
    <button class="btn-icon">Copy</button><button class="btn-icon">✕</button></div>
</div>
```
**CSS intent**: glass row, hover translateY(−1px), actions fade in on hover; left mode dot + uppercase mode tag in the mode colour.

### Island (conceptual structure — drawn, not DOM)
```
[ ● ▮▮▮▮▮▮▮  Listening ]          ← gold dot · waveform · label
[          1:07         ]          ← the M:SS timer is STACKED BELOW the waveform
                                     (refinement pass §9) so the bars never shift as
                                     it ticks; the listening pill grows upward only.
   capsule: bg #0C0B09, rim #3A3320→#6A6047, glass gradient, travelling sheen,
   bottom-center of active monitor, 34px tall, corner 17px, click-through.
Mode bar above (compact, collapsed):  [ ◆ Prompt ▾ | ▣Deck  ◇Foreign ]
            (tap ▾ to expand):        [ Prompt  Email  Reply ▴ | ▣Deck  ◇Foreign ]
```

### Stat tile / heatmap (structural)
```html
<div class="tiles">
  <div class="tile"><div class="num">128,540</div><div class="lab">Words dictated</div></div> …
</div>
<div class="heatmap"><span class="hm-cell hm3"></span>…(13×7)</div>
```
**Visual hierarchy** throughout: gold = primary/number/action; warm-cream = body; muted = metadata; mode colours = categorical; red/amber/green = status only.

---

## 12. PRODUCT DEFINITION SUMMARY

### Precise definition (current state)
**Mumble is a background, hotkey-driven desktop dictation app for Windows (with Linux/macOS ports) that transcribes speech locally with faster-whisper, optionally reshapes it with a cloud or local LLM into one of several Smart Modes, and pastes the result into the user's focused application — backed by a web-based Deck for searchable history, clipboard capture, AI "jobs" over saved material, a document Reader with AI TTS, statistics, and optional encrypted-account cloud sync.** It is the "Golden Black" design system realised as a native floating island overlay plus a pywebview main window, communicating over token-authenticated localhost IPC. Source version **0.9.1**.

### What Mumble is NOT
- Not a cloud-only service — STT is local by default; it runs and dictates with **no internet and no API key** (offline formatter).
- Not a meeting transcriber / diarizer / real-time captioner for others — it is single-user, push-to-dictate.
- Not a chat assistant — the AI shapes *your* dictation; it does not converse.
- Not mobile or browser-extension software — it is a desktop OS-level app.
- Not a key-syncing product — API keys never leave the device; only text/stats optionally sync.
- Not dependent on held modifier "mode keys" anymore (retired in The Big Shift).

### Key constraints & design boundaries
- **Privacy-first**: audio stays local; keys never sync; IPC is token-authenticated; updates are integrity-checked.
- **Never lose words**: ambiguity resolves to plain text on timeout; AI failure falls back to the offline builder.
- **Never steal focus**: island is click-through; Deck can be a non-activating palette.
- **Graceful degradation**: cloud → local LLM → deterministic formatter, always producing *some* faithful output.
- **Single instance, two processes**: controller (49519/49517) + web UI (49520).
- **Tiered performance**: visual tiers + Resource Saver + hardware-aware model selection (weak→base, mid→small, powerful→distil-large-v3).
- **Atomic, reconciled persistence**: every store survives crashes and concurrent writers.

---

*End of Master System Documentation. Every value, colour, default, schema, and behaviour above is sourced from `Internal/app/`. Where the running build and this document could drift (e.g. version bumps, new providers), the code under `Internal/app/` remains the single source of truth.*

---

## 13. LICENSING & OPEN-SOURCE ATTRIBUTIONS

### 13.1 Mumble's Own License

Mumble is released under the **MIT License**. A copy of the full license text is available in
the repository root at [`LICENSE`](../../LICENSE).

**Summary of permissions:**
- ✅ **Commercial use** — Mumble may be used, modified, and distributed commercially.
- ✅ **Modification** — Source modifications are permitted without a copyleft obligation.
- ✅ **Distribution** — Redistribution in source or binary form is allowed under the same
  license notice.
- ✅ **Private use** — No obligation to disclose source code when using or modifying
  Mumble privately (no copyleft).
- ⚠️ **Warranty disclaimer** — The software is provided "as is" without warranty of any kind.

**Third-party obligations:** While Mumble itself is MIT-licensed, it depends on open-source
components under a variety of licenses (see §13.2). When distributing Mumble (in source or
binary form), you must comply with the license terms of every bundled dependency. The MIT
License applies only to Mumble's original source code; third-party components retain their
own licenses.

---

### 13.2 Open-Source Dependency Inventory

Mumble's direct runtime set is pinned in `Internal/app/requirements.txt`; important
transitive/native components are also noted here. This section documents what each
component is, why it is used, its governing license, and any compliance requirements. The
dependencies are grouped into four functional areas: **(A) Core Runtime**, **(B) Reader
Parsers**, **(C) Meeting Support**, and **(D) Optional / Conditional**.

#### A. Core Runtime (11 dependencies)

These are the always-required packages that power dictation, the system tray, the web UI,
clipboard monitoring, global hotkeys, and the island overlay.

| # | Package | Purpose | License | Compliance Requirements |
|---|---------|---------|---------|------------------------|
| 1 | **faster-whisper** | Local speech-to-text engine using CTranslate2; transcribes spoken audio into text. | MIT | Retain copyright notice in distributions. |
| 2 | **CTranslate2** | High-performance inference backend for the Whisper model; provides the C++ runtime that faster-whisper wraps. | MIT | Retain copyright notice in distributions. |
| 3 | **sounddevice** | Cross-platform audio input via PortAudio; captures microphone audio at 16 kHz mono float32. | MIT | Retain copyright notice and permission notice; audit the bundled PortAudio native component separately. |
| 4 | **numpy** | Numerical array operations used throughout the audio pipeline (frame concatenation, resampling, silence detection). | BSD-3-Clause | Retain the copyright notice, list of conditions, and disclaimer in distributions. |
| 5 | **pywebview** | Web UI host that loads `index.html` in an Edge WebView2 native window; bridges JavaScript ↔ Python via the `window.pywebview.api` object. | BSD-3-Clause | Retain the copyright notice, list of conditions, and disclaimer in distributions. |
| 6 | **pystray** | System tray icon with context menu; provides the background presence and quick-access menu. | LGPL-3.0 | Dynamic linking is permitted without triggering copyleft on Mumble's own code; if you modify `pystray` itself, you must make those modifications available under LGPL-3.0. |
| 7 | **Pillow** | Image processing library used for rendering the floating island overlay (compositing, gradients, waveform bars) and for reading clipboard images. | MIT-CMU | Retain the licence notice and disclaimer. |
| 8 | **keyboard** | Global keyboard hook; registers and dispatches the dictation activation hotkey (`ctrl+windows`), quick-paste, search, and Deck hotkeys system-wide. | MIT | Retain copyright notice in distributions. |
| 9 | **mouse** | Global mouse button hook; enables optional mouse-button dictation bindings (e.g. thumb buttons). | MIT | Retain copyright notice in distributions. |
| 10 | **pyperclip** | Cross-platform clipboard read/write; used to retrieve the user's clipboard content for the Deck clipboard monitor and to paste processed text (via Ctrl+V). | BSD-3-Clause | Retain the copyright notice, list of conditions, and disclaimer in distributions. |
| 11 | **bottle** | Lightweight WSGI micro web-framework (vendored within the venv); provides internal HTTP routing inside the web UI shell process. | MIT | Retain copyright notice in distributions. Bottle is distributed unmodified. |

#### B. Reader Parsers (7 dependencies)

These libraries parse documents for the Reader feature. Each is loaded on demand when the
user opens a file of the corresponding format.

| # | Package | Purpose | License | Compliance Requirements |
|---|---------|---------|---------|------------------------|
| 12 | **PyMuPDF (fitz)** | PDF document parsing; extracts text, metadata, and structure from PDF files. ⚠️ **Requires special attention** — see §13.3 for full AGPL-3.0 compliance discussion. | **AGPL-3.0** | **Copyleft:** Modifications to PyMuPDF itself must be disclosed. Under the Affero clause, if Mumble's PDF parsing is exposed as a network service, source code disclosure obligations are triggered. See §13.3. |
| 13 | **python-docx** | Reads and extracts text from Microsoft Word `.docx` documents. | MIT | Retain copyright notice in distributions. |
| 14 | **beautifulsoup4** | HTML parsing and text extraction; used for the Reader's HTML document support and for parsing HTML clipboard captures. | MIT | Retain copyright notice in distributions. |
| 15 | **EbookLib 0.20** | EPUB document parsing; extracts text, chapters, and metadata from EPUB e-books. | **AGPL-3.0** | **Release blocker:** the previous BSD classification was incorrect. Replace it, obtain suitable alternative terms, or receive specialist compatibility advice before distribution. |
| 16 | **odfpy** | OpenDocument Text (`.odt`) parsing; extracts headings, paragraphs, and lists from ODF files. *(Added in Meeting Mode + Reader Enhancement mission.)* | Apache-2.0 | Retain the Apache 2.0 license notice; include a copy of the license. No copyleft obligations. |
| 17 | **python-pptx** | PowerPoint (`.pptx`) parsing; extracts slide text and speaker notes with per-slide structure. *(Added in Meeting Mode + Reader Enhancement mission.)* | MIT | Retain copyright notice in distributions. |
| 18 | **openpyxl** | Excel (`.xlsx`) spreadsheet parsing; extracts sheet data as table blocks with row/column structure. *(Added in Meeting Mode + Reader Enhancement mission.)* | MIT | Retain copyright notice in distributions. |

#### C. Meeting Support (2 dependencies)

These libraries support the Meeting Mode audio import and processing pipeline.

| # | Package | Purpose | License | Compliance Requirements |
|---|---------|---------|---------|------------------------|
| 19 | **soundfile** | Audio file reading via libsndfile; used by `process_audio_file` to import non-WAV meeting recordings (MP3, M4A, FLAC, etc.). *(Added in Meeting Mode + Reader Enhancement mission.)* | BSD-3-Clause | Retain the copyright notice, list of conditions, and disclaimer in distributions. |
| 20 | **scipy** | Scientific computing; used for audio resampling (`scipy.signal.resample`) when importing meeting audio at sample rates other than 16 kHz. *(Added in Meeting Mode + Reader Enhancement mission.)* | BSD-3-Clause | Retain the copyright notice, list of conditions, and disclaimer in distributions. |

#### D. Optional / Conditional (2 dependencies)

These packages are not required for core functionality but enable optional features or are
used during development and testing.

| # | Package | Purpose | License | Compliance Requirements |
|---|---------|---------|---------|------------------------|
| 21 | **pyannote.audio** | Neural speaker diarisation for Meeting Mode; identifies who spoke when in multi-speaker meetings. Requires a HuggingFace access token and explicit user opt-in. Not loaded unless the user configures a token. | MIT | Retain copyright notice in distributions. Pyannote models have separate licensing terms from the Hugging Face hub; users are responsible for accepting those terms when downloading models. |
| 22 | **psutil** | Cross-platform process and system monitoring; used in test suites to verify resource usage, check background threads, and assert process cleanup. Not loaded at runtime by the application itself. | MIT | Retain copyright notice in distributions. |

---

### 13.3 AGPL Reader Dependencies — Release Gate

The installed metadata identifies **PyMuPDF 1.27.2.3** as “AGPL-3.0 or Artifex
commercial” and **EbookLib 0.20** as AGPL-3.0. Both are imported in-process by the Reader.
The previous version of this document incorrectly classified EbookLib as BSD and asserted
that an ordinary Python import necessarily left Mumble's combined distribution under MIT.
That legal conclusion is withdrawn.

The engineering policy is deliberately conservative:

1. Do not ship a new public archive or installer containing either package until the
   relationship and complete corresponding-source obligations have been reviewed.
2. Prefer replacement with currently verified permissive components where functionality
   and security are acceptable; otherwise obtain suitable commercial terms or specialist
   open-source counsel.
3. If either package remains, include its verbatim licence, exact source/version offer,
   modification status and any required installation information in the release bundle.
4. Re-evaluate all obligations if Reader functionality is ever exposed over a network.
5. Do not treat this engineering summary as legal advice or as permission to distribute.

Removing or properly replacing these packages leaves Mumble's own MIT licence unchanged.
Every candidate replacement still requires a current licence, dependency and asset review.

---

### 13.4 Compliance Summary by License Type

| License | Count | Dependencies | Obligation Level |
|---------|-------|-------------|-----------------|
| MIT | 11 | faster-whisper, CTranslate2, keyboard, mouse, bottle, python-docx, beautifulsoup4, python-pptx, openpyxl, pyannote.audio, psutil | **Minimal** — retain copyright notice. |
| BSD-3-Clause | 5 | numpy, pywebview, pyperclip, soundfile, scipy | **Minimal** — retain copyright + conditions + disclaimer. |
| AGPL-3.0 | 2 | PyMuPDF (fitz), EbookLib | **Release-blocking compatibility review** — see §13.3. |
| LGPL-3.0 | 1 | pystray | **Moderate** — modified versions must be shared; dynamic linking is OK. |
| Apache-2.0 | 1 | odfpy | **Low** — retain license notice; state changes if modified. |
| MIT-CMU | 1 | Pillow | **Minimal** — retain licence and disclaimer. |

**Inventory note:** direct versions are governed by `requirements.txt`; this table also
includes selected transitive and optional components. A clean-build SBOM is still required.
psutil is a development/test dependency; pyannote.audio is optional and model terms are separate.

---

### 13.5 Attribution Format

When distributing Mumble in binary form, the following attribution text satisfies the
notice requirements for permissive-licensed dependencies:

> This product includes software developed by:
> - The faster-whisper and CTranslate2 contributors (MIT)
> - The PortAudio and sounddevice contributors (MIT-style / MIT; verify bundled native notice)
> - The NumPy contributors (BSD-3-Clause)
> - The pywebview contributors (BSD-3-Clause)
> - The pystray contributors (LGPL-3.0)
> - The Pillow contributors (MIT-CMU)
> - The keyboard and mouse contributors (MIT)
> - The pyperclip contributors (BSD-3-Clause)
> - The Bottle contributors (MIT)
> - Artifex Software, Inc. — PyMuPDF (AGPL-3.0)
> - The python-docx contributors (MIT)
> - The Beautiful Soup contributors (MIT)
> - The EbookLib contributors (AGPL-3.0; distribution blocked pending compatibility resolution)
> - The odfpy contributors (Apache-2.0)
> - The python-pptx contributors (MIT)
> - The openpyxl contributors (MIT)
> - The soundfile / libsndfile contributors (BSD-3-Clause)
> - The SciPy contributors (BSD-3-Clause)
> - The pyannote.audio contributors (MIT)
> - The psutil contributors (MIT)

All trademarks and registered trademarks are the property of their respective owners.
