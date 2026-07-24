# Mumble development instructions

Read `Core/README.html` before substantive work. It defines the current product, the working rules, the verification expectations, and the four durable records.

## Durable records

Keep only the relevant Core record current as part of the same task:

- `Core/README.html` — stable project philosophy, current layout, standards, and documentation rules.
- `Core/STATUS.html` — present state, active priorities, known bugs, release gates, and unfinished work.
- `Core/HANDBOOK.html` — current architecture, testing, packaging, porting, and deployment procedures.
- `Core/LOGS.html` — append-only history of meaningful completed work and milestones.

Do not create duplicate context, audit, TODO, bug, status, or handoff files when the information belongs in one of these records. If an unresolved issue is discovered during authorized work, record it in `Core/STATUS.html` before sign-off.

## Current layout

- `Core/` contains exactly the four current HTML records above.
- `Docs/` contains development guidance and project documentation.
- `GitHub/` contains issue forms, labels, and helper scripts; the GitHub Actions workflow remains at the repository root because GitHub requires that location.
- `Marketing/` contains the product website and campaign material.
- `Research/` contains evidence and design research that must not override the code or Core records.
- `Tooling/` contains build, packaging, verification, and synchronization helpers.
- `Legal/` contains development-facing legal material.

The application that ships lives in `../Internal/`. `Internal/Releases/` contains packaged distributables. The root `Mumble.exe`, `.gitignore`, `LICENSE`, and this root instruction loader are platform or repository convention files; maintain their authoritative development content here when it is useful, but do not move them in a way that breaks their required discovery behavior.

## Working rules

1. Read the four Core records relevant to the task before editing code or documentation.
2. Treat `Internal/app/` as the implementation source of truth. Describe only behaviour that exists in the current code.
3. Windows is the product-contract source. Keep macOS and Linux changes in their platform seams and verify parity deliberately.
4. Local transcription is the default. Cloud transcription and cloud text processing are explicit user choices and must remain visible.
5. Do not add secrets, private settings, generated environments, caches, or release-only binaries to the repository.
6. Keep the zero-CDN Web UI self-contained in `Internal/app/webui/`.
7. Update the relevant Core record when behaviour, architecture, layout, procedures, status, or a meaningful milestone changes.
8. Verify changes with the narrowest relevant checks, then the isolated application test runner when runtime code is touched.

## Operating mode

The only special owner-selected mode is `Undisturbed mode`. Otherwise use normal Codex behaviour. That mode permits continuous safe work and verification, but never overrides permissions, scope, required approvals, destructive or irreversible decisions, secrets, spending, or public/account actions.

## GitHub workflow guidance

Label sources and synchronization tooling are in `GitHub/`; issue forms are kept at the repository-required `.github/ISSUE_TEMPLATE/` path. The Matt Pocock skill configuration is at the repository-required `docs/agents/` path. GitHub Issues track substantial work; the Core records remain the concise project truth and are not replaced by issue discussions.
