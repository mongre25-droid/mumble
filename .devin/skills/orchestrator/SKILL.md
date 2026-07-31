---
name: orchestrator
description: Turn a spoken or messy request into an actually Ask Matt-routed workflow, polished copy-ready prompts, dependency-aware execution order, and optional task supervision. Use when the user wants an orchestration panel, asks to split work across skills or tasks, wants prompts improved, wants existing work supervised, or explicitly invokes /orchestrator.
---

# Orchestrator

Turn the chat into a clear control panel. Ask Matt selects the smallest suitable workflow from the full installed Matt Pocock skill set; Orchestrator makes that workflow understandable, creates the best possible prompts, and supervises authorised work.

## Operating contract

- First understand what the user is asking Orchestrator to do: route only, create prompts only, execute approved prompts, supervise existing work, or resume an existing programme. Do not assume that every invocation means execution.
- Preserve the user's actual goal, wording, constraints, existing evidence, and stated authority. Separate independent workstreams, dependencies, and work that needs supervision.
- Ask Matt is the normal router. Actually invoke `/ask-matt` for every new programme and every materially changed or resumed programme unless the user explicitly says that a still-current Ask Matt Route Receipt must be reused. Launch every invocation as its own separate visible task titled **Ask Matt**.
- Do not replace, approximate, or second-guess Ask Matt. If `/ask-matt` is unavailable, repair its availability or state that routing cannot start; do not silently invent a route.
- Ask Matt may choose any suitable installed Matt Pocock skill or flow. Do not force `/to-spec`, `/to-tickets`, GitHub issues, Wayfinder, grilling, research, prototypes, or implementation unless Ask Matt's actual route calls for them.
- Always preserve the user's raw request verbatim in the Ask Matt invocation. Show the user the complete exact prompt before launching the visible **Ask Matt** task. After it returns, reproduce its entire raw answer verbatim as the Route Receipt before creating prompts or launching work; never hide it, shorten it, or replace it with a summary.
- Always show the complete refined prompt for every task. Never hide a launched prompt. Prompts are Orchestrator's improved execution instructions; Ask Matt's route remains the authority for which skills and stages belong in the flow.
- Treat the Prompt Packet as the current canonical instructions for the programme. As verified findings, accepted decisions, dependency outcomes, or integration results emerge, revise every unlaunched affected prompt so it contains that information before it is sent to its destination session. Never silently mutate an already launched prompt: send that session an explicit follow-up only when the new information materially affects its authorised work.
- Keep four things distinct: an Ask Matt Route Receipt, a Prompt Packet, any tracker records, and the separate execution sessions that do the work. A plan, research report, prototype, specification, ticket, branch, or workspace is never described as a completed product change.
- Use the issue tracker only when the configured project workflow and the selected skill call for it. Follow `/setup-matt-pocock-skills` and the repository tracker guidance; do not invent a new tracker process or force GitHub into work that does not need it.
- For complex implementation and other substantive named deliverable sessions, prefer `gpt-5.6-sol` with high reasoning and the standard available service pace when that model is available. An explicit owner speed request overrides the standard-pace default for the authorised run. Use a smaller model only when the expected quality difference is genuinely negligible. If the preferred model is unavailable, choose the strongest suitable available model and report the fallback rather than blocking or silently lowering quality.
- Make no public, destructive, billable, credential-dependent, permission-dependent, or owner-only decision without the user's authority. In Undisturbed mode, continue every safe independent task before requesting the smallest needed owner action.

## Stage 0: Read the request and choose the operating mode

Read the user's words before routing. Record one of these modes in the Route Receipt:

- **Route only:** explain Ask Matt's route and stop.
- **Prompt Packet:** create polished prompts for the selected route and stop.
- **Supervised execution:** create prompts, launch authorised work, and supervise it.
- **Resume:** inspect the existing programme, invoke Ask Matt with that state, then follow its revised route.

Do not launch work in Route-only or Prompt-Packet mode. Do not treat Undisturbed mode as permission to ignore the mode the user selected; it only removes routine checkpoints from authorised execution.

## Stage 1: Actually route with Ask Matt

For a new, materially changed, or resumed programme, first confirm that `/ask-matt` is callable. Build the routing brief below by substituting the raw request without rewriting it, show that complete exact prompt to the user, then launch it in a separate visible task titled **Ask Matt**. Showing the prompt alone is not a routing result.

```text
/ask-matt

You are the workflow-routing core inside an Orchestrator session. Determine the smallest, strongest Matt Pocock workflow for the raw request below. Choose the route only: do not implement work or write final task prompts.

Raw user request:
<verbatim user request>

1. Extract the intended outcome, requested operating mode, separate workstreams, constraints, uncertainties, and existing work that needs supervision.
2. Choose the smallest suitable route from the full available Matt Pocock skills and flows. Do not assume a fixed chain or restrict the answer to examples in this prompt.
3. Give the exact skill order, genuine parallel branches, blocking edges, evidence or decisions required first, and owner-only blockers.
4. Recommend a Mini Grill only when up to three questions would materially improve an otherwise direct task. Do not duplicate a full Grill Me or Wayfinder interview.
5. If this is existing work to supervise or resume, state its current position, next safe action, and real blocker.

Return: understood outcome; requested operating mode; route and rationale; ordered workstreams; parallel and blocked work; Mini Grill questions if needed; tracker use only where the selected skills require it; and unresolved risk.
```

When the visible **Ask Matt** task finishes, reproduce its entire raw answer verbatim as the **Ask Matt Route Receipt**. Do not summarise, excerpt, reformat, or hide any part of it. If Ask Matt is unavailable, stop before prompt creation or execution unless the user explicitly authorises a labelled fallback route.

## Stage 2: Explain the route

Present a compact, beginner-friendly route board before task prompts:

- **Goal:** one plain-English sentence.
- **Operating mode:** whether this invocation stops after the route, stops after prompts, or proceeds into supervised execution.
- **Route:** numbered stages with the chosen skill and a one-line reason.
- **Parallel work:** only independent tasks that can safely proceed together.
- **Blocked work:** each dependency and why it blocks.
- **Tracker use:** whether the selected skills require GitHub, another configured tracker, or no tracker record.
- **Owner action:** only real user decisions, approvals, credentials, or access needs.

For a multi-branch effort, add a small Mermaid flowchart. For a small task, keep the route board as a short numbered list.

## Stage 3: Forge and quality-check prompts

Produce a visible **Prompt Packet**. Each task's refined prompt is one complete, living launch packet rather than a short task description plus separate hidden handoff material. Every task must have a literal `/skill-name` as its first line and include only useful detail:

- objective and user outcome;
- relevant context and evidence to inspect;
- precise scope and explicit exclusions;
- dependencies and safe parallel boundaries;
- expected artefacts or behaviour;
- verification and durable-record expectations when relevant;
- clear completion and handoff conditions.

For a launched execution session, also embed the current programme baseline, its exact dependency state, its session role, verified relevant findings, and the final completion receipt it must return. A tracker link may support the prompt but never replaces this complete context. When the programme learns something material, update the canonical refined prompt before every later launch, preserving original intent, acceptance criteria, exclusions, prior rejection findings, and handoff requirements.

Before publishing the packet, check each prompt for missing goal, context, evidence, scope, dependencies, success criteria, verification, documentation, and stopping conditions. Repair omissions instead of merely reporting them.

For each prompt, show its selected skill, its evidence state (`planned`, `running`, `candidate committed`, `independently accepted`, `integrated`, `merged`, `exact-head CI verified`, `physically verified`, `deployed`, `released`, or `owner blocked`), and a tracker link only when the selected workflow created one. Use only the states that genuinely apply; do not call a product change complete merely because code was written or a local test passed. Keep `integrated`, default-branch `merged`, `deployed`, and `released` distinct even when a project's order or release model does not use every state.

## Stage 4: Execute and supervise when authorised

Run this stage only in Supervised-execution mode.

### Execution-session boundary

- Launch every safe, unblocked named deliverable selected by Ask Matt in its own separate visible destination task or session. Before launch, show its complete exact refined prompt. Use the exact skill or flow the route selected; do not replace it with a generic implementation task or a hidden supervisor sub-agent.
- Every implementation issue must be launched in its own fresh execution session with cleared context, the complete visible Prompt Packet, its tracker issue, and its required branch or isolated workspace.
- The fresh execution session owns the actual issue work: it reads the issue and specification, implements the change, runs the focused checks, performs its self-review, and reports the evidence back to Orchestrator. A separate read-only session owns the independent acceptance verdict.
- The Orchestrator supervisor may use internal sub-agents only for bounded non-deliverable support, such as locating relevant files, gathering evidence, or preparing test output. It must never use hidden internal sub-agents instead of the separately launched named routed research, diagnosis, implementation, independent review, integration, or final-acceptance tasks.
- Each visible destination task may use its own internal sub-agents at its discretion. The destination task still owns the deliverable and must inspect, integrate, verify, and truthfully report all helper work in its completion receipt.
- State each named session and its truthful evidence state. Do not describe internal support work or tracker records as completed deliverable tasks.
- For every finished worker, inspect its actual output, verification, review, durable records, and commit where relevant before updating its state.
- If the selected skill created tracker records, update them with verified evidence and use their blocking edges to find newly ready work. Do not create, close, or rely on GitHub issues merely because this is an orchestrated effort.
- Continue the supervisor loop: finish a task, verify it, update the truthful record, identify newly unblocked work, and launch the next authorised tasks.
- A Wayfinder map, research report, prototype, specification, and ticket set are planning artefacts. They hand off to whatever next stage Ask Matt selected; they do not end an execution programme by themselves.
- In Undisturbed mode, keep this loop running until the selected route is complete or a genuine owner-only blocker remains. In other modes, stop where the user requested.

### Current verified baseline and convergence

- Establish one **current verified integration baseline** for a supervised execution programme: an exact commit or ref that contains the newest accepted, integrated, and appropriately checked project work. It is not automatically the repository's default branch, and it is never an unreviewed worker's draft.
- Give every session a separate safe workspace based on that exact baseline. Sessions work on the same current product snapshot, not on one shared writable checkout.
- Run in parallel only tasks whose declared dependencies are already present in the baseline. Do not launch a task that depends on another task's result from an older baseline.
- Treat the programme as dependency waves. At the end of a meaningful wave, launch a dedicated integration session. It starts from the most up-to-date reconciled baseline, combines only the exact independently accepted commits, resolves any conflict deliberately, runs the required checks on the exact combined head, and records the new verified integration baseline.
- Before an integration wave, compare the current integration baseline with the latest verified remote default branch. If that branch contains newer accepted work, reconcile it in the fresh integration workspace before treating the combined head as current. Do not claim that a local integration, the default branch, deployment, or a release is the same thing.
- Launch every newly unblocked dependent task from the new verified integration baseline and update its complete refined prompt with that baseline, accepted dependencies, new findings, and remaining gates. Do not force already-running independent tasks to absorb changes mid-task; integrate them deliberately when they return.
- If integration changes behaviour through a conflict resolution or other material adjustment, treat the combined head as a new candidate for the relevant checks and review.

### Immutable completion receipts and independent acceptance

- Every implementation session returns an immutable completion receipt: exact candidate commit hash, branch or ref, parent baseline hash, clean-workspace proof, evidence level, host or environment, checks run and their results, retained first failures, durable-record changes, and remaining gates.
- A separate read-only independent-review session receives the full current refined prompt and prior relevant findings. It verifies the exact candidate hash before reading code, then returns an acceptance or rejection verdict with evidence. An implementation session's self-review is useful but never supplies the independent acceptance verdict.
- The integration session verifies the accepted hashes before combining work. If the candidate changes, its receipt changes and the relevant independent review must assess the new exact candidate.

### Risk-based verification

- Use the project's established evidence policy when one exists. Otherwise use three compact levels: **Level A** for one isolated task or correction, **Level B** for the converged candidate, and **Level C** for final physical, platform, deployment, and release acceptance.
- Level A is the default task gate: reproduce the defect when feasible, test the changed production boundary, run one adjacent caller or integration smoke, and perform the narrow compile, syntax, static, and diff checks relevant to changed paths. Add one deterministic browser, native, accessibility, or package check only when that boundary is directly involved.
- Never use the 80/20 rule to omit a critical targeted privacy, security, credential, authorization, clipboard, data-loss, migration, installer, or destructive-path check.
- Move broad suites, repeated platform matrices, combined integration checks, package extraction, and exact-head CI to Level B after independently accepted candidates converge. Run no more than one full application runner per host at a time, retain the first red result, and explain it before any rerun.
- Reserve Level C for real applications, hardware, accessibility, performance, installers, signing, deployment, and release evidence. Keep unavailable physical or owner-controlled gates explicitly open.
- Store the reusable policy in the project's authoritative procedure record, a compact current ledger in its status record, and detailed receipts in the configured tracker only when the selected workflow uses one. Do not create duplicate evidence files.

### Correction-session reset

- After an independently rejected candidate, launch the correction in a fresh named execution session from the exact rejected commit with the complete updated Prompt Packet. Do not keep extending a session whose context has already compacted or whose candidate work is complete.
- Reuse an active session only for a small clarification before its candidate is complete and while its context remains intact. A correction that changes the candidate receives a new receipt and a fresh independent review.

### Launch preflight

- Before launching a wave or a newly dependent task, silently verify the exact starting ref, its clean status, the task's dependency state, safe-workspace creation, and enough available capacity to complete or preserve the work. Do not launch a task on a stale base merely to fill capacity.
- Verify that the exact selected skill is available to the destination session before it acts. If the session catalogue is stale but a trusted local skill file exists, include its exact path and require the session to read it completely. Do not silently downgrade to a generic task; repair availability or report the labelled blocker unless the user explicitly authorises a fallback.
- Before any full runner, check that the same host has no competing full application runner. Serialize scarce browser, native-window, clipboard, focus, microphone, package, and installer resources when concurrent use could invalidate evidence.
- If capacity is becoming insufficient, finish or preserve the active checkpoints first. Report only a real blocker or decision to the user; otherwise continue supervision.

## Supervision and recovery

Infer the requested behaviour rather than forcing a mode menu:

- **Route only / Prompt Packet:** stop exactly at the selected operating-mode boundary.
- **Normal orchestration:** launch authorised safe tasks, report meaningful changes, and ask only when an important decision or genuine blocker remains.
- **Undisturbed mode:** continue authorised safe work without routine questions; retry ordinary failures, inspect evidence, improve prompts or routes, and exhaust safe alternatives. Stop only after a real owner-only blocker remains.

On failure or weak output: inspect the evidence, diagnose the smallest cause, retry or route around it when safe, preserve useful work, and update the Route Receipt, Prompt Packet, and task states. Do not silently abandon a task or silently downgrade a selected skill or flow. State the exact user action needed only when no safe independent path remains.

## Authority boundary

- Keep the universal 80/20 routing, prompting, baseline, evidence, receipt, reset, and resource-scheduling rules in this skill.
- Keep project-specific testing, packaging, platform, deployment, and release rules in that project's authoritative records. In Mumble, those rules remain in the live Core records; do not copy Mumble-only commands, issue state, or release facts into this universal skill.
- Treat `Orchestrator-80-20-Workflow-Handoff.md` and transfer archives as historical transfer and recovery evidence only. They do not override this live runtime skill or a project's current authoritative records.

## Dashboard updates

Use a polished compact dashboard containing **Finished**, **Working**, **Blocked**, and **Next**. Preserve task titles and status truth. Use `ongoing` for active or uncertain work, `blocked` only for a genuine owner-only need, and `complete` only for clearly completed work.
