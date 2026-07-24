"""Pure planning core for the 5.6 sol xtra high desktop-assistant experiment.

This module deliberately has no dependency on Mumble's runtime.  It converts a
small, honest set of natural-language commands into inspectable action plans.
The optional desktop host decides which actions it can safely execute.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Iterable
from urllib.parse import quote_plus, urlparse


SUPPORTED_SITES = {
    "brave": "https://search.brave.com/search?q={query}",
    "google": "https://www.google.com/search?q={query}",
    "github": "https://github.com/search?q={query}",
    "linkedin": (
        "https://www.linkedin.com/search/results/all/?keywords={query}"
    ),
    "reddit": "https://www.reddit.com/search/?q={query}",
    "x": "https://x.com/search?q={query}",
    "youtube": "https://www.youtube.com/results?search_query={query}",
}

KNOWN_LOCATIONS = {
    "desktop": "~/Desktop",
    "documents": "~/Documents",
    "downloads": "~/Downloads",
    "home": "~",
    "pictures": "~/Pictures",
}

APP_ALIASES = {
    "calculator": "calculator",
    "calc": "calculator",
    "file explorer": "file-explorer",
    "explorer": "file-explorer",
    "notepad": "notepad",
    "paint": "paint",
    "settings": "settings",
}


@dataclass(frozen=True)
class Action:
    """One user-visible unit in a proposed automation plan."""

    kind: str
    label: str
    target: str = ""
    value: str = ""
    risk: str = "low"
    executable: bool = True
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Plan:
    """A deterministic plan that can be rendered before execution."""

    command: str
    summary: str
    actions: tuple[Action, ...]
    confirmation_required: bool
    executable: bool

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "summary": self.summary,
            "actions": [action.to_dict() for action in self.actions],
            "confirmation_required": self.confirmation_required,
            "executable": self.executable,
        }


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().strip(".?!"))


def _split_workflow(command: str) -> list[str]:
    parts = re.split(
        r"\s*(?:,?\s+and\s+then\s+|,?\s+then\s+)\s*",
        command,
        flags=re.I,
    )
    return [part for part in (_clean(part) for part in parts) if part]


def _normalise_site(site: str) -> str:
    site = _clean(site).lower()
    return {"twitter": "x", "brave search": "brave"}.get(site, site)


def _search_action(
    query: str,
    site: str,
    existing_tab: bool = False,
) -> Action:
    site = _normalise_site(site)
    template = SUPPORTED_SITES.get(site)
    if not template:
        return Action(
            "search_web",
            f"Search {site.title()}",
            target=site,
            value=query,
            risk="confirm",
            executable=False,
            note="This site has no reviewed search adapter in the prototype.",
        )
    url = template.format(query=quote_plus(query))
    if existing_tab:
        return Action(
            "search_existing_tab",
            f"Search in the current {site.title()} tab",
            target=site,
            value=url,
            risk="confirm",
            executable=False,
            note=(
                "The current-tab browser adapter is designed but not "
                "connected in this isolated build."
            ),
        )
    return Action(
        "open_url",
        f"Search {site.title()} for “{query}”",
        target=site,
        value=url,
        note="Opens a new browser tab.",
    )


def parse_step(step: str) -> list[Action]:
    """Parse one workflow clause into one or more concrete actions."""

    original = _clean(step)
    lower = original.lower()

    # “Search for X in the existing Brave tab” is intentionally detected
    # before the more general website-search form.
    match = re.match(
        r"(?:search(?:\s+for)?|find)\s+(.+?)\s+in\s+(?:the\s+)?"
        r"(?:existing|current)\s+([\w -]+?)\s+tab$",
        original,
        re.I,
    )
    if match:
        return [
            _search_action(
                _clean(match.group(1)),
                _clean(match.group(2)),
                existing_tab=True,
            )
        ]

    match = re.match(
        r"(?:search(?:\s+for)?|find)\s+(.+?)\s+(?:on|in)\s+([\w -]+)$",
        original,
        re.I,
    )
    if match:
        return [_search_action(_clean(match.group(1)), _clean(match.group(2)))]

    match = re.match(
        r"(?:search(?:\s+for)?|look up|find)\s+(.+)$",
        original,
        re.I,
    )
    if match:
        return [_search_action(_clean(match.group(1)), "brave")]

    match = re.match(
        r"(?:go|navigate|open website)\s+(?:to\s+)?(.+)$",
        original,
        re.I,
    )
    if match:
        target = _clean(match.group(1))
        candidate = target if "://" in target else f"https://{target}"
        parsed = urlparse(candidate)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        return [
            Action(
                "open_url",
                f"Navigate to {target}",
                target=target,
                value=candidate,
                executable=valid,
                risk="low" if valid else "confirm",
                note=(
                    "Opens a new browser tab."
                    if valid
                    else "That does not look like a valid web address."
                ),
            )
        ]

    match = re.match(
        r"(?:switch to|focus|bring (?:up|forward))\s+(.+)$",
        original,
        re.I,
    )
    if match:
        target = _clean(match.group(1))
        return [
            Action(
                "focus_window",
                f"Focus the existing {target} window",
                target=target,
                risk="confirm",
                note=(
                    "Matches a visible window title; it never launches a "
                    "replacement."
                ),
            )
        ]

    match = re.match(
        r"(?:open|show)\s+(?:my\s+)?"
        r"(desktop|documents|downloads|home|pictures)(?:\s+folder)?$",
        lower,
        re.I,
    )
    if match:
        name = match.group(1).lower()
        return [
            Action(
                "open_path",
                f"Open {name.title()}",
                target=KNOWN_LOCATIONS[name],
            )
        ]

    match = re.match(
        r"open\s+(?:the\s+)?(?:folder|directory)\s+(.+)$",
        original,
        re.I,
    )
    if match:
        target = _clean(match.group(1)).strip('"')
        return [
            Action(
                "open_path",
                f"Open folder “{target}”",
                target=target,
                risk="confirm",
                note="The host verifies the path exists before opening it.",
            )
        ]

    match = re.match(
        r"open\s+(?:the\s+)?(?:file|document)\s+(.+)$",
        original,
        re.I,
    )
    if match:
        target = _clean(match.group(1)).strip('"')
        return [
            Action(
                "open_path",
                f"Open file “{target}”",
                target=target,
                risk="confirm",
                note=(
                    "Uses the file's normal Windows application after "
                    "confirmation."
                ),
            )
        ]

    match = re.match(r"(?:open|launch|start)\s+(.+)$", original, re.I)
    if match:
        spoken = _clean(match.group(1))
        alias = APP_ALIASES.get(spoken.lower())
        return [
            Action(
                "open_app",
                f"Open {spoken.title()}",
                target=alias or spoken,
                executable=alias is not None,
                risk="low" if alias else "confirm",
                note=(
                    "Uses the experiment's reviewed application allow-list."
                    if alias
                    else (
                        "This app is not in the prototype's reviewed "
                        "allow-list."
                    )
                ),
            )
        ]

    match = re.match(r"paste\s+(.+?)\s+(?:into|in)\s+(.+)$", original, re.I)
    if match:
        value, target = _clean(match.group(1)), _clean(match.group(2))
        return [
            Action(
                "paste_text",
                f"Paste into {target}",
                target=target,
                value=value,
                risk="confirm",
                executable=False,
                note=(
                    "Clipboard-safe text injection is specified but "
                    "intentionally not connected in this sandbox."
                ),
            )
        ]

    match = re.match(
        r"(?:edit|change|replace|append to)\s+(.+)$",
        original,
        re.I,
    )
    if match:
        target = _clean(match.group(1))
        return [
            Action(
                "edit_file",
                f"Prepare an edit for {target}",
                target=target,
                risk="blocked",
                executable=False,
                note=(
                    "File mutation requires a diff preview and a "
                    "production-grade approval adapter."
                ),
            )
        ]

    return [
        Action(
            "unsupported",
            f"Clarify “{original}”",
            value=original,
            risk="blocked",
            executable=False,
            note=(
                "The local prototype could not map this safely to a "
                "reviewed action."
            ),
        )
    ]


def build_plan(command: str) -> Plan:
    """Build a complete plan from a spoken or typed command."""

    command = _clean(command)
    if not command:
        raise ValueError("A command is required.")

    actions: list[Action] = []
    for step in _split_workflow(command):
        actions.extend(parse_step(step))

    executable = all(action.executable for action in actions)
    confirmation = len(actions) > 1 or any(
        action.risk != "low" for action in actions
    )
    summary = (
        actions[0].label
        if len(actions) == 1
        else f"{len(actions)}-step workflow · "
        + " → ".join(action.label for action in actions)
    )
    return Plan(command, summary, tuple(actions), confirmation, executable)


def validate_plan(payload: dict) -> Plan:
    """Rebuild a client-submitted plan so execution never trusts its fields."""

    command = (
        str(payload.get("command", ""))
        if isinstance(payload, dict)
        else ""
    )
    return build_plan(command)


def action_kinds(plan: Plan) -> Iterable[str]:
    return (action.kind for action in plan.actions)
