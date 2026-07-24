# How I Engineer — Analysis & Integration Blueprint for Mumble

**Source:** [github.com/AnasInno/how-i-engineer](https://github.com/AnasInno/how-i-engineer)  
**Date:** 2026-06-28  
**Target:** Mumble v0.9 ("The Big Shift")

---

## 1. What "How I Engineer" Actually Is

"How I Engineer" (HIE) is not a framework, library, or product. It is a **published operating pattern** — a set of habits, file structures, contract templates, and runnable examples that encode one person's method for shipping AI-assisted software without the usual chaos. It was extracted from real product work (a teacher-facing tool called TeachClaw) and scrubbed of private data so others can study, fork, and adapt the pattern.

The central insight: **most failed AI-augmented projects fail not because the model is weak, but because the operating model is weak.** Vague briefs, orchestration before understanding, no deterministic baseline, hidden local state, no proof chain, accidental leaks — these are engineering failures, not intelligence failures.

### The pipeline in one line

```
problem conversation → tight brief → smallest slice → orchestrator (if needed) → build → eval → autoreview → proof → PR → CI/CD → merge
```

### The repo shape

| Directory | Contents |
|---|---|
| `.agents/skills/` | Task-specific operating procedures (conversation briefs, workflow shipping, orchestration, autoreview, QA, evals, Crabbox proof, PR/ship safety) |
| `codex/how-i-engineer/` | Agent router (`LOAD-FIRST.md`) + compact operating doctrine (`KERNEL.md`) |
| `AGENTS.md` | Root router — what any agent should read first |
| `docs/` | Build loop, orchestrator/worker system, eval framework, Crabbox, QA/browser E2E, PR/ship safety, scrub boundaries |
| `ops/contracts/` | Validation gate rules and worker-brief templates |
| `ops/evals/` | Eval contracts — scenario packs, rubrics, scorecards |
| `templates/` | Reusable automation brief template |
| `examples/` | Two runnable scrubbed TeachClaw examples: proof loop + eval harness |
| `scripts/` | Public safety scanner, example verifier, repo doctor |

Single command: `make check` (smoke tests + unit tests + eval tests + safety scan).

---

## 2. The Six Operating Principles (KERNEL.md)

### 1. The Promise
> "The work only counts if less useful human work exists afterwards."

Prefer real user pain over impressive agent theatre, useful output over process narration, deterministic baseline over model dependency, minimal code over broad architecture, evals over one nice output, proof over vibes.

### 2. The Truth Model
> "Current files beat old docs."

Use docs and WORK-DONE.md for history. Use git status, tests, generated outputs, CI/CD checks, and safety scans for what is true *now*. Never create stale "current state" docs.

### 3. The Autonomy Boundary
> "Local autonomy is high. External autonomy is low."

Agents may act on code, docs, tests, fake data, and local proof. They must **stop** before sending email, writing to CRMs, posting publicly, mutating live systems, scraping private systems, or publishing secrets.

### 4. The Slice Rule
> "Build the smallest coherent version that produces useful output and can be proved."

Smallest coherent = one persona, one input shape, one output format, one smoke command, one clear proof standard. Avoid platform building, broad integrations, and AI as the only source of correctness.

### 5. The Autoreview Rule
> "Non-trivial work gets a separate review pass before it is called ready."

Autoreview checks: brief fit, minimality (did every file earn its place?), proof claims (closeout only claims what was actually run?), safety (no leaks). The reviewer may recommend deleting work.

### 6. The Evidence Ladder
> "Do not let one layer stand in for another."

Eleven distinct proof levels — from "file exists" up to "PR/ship safety scan." Each level must be independently satisfied. A green smoke test does not prove output quality.

---

## 3. The 11-Worker Model

HIE models work as **bounded worker lanes**, each with narrow scope. No worker does everything:

| Role | Responsibility | Real-world analogue |
|---|---|---|
| **Problem Conversation** | Turns vague request into grounded slice: user, pain, input, output, risk, limits, proof | Product manager |
| **Orchestrator** | Defines worker lanes, acceptance tests, non-goals, allowed data, evidence requirements | Tech lead |
| **Research Worker** | Checks workflow reality, data shape, rejection risks | UX researcher |
| **Builder Worker** | Implements CLI/local app — deterministic core first, AI only where it helps | Senior engineer |
| **Eval Worker** | Scenario packs, rubrics, scorecards, strategy comparison | QA engineer |
| **Autoreview Worker** | Brief fit, minimality, overengineering, proof claims, safety — independent review | Code reviewer |
| **QA Worker** | Tests, failure modes, docs, .env boundaries, leak-prone files | Penetration tester |
| **Browser E2E Worker** | Real owner-facing browser path: open, enter, run, inspect | E2E test engineer |
| **Computer Use Worker** | OS-level flows: file pickers, PDFs, native dialogs, drag/drop | Desktop tester |
| **Crabbox Runner** | Isolated remote proof: lease box → hydrate → sync → run → collect → release | CI runner |
| **PR / Ship Worker** | Merge-ready shape: clean git, safety scan, no junk, no private history | Release engineer |

---

## 4. The 11-Rung Proof Ladder

HIE defines 11 **non-substitutable** proof levels:

| Rung | Proof type | What it means |
|---|---|---|
| 1 | Code shape | Files exist; commands documented |
| 2 | CLI smoke | Sample input produces sample output |
| 3 | Tests | Important behavior + failure paths checked |
| 4 | Eval scorecard | Scenario pack + rubric + quality verdict |
| 5 | Output quality | Human can inspect usefulness |
| 6 | Browser proof | Visible local app path actually run + recorded |
| 7 | Computer proof | OS-level visible path actually run + recorded |
| 8 | Tool readiness | Required tools checked before proof run |
| 9 | Crabbox proof | Isolated run evidence actually collected |
| 10 | Autoreview | Brief fit, minimality, proof claims, safety independently checked |
| 11 | PR/ship safety | No secrets, private paths, generated junk, unsafe actions |

**Weak proof** (what does NOT count): "it builds," "the model answered," "file exists," "browser opened" (without completing user path), "tool installed" (without proof command using it).

**Closeout labels:** Every piece of work gets exactly one: `fail`, `needs repair`, `needs judgement`, or `merge candidate`. Never call work ready just because a command exited zero.

---

## 5. Mumble's Current Process (Side-by-Side)

### Mumble's Session Ritual (HANDBOOK §1)

**Start:** Read four core docs → restate request as ☐ checklist → read the actual code → confirm mode.  
**End:** Verify (tests + lint) → resolve checklist → update docs → commit → deploy → sign off.

### The Four Core Docs

| Doc | Purpose | HIE equivalent |
|---|---|---|
| **README** | Philosophy, standards, user-facing overview | KERNEL.md (the promise) |
| **HANDBOOK** | Architecture, data flow, rules, session ritual, port-sync, deploy | LOAD-FIRST.md + build-loop + orchestrator docs |
| **STATUS** | Current health, active priorities, known bugs, tech debt, roadmap, TODO | "Current truth" concept — inspect checkout |
| **LOGS** | Dated history of what shipped | WORK-DONE.md pattern |

### Testing

15 offline + 3 live test files (`test_*.py`) covering formatting, modes, bindings, presets, favorites, stream seams, WebUI API, UI, cloud sync, foreign boost, local engine, reader, and more.

### Bug-fixing protocol (HANDBOOK §7)
1. Reproduce first
2. Write a failing test
3. Fix; test goes green
4. Check all callers
5. Add regression note to STATUS

---

## 6. Full Comparison: HIE vs Mumble vs Factory Mission Control

| Dimension | How I Engineer | Mumble (current) | Factory Mission Control |
|---|---|---|---|
| **Entry point** | Problem conversation → brief | Request → read docs → checklist | Prompt → Mission Control → assign |
| **Work decomposition** | Orchestrator splits into 11 worker lanes | Single Droid agent, guided by docs + ritual | Mission phases, sub-droids, skills, tools |
| **Agent roles** | 11 distinct narrow workers | One Droid agent | Custom droids, skills, MCP servers |
| **Deterministic baseline** | Core rule: deterministic first | Strongly aligned — `formatting.py` is deterministic; cloud AI is opt-in | Not a first-order concern |
| **Proof ladder** | 11 explicit rungs; closeout labels required | Implicit: tests pass, docs match, checklist resolved | Mission verification; acceptance criteria |
| **Evals** | Scenario packs, rubrics, scorecards — central | No formal eval framework | Not built-in |
| **Autoreview** | Independent review for brief fit, minimality, safety | Session-end review serves similar gate | Mission validation |
| **Safety scan** | Mandatory pre-PR scan | Gitignore covers basics; no automated scan | Relies on gitignore + diligence |
| **Remote isolated proof** | Crabbox: lease → hydrate → sync → run → collect → release | None — everything runs locally | Cloud automations on remote droids (scheduled) |
| **Browser/Computer proof** | Explicit worker lanes for visible-path testing | `test_ui.py` exists but no automated browser/desktop proof | `agent-browser`, `tuistory`, `desktop-control` skills |
| **Docs as truth** | "Current files beat old docs" | Strongly aligned — status verified against code | Wiki generation + CI refresh |
| **Slice rule** | One persona, one input, one output, one smoke command | Not formalized; checklist naturally bounds work | Mission scope defined by prompt |
| **CI/CD** | GitHub Actions on push/PR | No CI/CD pipeline | `install-qa` skill; cloud automations |
| **Agent routing** | `AGENTS.md` + `LOAD-FIRST.md` | Four core docs = de facto router | System prompt + on-demand skills |

---

## 7. Where Mumble Already Aligns

**Strong alignments (require no change):**

- **Deterministic core before AI:** `formatting.py` (offline engine) is deterministic; `ai.py` (cloud) is opt-in and user-key-gated. This *is* the HIE rule.
- **Current files beat old docs:** Session-start ritual mandates reading actual code. STATUS is versioned and verified against the codebase.
- **Session ritual as operating loop:** The start/end ritual mirrors HIE's build loop — read, scope, build, verify, document, ship.
- **Checklist-driven work:** ☐ checklist maps to HIE's "tight brief." Unresolved items go into STATUS — similar to HIE closeout labels.
- **Privacy boundaries:** "Audio never leaves the device" is stronger than HIE's "don't publish secrets."
- **Test suite:** 15+ test files covering core behavior and failure paths (proof rungs 1-3).

**Partial alignments (could be strengthened):**

- Proof ladder is implicit, not explicit
- No formal agent-routing entry point (`AGENTS.md`)
- No automated pre-commit/pre-PR safety scanner
- No explicit "smallest coherent slice" discipline

---

## 8. What Mumble Could Absorb From HIE

### 8.1 Explicit proof ladder
Replace binary "tests pass/fail" with named levels. Session sign-offs become "L4 proved (tests + safety scan)" instead of "verified."

### 8.2 Pre-commit safety scanner
A lightweight Python script that checks: no `.env` files with real keys, no absolute paths, no `settings.json` committed, no generated zip files, no private session files. Run as part of session-end or as a pre-commit hook.

### 8.3 Formal automation brief template
A structured questionnaire for non-trivial feature work — who uses it, what they do manually, what input they have, what output they should get, what the feature must never do, what proof would make it trustworthy.

### 8.4 Agent router (`AGENTS.md`)
A 15-line file in the repo root that tells any agent what to read first — the four core docs, in order. Adds legibility without changing any existing habit.

### 8.5 Eval harness for Smart Mode quality
The most ambitious idea. Scenario packs (10-20 test dictations per mode), weighted rubrics, strategy comparison, scorecards. Valuable when switching AI providers/models — instead of "seems fine," Mumble would have *data*. Start with Text mode (the default) before expanding.

### 8.6 GitHub Actions CI
Minimal workflow: set up Python → create venv → install dependencies → run offline tests → safety scan. Catches regressions before they reach the owner's machine.

---

## 9. Integration Roadmap

### Phase 1 — This week (zero disruption)
- Add `AGENTS.md` to repo root (15-line router)
- Add lightweight safety scanner (`scripts/safety_check.py`)
- Run safety scanner as part of session-end ritual

### Phase 2 — This sprint (minor additions)
- Adopt proof-ladder language in sign-offs
- Create feature-brief template for non-trivial changes
- Set up GitHub Actions CI for offline test suite

### Phase 3 — When a mode changes (targeted)
- Build eval harness for one Smart Mode
- Create scenario packs (10-20 dictations)
- Define rubric; produce scorecard
- Use scorecard to gate the mode change

### Phase 4 — When Mumble outgrows single-agent (future)
- Split work into orchestrator + worker lanes
- Add Crabbox-style isolated proof for port testing
- Add browser E2E proof for the web UI
- Formalize autoreview pass before merge

---

## 10. Risks and Anti-Patterns to Avoid

### Over-engineering the process
HIE describes a multi-agent system used by someone shipping products professionally. Mumble is a single-agent project. Adopting the full 11-role model would be counterproductive noise. Absorb the *principles* (proof ladder, deterministic baseline, safety scan) without the *machinery* (multi-agent orchestration, Crabbox, browser workers).

### Eval harness scope creep
Building an eval harness for all seven Smart Modes at once would be a multi-week project delivering zero user-facing value. Start with **one mode** (Text) and prove the harness works before expanding.

### Process as substitute for judgement
HIE warns: "never call a workflow ready just because a command exited zero." Adding proof labels must not become a checkbox exercise. Every piece of process must answer: "what decision does this enable that we could not make before?"

### Factory Mission Control vs HIE — complementary, not competing
Mission Control is a **runtime environment** for agentic work (the shell, tools, skills, session management). HIE is a **design pattern** for structuring that work (lanes, proof ladder, eval harness). They solve different problems. Mumble already operates inside Droid (Factory's agent runtime) — the question is how much of HIE's *furniture* to bring into the *room*.

---

## Summary

**How I Engineer** is a pattern to study, not a product to install. Its core contributions: the proof ladder, eval harness, autoreview gate, and worker-lane decomposition.

**Mumble already does much of this implicitly.** Its session ritual, four-doc system, deterministic-first architecture, and privacy boundaries are strongly aligned with HIE's kernel. The gaps are in *explicitness* — named proof levels, automated safety scanning, feature-brief templates, and CI/CD.

**The recommended path** is to adopt the lightest, highest-value pieces first (AGENTS.md, safety scanner, proof labels) and reserve the heavier machinery (eval harness, worker lanes, Crabbox) for when Mumble's complexity genuinely demands them. The goal is not to become HIE — it is to make Mumble's already-strong process *legible* and *verifiable* in the way HIE demonstrates.

---

*Generated 2026-06-28 · Source: [AnasInno/how-i-engineer](https://github.com/AnasInno/how-i-engineer) · MIT License*
