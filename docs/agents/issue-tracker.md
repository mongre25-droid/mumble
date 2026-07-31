# Issue tracker: GitHub

Mumble's substantial work, specifications, planning maps, and decisions live in the `mongre25-droid/mumble` GitHub repository. Use the `gh` command-line tool from this checkout so it infers the repository from the configured remote.

## Conventions

- Create an issue with `gh issue create` and include the intended outcome, evidence, scope, verification, durable-record expectations, and explicit blockers.
- Read an issue and its comments with `gh issue view <number> --comments` before acting on it.
- List issues with `gh issue list --state open --json number,title,body,labels,comments` and narrow the result by label when appropriate.
- Comment with `gh issue comment <number> --body "..."`.
- Apply or remove labels with `gh issue edit <number> --add-label "..."` or `--remove-label "..."`.
- Close an issue with `gh issue close <number> --comment "..."` only after its completion evidence exists.

GitHub Issues complement the concise project truth; they do not replace `Development Files/Core/STATUS.html`, `HANDBOOK.html`, or `LOGS.html`.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub shares one number space across issues and pull requests. If a bare number is ambiguous, try `gh pr view <number>` and then `gh issue view <number>`.

## Skill operations

- When a skill says "publish to the issue tracker", create a GitHub issue.
- When a skill says "fetch the relevant ticket", use `gh issue view <number> --comments`.

## Wayfinding operations

- The programme map is one issue labelled `wayfinder:map` or, when that label is unavailable, `kind: planning`.
- Child tickets are linked as GitHub sub-issues where supported. Otherwise, place them in the map's task list and include `Part of #<map>` in each child.
- Record every blocking edge with GitHub's native issue dependency relationship where supported and also include a plain `Blocked by: #<number>` line so the dependency remains readable and portable.
- A frontier ticket is an open, unassigned child whose recorded blockers are all closed.
- Claim a frontier ticket before work, resolve it with evidence, then close it and update the map.

Never treat an issue being closed, a local test passing, or a source branch changing as proof of installation, physical-device behaviour, signing, deployment, or public release.
