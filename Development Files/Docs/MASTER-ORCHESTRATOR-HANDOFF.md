# Mumble master Orchestrator handoff

**Prepared:** 26 July 2026

**Purpose:** transfer the active Mumble programme to a new AI without losing the route, evidence, Git history, issue dependencies, owner intent, or verification boundaries.

**Repository:** `C:\Mumble` and [mongre25-droid/mumble](https://github.com/mongre25-droid/mumble)

**Product source version:** Mumble 0.95

**Transfer mode:** pause after publishing this handoff; do not start new feature work in the closing session.

---

## 1. Identity and first instruction for the receiving AI

You are the **Orchestrator supervisor** for the existing Mumble supervised-execution programme. You are not starting a greenfield project and you are not being asked to redesign the route from scratch.

Start by reading, in this order:

1. `C:\Mumble\AGENTS.md`.
2. `C:\Mumble\Development Files\GitHub\AGENTS.md`.
3. all four authoritative Core records:
   - `C:\Mumble\Development Files\Core\README.html`;
   - `C:\Mumble\Development Files\Core\STATUS.html`;
   - `C:\Mumble\Development Files\Core\HANDBOOK.html`;
   - `C:\Mumble\Development Files\Core\LOGS.html`.
4. this handoff completely.
5. `C:\Users\khale\.codex\skills\orchestrator\SKILL.md` and its live universal companion `C:\Users\khale\.codex\skills\orchestrator\WORKFLOW.md`.
6. GitHub umbrella specification [#12](https://github.com/mongre25-droid/mumble/issues/12), the next issue bodies, and the latest comments on the issues you are about to launch.

Treat live Git, the Core records, the actual issue bodies/comments, and exact commit receipts as evidence. Do not infer completion from a label, branch name, workspace, old plan, or chat summary.

---

## 2. Owner outcome and non-negotiable intent

The owner wants Mumble brought from its strong prototype state to a fast, trustworthy, centred, premium desktop voice workspace across Windows, macOS, and Linux.

The original programme includes:

- truthful long-duration dictation rather than an unexplained ten-minute claim;
- segmented durable capture, stable partials, warm-state and full latency measurement;
- reliable insertion into the user's actually selected target, including ChatGPT/Codex and ordinary Windows applications;
- a fast draggable local application/file launcher called **Mumble Find**;
- a separate consent-gated **Web Search** for Google, Perplexity, Brave, and supported providers;
- a centred visual language across Home, Deck, Stats, Meetings, Reader, Settings, the main Island, and the secondary Island;
- a clearer Home shortcut surface and plain-language processing/transcription settings;
- a premium unified Deck command surface and first-class image clipboard actions;
- premium Meetings recording and Island controls with a clear Stop or Cancel action;
- deep, reproducible local-AI and open-source dictation research with licence tracking;
- truthful CI that runs the intended tests rather than merely appearing green;
- real functional parity and physical acceptance on Windows, macOS, and Linux.

Critical owner intent for paste behaviour:

- Mumble should paste where the user is editing when dictation finishes.
- If the user changes applications while speaking, the target selected at Stop/finalisation is the intended target unless a deliberately specified product rule says otherwise.
- Do not introduce a generic “unsupported text field” block.
- Integrity-level restrictions, clipboard ownership, focus restoration, exact-once delivery, and safe recovery must be solved as engineering boundaries, not used to reject normal targets.
- Do not claim success before text is actually visible in the target.
- The reported ChatGPT/Codex delay remains a physical acceptance gate even though controlled test fields are fast.

The owner strongly prefers the interface to remain centred and expects the launcher, Deck, Meetings widget, Island states, Reader, Stats, and Settings to feel like one coherent premium product.

---

## 3. Communication and operating rules

The owner is a beginner. Explain unavoidable technical words in ordinary English.

For user-visible responses in this repository:

- the first response starts exactly `Prompt start,`;
- later updates for the same unfinished task start exactly `Prompt continuing,`;
- `Prompt end.` is used only when nothing remains active and the requested task is genuinely complete;
- immediately before `Prompt end.`, include the compact required TL;DR block from `AGENTS.md`;
- lead with the practical outcome;
- clearly separate source, tests, CI, physical evidence, installation, deployment, release, and owner acceptance.

The owner previously selected **Undisturbed mode** for the programme. Work continuously through safe, authorised tasks without routine questions. This never authorises public releases, spending, credentials, destructive operations, signing identities, or other owner-only decisions.

The owner later explicitly requested this rapid handoff and pause. Therefore the closing session must not launch Issues #21–#30. The receiving AI resumes them in a new orchestrated session.

---

## 4. Corrected orchestration method

An earlier attempt created useful GitHub planning and CI work but did not correctly invoke Ask Matt or supervise the complete programme. The Ask Matt/Orchestrator setup was repaired afterward.

The durable workflow is now:

1. Ask Matt is the normal router for a new or materially changed/resumed programme.
2. It runs as a separate visible task titled **Ask Matt** using the literal `/ask-matt` skill.
3. The exact prompt sent to Ask Matt is shown before launch.
4. Ask Matt's raw answer is shown in full as the Route Receipt.
5. The Orchestrator shows the route board and complete refined Prompt Packet before launching work.
6. Named research, diagnosis, implementation, independent review, convergence, and final acceptance run in separate visible tasks.
7. The Orchestrator supervisor may use hidden helpers only for bounded non-deliverable support. It must not replace a named routed deliverable with a hidden helper.
8. A destination implementation task may use its own internal helpers at its discretion and remains responsible for their work.
9. Substantial implementation normally uses the strongest SOL model with high reasoning at standard pace. A lighter model is appropriate only when the expected quality difference is negligible or the owner explicitly requests speed.
10. Every launch receives the complete living Prompt Packet, not just an issue URL.
11. Each candidate returns an immutable receipt: exact commit, parent, workspace/ref, clean state, checks, first failures, record changes, and remaining gates.
12. A separate read-only task reviews the exact candidate.
13. Accepted candidates converge in a dedicated integration task.
14. Newly dependent tasks start from the newest verified integration baseline, not stale `main` or an unfinished sibling branch.
15. High-leverage Level A checks happen per task; broad Level B checks and exact-head CI happen once per meaningful combined wave; real-world Level C checks remain separate.

Do not rerun Ask Matt merely to recreate work already routed. Reuse the existing route and this dependency board unless the owner materially changes the outcome or explicitly asks for a new Ask Matt receipt. If a reroute is genuinely required, follow the visible separate-task rule exactly.

---

## 5. Repository and publication truth at transfer

The product working copy is `C:\Mumble` on branch `main`.

Before this handoff was committed, its exact accepted product head was:

```text
3d04e85d361446da58296a90aae508bb0185bf97
```

That commit is the direct correction child of the #18/#19 convergence. The handoff/Core publication commit sits above it. On receipt, run `git rev-parse HEAD` to obtain that documentation publication head and verify `git status --short --branch` is clean.

The remote is:

```text
https://github.com/mongre25-droid/mumble
default branch: main
visibility at handoff preparation: PUBLIC
```

The repository was made public by explicit owner authorisation so GitHub-hosted Actions minutes would not remain blocked by private-repository included-minute limits. Public visibility is a real exposure boundary. Do not assume obscurity protects secrets. No secret is intentionally recorded in this handoff; continue to keep credentials, private settings, `.env` files, caches, and ignored runtime data out of Git.

The last previously published remote `main` was:

```text
276619e15320f016af6576425fc0e563c299e0b4
```

Its exact-main CI run [30186499494](https://github.com/mongre25-droid/mumble/actions/runs/30186499494) passed all 14 required jobs.

The closing session is authorised to publish the current linear/history-preserving lineage by a normal non-force push. The receiving AI must verify the actual remote head and the new exact-head CI run before treating publication or CI as complete.

Never use force push, hard reset, destructive cleanup, or issue deletion for tidiness.

---

## 6. Exact accepted and rejected lineage

The important lineage is:

```text
276619e15320f016af6576425fc0e563c299e0b4  previous published main and green CI
  ... accepted local programme history ...
65f27576f093553418540abe58501043971e8b1f  shared verified baseline before #18/#19
├─ 8e93c8139ab1a5e4bd3e84811fcccc4a2ae6d1b6  independently accepted #18 Web Search
└─ e872cfdf6ace7be3cb60343a305904ca05ed52e9  independently accepted #19 local-AI evidence

52b06b8ee98ba8ef3b2029347a14eae818b8ac70  ordered three-parent convergence
  parents, in order:
  1. 65f27576f093553418540abe58501043971e8b1f
  2. 8e93c8139ab1a5e4bd3e84811fcccc4a2ae6d1b6
  3. e872cfdf6ace7be3cb60343a305904ca05ed52e9

3d04e85d361446da58296a90aae508bb0185bf97  accepted direct correction child
  sole parent: 52b06b8ee98ba8ef3b2029347a14eae818b8ac70
```

The convergence `52b06b8` was deliberately retained in history even though review rejected it. That preserves evidence rather than rewriting it. Its independent review found:

1. caller-invented local-AI evidence could grant eligibility;
2. startup could create shortcut collisions with existing global commands;
3. a false browser-opener result could be reported as success;
4. Core leading truth still described the pre-convergence state.

Correction `3d04e85` reproduced and corrected those findings. Follow-on review also caught and corrected:

- a replacement race between receipt hashing and parsing;
- Linux/macOS failed-rebind live-hook state inconsistency;
- a stale Issue #19 status row.

The final independent re-review accepted exact `3d04e85` with zero actionable P0–P3 findings. It verified the sole parent, unchanged ordered convergence history, clean detached review state, same-byte hash/parse evidence, live-hook rebinding truth, all nine candidates non-eligible, and current Core truth.

---

## 7. Mumble runtime truth

After local main fast-forwarded to `3d04e85`, Mumble was restarted from the canonical hidden launcher.

The authenticated local command channel returned:

```json
{"ok":true,"state":"idle","text":"Ready","recording":false,"readiness":"cold"}
```

The maintained Windows launcher verifier passed:

- manual launcher;
- runtime and entry point;
- icon;
- Start-menu shortcut;
- Windows-startup shortcut;
- desktop shortcut;
- Startup Apps path;
- canonical path checks.

This is startup evidence only. It is not proof of a clean installer, physical microphone, genuine provider, ChatGPT/Codex paste timing, macOS/Linux physical parity, deployment, public release, or owner acceptance.

At transfer, Mumble should still be running from the current local `main`. Recheck the authenticated status before using that as current evidence.

---

## 8. Programme issue ledger

The formal umbrella specification [#12](https://github.com/mongre25-droid/mumble/issues/12) remains open. It is not a delivery claim.

| Issue | Title | Tracker state | Source/evidence truth at handoff |
|---|---|---:|---|
| [#13](https://github.com/mongre25-droid/mumble/issues/13) | CI truth | Closed | Completed and published; exact-main CI proved the maintained jobs. |
| [#14](https://github.com/mongre25-droid/mumble/issues/14) | Processing truth | Open | Accepted source is in main history; live-provider and physical gates remain. |
| [#15](https://github.com/mongre25-droid/mumble/issues/15) | Reliable insertion | Open | Accepted protections and focused corrections are in main history; physical ChatGPT/Codex timing, installed helper/elevation, CI and owner gates remain. |
| [#16](https://github.com/mongre25-droid/mumble/issues/16) | Long dictation | Open | Accepted segmented durable-capture source is integrated; genuine microphone/endurance, installed, CI and platform gates remain. |
| [#17](https://github.com/mongre25-droid/mumble/issues/17) | Mumble Find | Open | Accepted local indexed overlay/native-action source is integrated; physical Search freshness, drag and installed performance remain. |
| [#18](https://github.com/mongre25-droid/mumble/issues/18) | Web Search | Open | Accepted source and accepted correction are integrated; genuine providers, physical shortcuts/conflicts, package/install/CI remain. |
| [#19](https://github.com/mongre25-droid/mumble/issues/19) | Local AI evidence | Open | Reproducible evidence system integrated; no model adopted; formal corpus/matrices/licence/hardware/package/physical gates remain. |
| [#20](https://github.com/mongre25-droid/mumble/issues/20) | Focus Stage foundation | Open | Accepted source is in main history; physical display and assistive-technology gates remain. |
| [#21](https://github.com/mongre25-droid/mumble/issues/21) | Home and Settings | Open | Not started. Source dependencies are now satisfied. Launch in next parallel wave. |
| [#22](https://github.com/mongre25-droid/mumble/issues/22) | Premium Deck | Open | Not started. Source dependencies are now satisfied. Launch in next parallel wave. |
| [#23](https://github.com/mongre25-droid/mumble/issues/23) | Meetings and Island Control Rail | Open | Not started. Source dependencies are satisfied. Launch in next parallel wave, but isolate overlapping UI/Core files. |
| [#24](https://github.com/mongre25-droid/mumble/issues/24) | Stats and Reader | Open | Independently accepted source is integrated; physical screen-reader/display, package/CI and owner gates remain. |
| [#25](https://github.com/mongre25-droid/mumble/issues/25) | Windows acceptance | Open | Blocked on source work #21–#23 and the combined accepted baseline. |
| [#26](https://github.com/mongre25-droid/mumble/issues/26) | macOS functional parity | Open | Blocked on completion/convergence of #14–#24. |
| [#27](https://github.com/mongre25-droid/mumble/issues/27) | Linux functional parity | Open | Blocked on completion/convergence of #14–#24. |
| [#28](https://github.com/mongre25-droid/mumble/issues/28) | macOS acceptance | Open | Blocked on #26 plus physical permissions, hardware, signing/notarisation and packaging resources. |
| [#29](https://github.com/mongre25-droid/mumble/issues/29) | Linux acceptance | Open | Blocked on #27 plus physical X11/Wayland, desktop and package matrices. |
| [#30](https://github.com/mongre25-droid/mumble/issues/30) | Final parity and promotion gate | Open | Blocked on #25, #28 and #29. |

Planning/research Issues #1–#11 are closed records. They are useful evidence but not unquestionable truth. Keep, amend, supersede, or mark obsolete only when the current workflow and evidence justify it. Do not delete or rewrite history for neatness.

GitHub labels were stale at preparation time. Examples: accepted work could still carry `ready-for-agent`, while merged source could still carry `state: blocked`. The Core ledger and exact receipts are more current than label text. Reconcile labels deliberately only after confirming the repository's configured tracker rules; do not invent a new label process.

---

## 9. Exact #18 Web Search receipt

Accepted candidate:

```text
8e93c8139ab1a5e4bd3e84811fcccc4a2ae6d1b6
parent: 65f27576f093553418540abe58501043971e8b1f
```

GitHub receipt:

<https://github.com/mongre25-droid/mumble/issues/18#issuecomment-5084991341>

Implemented boundary:

- Web Search is separate from local Mumble Find.
- It has its own global command, shortcut, callback, migration, registration, toggle, and active-dictation path.
- Selected words remain in a bounded local request until explicit provider-named consent.
- Google, Perplexity, and Brave are explicit one-shot routes.
- cancellation, expiry, replay, or failed consent surface sends nothing online;
- layout-independent physical scan code 41 can represent Alt plus the key below Escape without forcing it as the default;
- Flow Launcher collisions retain the previous binding and show an explanation;
- Home exposes Dictate, Paste latest, Open Deck, Mumble Find, and Web Search;
- Mumble Find remains local with exactly six top-level destinations;
- Windows, Linux, and macOS maintained seams were covered;
- corrected startup and rebinding compare canonical physical chords across all live global commands;
- a conflicting Web Search binding remains unregistered and retryable rather than stealing or sharing a chord;
- browser success requires the platform opener to report success.

Focused evidence:

- 81 tests passed;
- one platform-only skip;
- 25 subtests passed;
- standalone Web API check passed;
- production-browser proof retained six destinations and five Home globals;
- local provider doubles only; no genuine provider was contacted.

Open gates:

- genuine Google, Perplexity, and Brave behaviour;
- physical shortcut/conflict interaction on Windows, macOS, and Linux;
- installation and package behaviour;
- exact-head CI on the publication head;
- deployment, release, rollback, and owner acceptance.

---

## 10. Exact #19 local-AI evidence receipt

Accepted candidate:

```text
e872cfdf6ace7be3cb60343a305904ca05ed52e9
parent: 65f27576f093553418540abe58501043971e8b1f
```

GitHub receipt:

<https://github.com/mongre25-droid/mumble/issues/19#issuecomment-5084961041>

Implemented evidence system:

- a versioned synthetic seven-fixture corpus manifest;
- a predeclared eight-gate policy;
- nine candidate inventory entries;
- exact source and licence fields;
- a content-free result ledger;
- a named Windows hardware/software record;
- hash-verified model cache and fallback logic;
- strict metric domains and per-mode evidence;
- bounded HTTPS/hash/cache recovery;
- deterministic Prompt, Email, Reply, and classification fixture smoke;
- approved immutable run receipts whose source, corpus, hardware, repetitions, tool, metrics, and gate records are opened and hash-verified from the same bytes used for parsing;
- manual or owner labels cannot grant automated eligibility;
- the current approval registry is empty, so all nine candidates remain non-eligible;
- both existing baselines remain and no new candidate was adopted.

Preliminary cached-only result:

| Measurement | Result |
|---|---:|
| Candidate | existing faster-whisper `small.en` snapshot |
| Word error rate | 0.20 |
| Stop to final | 2965.109 ms |
| Real-time factor | 0.636519 |
| Peak RAM | 702.246 MB |
| Resident idle RAM | 318.977 MB |
| One-second resident idle CPU | 1.6% |
| Model bytes | 486,098,798 |
| Private audio used | No |
| Model downloaded | No |
| Stable partial | Not available in this completed-utterance path |
| Failure recovery | Not run |

This is one synthetic SAPI measurement, not a 30-run formal benchmark or adoption decision.

Focused evidence:

- initial candidate passed 11 focused tests;
- final correction boundary passed a combined 49 tests and 28 platform variations;
- nine candidates and seven fixtures validated;
- changed Python syntax, JSON, Core structure, whitespace, and ancestry checks passed.

Open gates:

- owner-approved versioned speech corpus and baseline snapshot;
- approved-runner receipts;
- 30-run cold/warm quality, latency, recovery, and resource matrices;
- candidate runtimes and model weights;
- licence and package permission per candidate;
- suitable strong hardware for Parakeet evaluation;
- installed package, physical platforms, exact-head CI, deployment, release and owner acceptance.

---

## 11. Dependency board

An arrow below means the issue on the left depends on the issue or issues on the right:

```text
#14 <- #13
#15 <- #13
#16 <- #13, #15
#17 <- #13, #15
#18 <- #14, #17
#19 <- #13, #14, #16
#20 <- #13
#21 <- #14, #16, #18, #20
#22 <- #14, #15, #18, #20
#23 <- #14, #16, #20
#24 <- #14, #20
#25 <- #14–#19, #21–#24
#26 <- #14–#24
#27 <- #14–#24
#28 <- #26
#29 <- #27
#30 <- #25, #28, #29
```

Current source-complete/accepted programme items are #13, #14, #15, #16, #17, #18, #19, #20, and #24: **9 of 18 implementation issues**. This does not mean those nine are physically accepted or released.

Not-started implementation items are #21, #22, #23, #25, #26, #27, #28, #29, and #30: **9 of 18**.

The next source wave is #21, #22, and #23 in parallel, each from the same exact publication baseline. They are independent in outcome but likely overlap shared Web UI and Core files, so they must use isolated workspaces and converge deliberately.

---

## 12. Next-wave launch instructions

### Wave A: #21, #22, and #23

Launch three separate visible implementation tasks after verifying the new published `main` and exact starting hash.

Each complete Prompt Packet must include:

- literal selected skill on the first line;
- the full issue objective and acceptance criteria from GitHub;
- the exact current verified integration baseline;
- accepted dependencies and their exact commits;
- the owner intent in sections 2 and 3 of this handoff;
- relevant prior research/spec/prototype evidence;
- precise scope and exclusions;
- isolation/workspace rule;
- Level A risk-weighted checks;
- Core record duties;
- one immutable completion receipt;
- explicit remaining physical/package/CI/release gates;
- instruction not to close the issue or self-approve.

Use strong SOL/high reasoning at standard pace unless the task is genuinely trivial. The implementation task may use its own internal helpers. The Orchestrator must not implement these three issues in hidden supervisor helpers.

### Independent reviews

For each exact candidate, launch a separate read-only review task with:

- the full current Prompt Packet;
- exact candidate and parent;
- clean/detached preflight;
- P0–P3 findings;
- separate Standards and Specification axes;
- acceptance or rejection of only that exact hash.

### Corrections

For a rejection, launch a fresh correction task from the exact rejected commit. Include every retained finding. Reproduce each red when feasible, correct it, run focused green evidence, create one new exact candidate, and send that new candidate to a fresh read-only review.

### Wave convergence

After all accepted candidates are available, launch a dedicated convergence task from the newest verified baseline. Preserve accepted parent history, reconcile overlaps deliberately, run focused combined checks, update Core, and independently review the exact combined head.

Only after that convergence should #25, #26, and #27 be considered for launch.

---

## 13. Remaining acceptance waves

### #25 Windows acceptance

This is not merely another source task. It must cover the real Windows package and lifecycle, physical application targets, clipboard/focus behaviour, shortcuts, Search freshness, native drag, microphone/endurance, performance, installation, upgrade, rollback, and owner-visible outcomes.

### #26 and #27 functional parity

These can begin in parallel after #14–#24 converge. They must compare every actual shared contract against Windows and implement only genuine platform seams. A successful build is not functional parity.

### #28 and #29 physical platform acceptance

These depend on access to suitable machines and owner-controlled permissions. macOS includes Accessibility, microphone, input monitoring, menu bar, focus, signing/notarisation and supported architectures. Linux includes X11/Wayland, desktop environments, clipboard/text insertion, sound systems, trays, focus, packages and distribution dependencies.

### #30 final parity and promotion

This is the final evidence ledger across Windows, macOS, and Linux. It depends on #25, #28, and #29 and must keep source, CI, physical, installation, signing, deployment, release, rollback, and owner approval separate. Do not promote merely because all platforms build.

---

## 14. Verification policy and time-saving method

The professional universal policy is:

```text
C:\Users\khale\.codex\skills\orchestrator\WORKFLOW.md
```

The old historical transfer document was renamed to:

```text
C:\Users\khale\Documents\Orchestrator-Workflow-Handoff.md
```

The live workflow is called **risk-weighted delivery** or **high-leverage verification**, not “the 80/20 file.” Its important controls are:

- focused Level A per task;
- never omit the narrow critical privacy/security/clipboard/data-loss/installer check;
- independent delta-first review;
- fresh correction sessions after rejection;
- one Level B broad runner/package/CI set per combined wave;
- real Level C checks only when real applications, hardware, platforms, permissions, installers, signing, deployment, or release are available;
- one full application runner per host at a time;
- serialize focus, clipboard, browser, microphone, native window, installer and package resources when interference would invalidate evidence;
- reuse valid deterministic evidence only when exact commit, input and environment remain applicable;
- batch tracker and documentation administration at meaningful milestones;
- preserve first failures and stop rerunning work that produces no new evidence;
- create a clean master handoff before context or usage exhaustion.

This keeps the valuable parts of test-driven work and independent review while reducing repeated broad suites and administrative overhead.

---

## 15. GitHub and CI rules

GitHub Issues are evidence and coordination records because the configured Matt Pocock workflow selected them. They do not replace Core current truth and they do not prove implementation.

Use:

- issue bodies for durable scope/specification;
- issue comments for exact immutable candidate, review, convergence, publication, CI and physical receipts;
- labels only after confirming their configured meaning;
- non-force history-preserving Git operations;
- exact-head CI after publication.

Before a push:

1. fetch `origin/main`;
2. verify the remote relationship and ancestry;
3. verify clean porcelain;
4. push normally, never force;
5. capture the exact run URL and `headSha`;
6. read failed job steps and annotations before calling a failure a product defect.

A zero-step failed job with billing/payment/spending annotations is an account blocker, not a test result. A skipped manual promotion job on a push event can be correct. Green CI proves only the jobs it actually ran.

The repository is public at handoff. Public repositories can receive hosted Actions usage under GitHub's current terms, but account policies can change. Verify current repository/account truth rather than relying on this historical reason.

---

## 16. Evidence already separated correctly

Keep these distinctions:

- **planning/research/prototype:** Issues #1–#11 and research/prototype files;
- **formal specification:** open umbrella #12;
- **implementation records:** #13–#30 issue bodies/comments;
- **execution sessions:** visible tasks that produce candidates or verdicts;
- **code integrated locally:** exact local `main` ancestry;
- **published code:** exact `origin/main` ancestry after push;
- **CI:** exact run against the exact published head;
- **runtime startup:** authenticated Ready/idle receipt and launcher verification;
- **physical testing:** real applications, hardware, targets, shortcuts and platforms;
- **installation/package:** clean installed artifact behaviour;
- **deployment/release:** actual promoted/public artifact;
- **owner acceptance:** explicit owner decision after evidence.

Never compress these into one word such as “done.”

---

## 17. Important previous session/task receipts

The following visible Codex tasks were part of the recent programme. They are historical execution evidence, not substitutes for commits or GitHub receipts:

- `019f9f5d-0304-75d2-85b3-d4a61113ca96`
- `019f9f5d-0304-75d2-85b3-d4b288b765e0`
- `019f9f8b-ad51-7410-910c-50aad5904a27`
- `019f9f98-92ec-7ef1-bdc6-24782b330bc9`
- `019f9fa6-e852-7841-8d70-1bf63796fc42`
- `019f9fa6-e852-7841-8d70-1c1dd9576fc3`
- `019f9fdd-5899-74e1-8245-f730ddb56cc1` — #18/#19 convergence;
- `019f9fe4-5ae7-7763-8798-976caa03d197` — independent convergence review, later final ACCEPT re-review;
- `019f9ff0-4faf-7061-b5d3-50e0abd290c6` — correction of #18/#19 convergence.

Do not resume an old implementation task merely because it exists. Start the next named issue in a fresh visible task from the current verified baseline.

---

## 18. Known open product and evidence risks

1. The ChatGPT/Codex paste-visible delay needs physical reproduction and timing. Controlled fields are not enough.
2. Elevated target applications need a properly authenticated/signed helper boundary rather than a blanket field restriction.
3. Long dictation needs genuine microphone/endurance and restart testing.
4. Mumble Find needs real Windows Search freshness, native physical drag and installed performance evidence.
5. Web Search needs genuine provider and physical shortcut/conflict testing without weakening explicit consent.
6. The local-AI work has a strong evidence harness but intentionally no adopted candidate.
7. #21–#23 will likely touch shared Web UI/Core files and must be isolated then deliberately converged.
8. macOS and Linux remain source and physical parity programmes, not simple build jobs.
9. Signing, notarisation, publisher identity, public release, spending and credentials remain owner-controlled.
10. GitHub labels lag source truth and must be reconciled carefully.
11. Public repository visibility increases exposure; continue secret hygiene.

---

## 19. Time estimate from this handoff

These are active-work estimates, not calendar guarantees:

| Stage | Estimate |
|---|---:|
| #21–#23 parallel source wave, reviews, corrections and convergence | 3–6 hours |
| #25 Windows acceptance | 2–4 hours when physical access is available |
| #26/#27 functional parity | 6–12 hours |
| #28/#29 physical platform acceptance | 4–10 hours when machines/credentials are ready |
| #30 final ledger and promotion preparation | 2–4 hours |
| Remaining source through parity | about 12–22 active hours |
| Full programme including physical/release gates | about 20–35 active hours, usually 3–7 calendar days |

Hardware, signing identities, notarisation, distribution credentials, owner availability and physical bug findings can extend the calendar time.

---

## 20. Exact receiving checklist

Do this before launching the next wave:

1. Read the files listed in section 1.
2. Run read-only Git checks for current branch, head, parents, remote head, ancestry and clean porcelain.
3. Verify GitHub repository visibility and issue states.
4. Find the exact new `main` Actions run triggered by the closing publication commit and record its `headSha`, URL, status and conclusion.
5. Read the latest comments on #12, #18 and #19.
6. Confirm Mumble's authenticated runtime status if the running app matters to the next task.
7. Reconcile any changed external truth into STATUS and the relevant issue comments.
8. Build complete visible Prompt Packets for #21, #22 and #23 using their issue bodies plus this handoff.
9. Launch them separately from the same exact clean baseline.
10. Supervise candidates, independent reviews, corrections and convergence according to `WORKFLOW.md`.

Stop and ask the owner only for a genuine owner-controlled blocker. Do not ask routine questions that can be answered from the repository, issues, Core, or safe inspection.

---

## 21. Closing truth

At the moment this handoff was written:

- #18 and #19 were independently accepted and integrated locally through correction `3d04e85`;
- Mumble was restarted from that source and reported Ready/idle;
- the owner explicitly requested rapid publication, handoff and pause;
- no #21–#30 feature implementation was started in the closing session;
- no physical, installed, deployed, released or owner-accepted claim was created by this handoff;
- the next AI must verify the actual publication head and exact-head CI because those are external state and may change after this file is committed.

This file is intentionally detailed so a new AI can continue without reconstructing the programme from the huge prior chat. The four Core records remain the concise authoritative product truth; this is the explicit transfer packet requested by the owner.
