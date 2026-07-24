# Issue tracker: GitHub

Mumble's work discussion and tickets live in GitHub Issues. Use the `gh` command-line tool for issue operations. The repository is inferred from the current Git connection.

## Conventions

- Create an issue with `gh issue create` and a clear title, evidence, completion checks, and explicit blockers.
- Read an issue with `gh issue view <number> --comments` before acting on it.
- Use GitHub Issues for planning maps, engineering decisions, and bounded implementation work.
- Keep `Development Files/Core/STATUS.html` as the concise present-state record, `HANDBOOK.html` as procedures and context, and `LOGS.html` as completed history. Issues complement these records; they do not replace them.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## When a skill says “publish to the issue tracker”

Create a GitHub issue. When it says “fetch the relevant ticket”, use `gh issue view <number> --comments`.
