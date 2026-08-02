"""Shared parser for the canonical machine-readable Core present state."""

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any


AUTHORITY_ID = "mumble-current-state"
AUTHORITY_SCHEMA = "mumble.current-state.v1"

_EXPECTED_FIELDS = {
    "schema": AUTHORITY_SCHEMA,
    "main": "66a3564ab2e7354f0b1c5b0649686611c758f8bc",
    "accepted_source": "b6fe674f6332a5cfb20bf35568152fe22798f55e",
    "correction_parent": "2f22153e97388c33f8517f89e2af04095389c4d1",
    "published_pr_head": "b6fe674f6332a5cfb20bf35568152fe22798f55e",
    "ci_subject": "b6fe674f6332a5cfb20bf35568152fe22798f55e",
    "ci_run": "30730116960",
    "ci_status": "passed",
    "review_subject": "b6fe674f6332a5cfb20bf35568152fe22798f55e",
    "review_status": "accepted",
    "review_task": "019fc013-3ab9-7bc0-9c39-f63385bb8359",
    "published_records_head": "ce28abb4c665fc5216aecb083bdc8e5e30e8cc8f",
    "published_records_parent": "66a3564ab2e7354f0b1c5b0649686611c758f8bc",
    "records_ci_run": "30730441394",
    "records_ci_status": "passed",
    "records_review_status": "rejected",
    "record_candidate_status": "awaiting-review",
    "current_record": "Entry 95",
    "pr_number": "49",
    "pr_status": "merged",
    "merge_status": "merged",
    "promotion_status": "source-merged",
    "replacement_ci_status": "passed",
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
    published_records_head: str
    published_records_parent: str
    records_ci_run: str
    records_ci_status: str
    records_review_status: str
    record_candidate_status: str
    current_record: str
    pr_number: str
    pr_status: str
    merge_status: str
    promotion_status: str
    replacement_ci_status: str
    open_issues: str
    acceptance_gates: str


def _plain_text(fragment: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\u2013", "-")
    return " ".join(text.split())


def _present_state_authorities(status_html: str) -> dict[str, str]:
    try:
        opening = status_html.split(
            '<div class="plain"><div class="tag">Current truth</div><div><p>', 1
        )[1].split("</p>", 1)[0]
        verification = status_html.split(
            '<h2><span class="sec">2</span>Verification and release gates</h2>', 1
        )[1].split("</p>", 1)[0]
        current_table = status_html.split(
            '<h2><span class="sec">3</span>Programme evidence ledger</h2>', 1
        )[1].split("</table>", 1)[0]
    except IndexError as exc:
        raise CurrentStatusContractError("a required present-state prose authority is missing") from exc

    rows = [_plain_text(row) for row in re.findall(r"<tr>(.*?)</tr>", current_table, re.DOTALL)]
    integration_rows = [
        row for row in rows if row.startswith("Current published integration state ")
    ]
    issue_30_rows = [row for row in rows if row.startswith("#30 final parity and promotion ")]
    if len(integration_rows) != 1 or len(issue_30_rows) != 1:
        raise CurrentStatusContractError(
            "expected exactly one #30 row and one current published integration row"
        )

    return {
        "opening current truth": _plain_text(opening),
        "verification and release gates": _plain_text(verification),
        "issue #30": issue_30_rows[0],
        "published integration": integration_rows[0],
    }


def _validate_present_state_authorities(
    status_html: str, current: CurrentStatus
) -> None:
    authorities = _present_state_authorities(status_html)
    required_fragments = {
        "opening current truth": (
            current.main,
            current.accepted_source,
            current.ci_run,
            current.published_records_head,
            current.records_ci_run,
        ),
        "verification and release gates": (
            current.main,
            current.accepted_source,
            current.ci_run,
            current.published_records_head[:8],
            current.records_ci_run,
        ),
        "issue #30": (
            current.main[:8],
            current.accepted_source[:8],
            current.ci_run,
            current.published_records_head[:8],
            current.records_ci_run,
        ),
        "published integration": (
            current.main,
            current.accepted_source,
            current.ci_run,
            current.published_records_head,
            current.records_ci_run,
        ),
    }
    for label, fragments in required_fragments.items():
        missing = [fragment for fragment in fragments if fragment not in authorities[label]]
        if missing:
            raise CurrentStatusContractError(
                f"{label} contradicts the current-state authority; missing={missing}"
            )

    all_current_prose = " ".join(authorities.values()).lower()
    stale_claims = (
        "unchanged main 2f000b43",
        "accepted local source c86e48f7",
        "no candidate ci or promotion",
        "fresh independent review and exact-head ci remain open",
        "no push, pr, merge",
    )
    present_stale_claims = [claim for claim in stale_claims if claim in all_current_prose]
    if present_stale_claims:
        raise CurrentStatusContractError(
            f"present-state prose retains stale claims: {present_stale_claims}"
        )


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
        raise CurrentStatusContractError("the CI subject must be the published PR head")
    if fields["review_subject"] != fields["published_pr_head"]:
        raise CurrentStatusContractError("the accepted review subject must be the published PR head")
    if fields["pr_status"] == "open" and fields["merge_status"] != "unmerged":
        raise CurrentStatusContractError("an open PR cannot be recorded as merged")
    if fields["merge_status"] == "unmerged" and fields["promotion_status"] != "not-promoted":
        raise CurrentStatusContractError("an unmerged correction cannot be recorded as promoted")
    if fields["pr_status"] == "merged" and fields["merge_status"] != "merged":
        raise CurrentStatusContractError("a merged PR must be recorded as merged")
    if fields["merge_status"] == "merged" and fields["promotion_status"] != "source-merged":
        raise CurrentStatusContractError("a merged correction must record source merge truth")
    if fields["ci_status"] == "passed" and fields["replacement_ci_status"] != "passed":
        raise CurrentStatusContractError("passed replacement CI must remain current")
    if fields["published_records_parent"] != fields["main"]:
        raise CurrentStatusContractError("the published records parent must be the source merge")
    if fields["records_review_status"] == "rejected" and fields["record_candidate_status"] != "awaiting-review":
        raise CurrentStatusContractError("a rejected records head requires a fresh review candidate")

    current = CurrentStatus(**fields)
    _validate_present_state_authorities(status_html, current)
    return current


def test_current_status_contract_accepts_the_canonical_authority() -> None:
    status_path = (
        Path(__file__).resolve().parents[2]
        / "Development Files"
        / "Core"
        / "STATUS.html"
    )

    current = parse_current_status(status_path.read_text(encoding="utf-8"))

    assert current.current_record == "Entry 95"


def test_every_present_state_row_agrees_with_the_canonical_authority() -> None:
    status_path = (
        Path(__file__).resolve().parents[2]
        / "Development Files"
        / "Core"
        / "STATUS.html"
    )
    status = status_path.read_text(encoding="utf-8")
    current = parse_current_status(status)
    authorities = _present_state_authorities(status)

    assert set(authorities) == {
        "opening current truth",
        "verification and release gates",
        "issue #30",
        "published integration",
    }
    assert current.published_records_head in authorities["published integration"]
    assert "PR #49 merged" in authorities["published integration"]
    assert "No install, runtime restart, physical check" in authorities["published integration"]
    assert "manual artifact promotion was not run" in authorities["issue #30"]
