"""Shared parser for the canonical machine-readable Core present state."""

from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
from typing import Any


AUTHORITY_ID = "mumble-current-state"
AUTHORITY_SCHEMA = "mumble.current-state.v1"

_EXPECTED_FIELDS = {
    "schema": AUTHORITY_SCHEMA,
    "main": "2f000b43daced675fcbfa53c8e7c75862124906b",
    "accepted_source": "c86e48f770083490e4621ef9770e654ab0d38b1e",
    "correction_parent": "2f22153e97388c33f8517f89e2af04095389c4d1",
    "published_pr_head": "936659747c351d7a6980a5ce8b377638d07f7a13",
    "ci_subject": "936659747c351d7a6980a5ce8b377638d07f7a13",
    "ci_run": "30728545428",
    "ci_status": "failed",
    "review_subject": "2f22153e97388c33f8517f89e2af04095389c4d1",
    "review_status": "rejected",
    "review_task": "019fc013-3ab9-7bc0-9c39-f63385bb8359",
    "current_record": "Entry 93",
    "pr_number": "49",
    "pr_status": "open",
    "merge_status": "unmerged",
    "promotion_status": "not-promoted",
    "replacement_ci_status": "not-run",
    "open_issues": "12,25,28,29,30",
    "acceptance_gates": "open",
}


class CurrentStatusContractError(AssertionError):
    """Raised when STATUS.html has ambiguous or invalid present-state authority."""


@dataclass(frozen=True)
class CurrentStatus:
    schema: str
    main: str
    accepted_source: str
    correction_parent: str
    published_pr_head: str
    ci_subject: str
    ci_run: str
    ci_status: str
    review_subject: str
    review_status: str
    review_task: str
    current_record: str
    pr_number: str
    pr_status: str
    merge_status: str
    promotion_status: str
    replacement_ci_status: str
    open_issues: str
    acceptance_gates: str


class _AuthorityHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.authorities: list[str] = []
        self._capturing = False
        self._content: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        is_authority = any(
            (name == "id" and value == AUTHORITY_ID)
            or name == "data-current-state-authority"
            for name, value in attrs
        )
        if not is_authority:
            return

        attribute_names = [name for name, _value in attrs]
        if len(attribute_names) != len(set(attribute_names)):
            raise CurrentStatusContractError("current-state authority has duplicate HTML attributes")

        attributes = dict(attrs)
        if self._capturing:
            raise CurrentStatusContractError("current-state authorities may not be nested")
        if tag != "script":
            raise CurrentStatusContractError("current-state authority must be a script element")
        if attributes.get("id") != AUTHORITY_ID:
            raise CurrentStatusContractError("current-state authority has the wrong id")
        if attributes.get("type") != "application/json":
            raise CurrentStatusContractError("current-state authority must contain JSON")
        if attributes.get("data-current-state-authority") != AUTHORITY_SCHEMA:
            raise CurrentStatusContractError("current-state authority has the wrong schema marker")
        self._capturing = True
        self._content = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._content.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capturing and tag == "script":
            self.authorities.append("".join(self._content))
            self._capturing = False
            self._content = []


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CurrentStatusContractError(f"current-state field {key!r} appears more than once")
        result[key] = value
    return result


def parse_current_status(status_html: str) -> CurrentStatus:
    """Parse and strictly validate the sole authoritative current-state record."""

    parser = _AuthorityHTMLParser()
    parser.feed(status_html)
    parser.close()
    if parser._capturing:
        raise CurrentStatusContractError("current-state authority is not closed")
    if len(parser.authorities) != 1:
        raise CurrentStatusContractError(
            f"expected exactly one current-state authority, found {len(parser.authorities)}"
        )

    try:
        fields = json.loads(parser.authorities[0], object_pairs_hook=_unique_object)
    except CurrentStatusContractError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise CurrentStatusContractError("current-state authority is not valid JSON") from exc

    if not isinstance(fields, dict):
        raise CurrentStatusContractError("current-state authority must be one JSON object")

    missing = sorted(set(_EXPECTED_FIELDS) - set(fields))
    extra = sorted(set(fields) - set(_EXPECTED_FIELDS))
    if missing or extra:
        raise CurrentStatusContractError(
            f"current-state fields differ from the contract; missing={missing}, extra={extra}"
        )

    for field, expected in _EXPECTED_FIELDS.items():
        actual = fields[field]
        if not isinstance(actual, str):
            raise CurrentStatusContractError(f"current-state field {field!r} must be one string")
        if actual != expected:
            raise CurrentStatusContractError(
                f"current-state field {field!r} must be {expected!r}, got {actual!r}"
            )

    if fields["ci_subject"] != fields["published_pr_head"]:
        raise CurrentStatusContractError("the failed CI subject must be the published PR head")
    if fields["review_subject"] != fields["correction_parent"]:
        raise CurrentStatusContractError("the rejected review subject must be the correction parent")
    if fields["pr_status"] == "open" and fields["merge_status"] != "unmerged":
        raise CurrentStatusContractError("an open PR cannot be recorded as merged")
    if fields["merge_status"] == "unmerged" and fields["promotion_status"] != "not-promoted":
        raise CurrentStatusContractError("an unmerged correction cannot be recorded as promoted")
    if fields["ci_status"] == "failed" and fields["replacement_ci_status"] != "not-run":
        raise CurrentStatusContractError("failed CI remains current until replacement CI runs")

    return CurrentStatus(**fields)


def test_current_status_contract_accepts_the_canonical_authority() -> None:
    status_path = (
        Path(__file__).resolve().parents[2]
        / "Development Files"
        / "Core"
        / "STATUS.html"
    )

    current = parse_current_status(status_path.read_text(encoding="utf-8"))

    assert current.current_record == "Entry 93"
