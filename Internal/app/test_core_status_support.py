"""Shared parser for the canonical machine-readable Core present state."""

from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

import pytest


AUTHORITY_ID = "mumble-current-state"
AUTHORITY_SCHEMA = "mumble.current-state.v1"
PROJECTION_SCHEMA = "mumble.current-state-projection.v1"
_FORBIDDEN_MARKUP_ERROR = (
    "STATUS contains forbidden non-canonical markup: comments, processing "
    "instructions, invalid doctypes, marked or unknown declarations, and "
    "malformed HTML declarations are not allowed"
)
_HTML_WHITESPACE = " \t\n\f\r"

_EXPECTED_FIELDS = {
    "schema": AUTHORITY_SCHEMA,
    "product_version": "0.95",
    "authority_scope": "valid-when-read-from-refs/heads/main",
    "target_ref": "refs/heads/main",
    "containing_commit": "@self",
    "source_merge": "66a3564ab2e7354f0b1c5b0649686611c758f8bc",
    "accepted_source": "b6fe674f6332a5cfb20bf35568152fe22798f55e",
    "correction_parent": "2f22153e97388c33f8517f89e2af04095389c4d1",
    "published_pr_head": "b6fe674f6332a5cfb20bf35568152fe22798f55e",
    "ci_subject": "b6fe674f6332a5cfb20bf35568152fe22798f55e",
    "ci_run": "30730116960",
    "ci_status": "passed",
    "accepted_source_review_subject": "b6fe674f6332a5cfb20bf35568152fe22798f55e",
    "accepted_source_review_status": "accepted",
    "accepted_source_review_task": "019fc013-3ab9-7bc0-9c39-f63385bb8359",
    "review_subject": "704921eb61014ac4d5b0f01a0399defab3028882",
    "review_status": "accepted",
    "review_task": "019fc013-3ab9-7bc0-9c39-f63385bb8359",
    "predecessor_published_records_head": "ce28abb4c665fc5216aecb083bdc8e5e30e8cc8f",
    "predecessor_published_records_parent": "66a3564ab2e7354f0b1c5b0649686611c758f8bc",
    "predecessor_records_ci_run": "30730441394",
    "predecessor_records_ci_status": "passed",
    "predecessor_records_review_status": "rejected",
    "rejected_records_candidates": "bf778c698064023bd3fe8e33abb8a1093c1dd8a0,21c21567029b1232e07ba85ca4d196820f3cfed9,7a2e239d6c152413b9404844b681634436c79061,befe0529533944300a323728f93d38d6dd8a59cd",
    "rejected_records_candidates_status": "rejected",
    "publication_action_at_commit": "not-yet-pushed",
    "final_receipt_ci_at_commit": "not-run",
    "final_receipt_ci_live_authority": "github-checks-for-@self",
    "package_build_status": "passed",
    "package_install_status": "not-run",
    "runtime_restart_status": "not-run",
    "physical_status": "not-run",
    "permissions_status": "not-run",
    "signing_status": "not-run",
    "notarisation_status": "not-run",
    "artifact_promotion_status": "not-run",
    "deployment_status": "not-run",
    "public_release_status": "not-run",
    "rollback_acceptance_status": "not-run",
    "owner_acceptance_status": "not-run",
    "product_tree_status": "unchanged",
    "package_tree_status": "unchanged",
    "workflow_status": "unchanged",
    "installer_status": "unchanged",
    "saved_checkout_status": "unchanged",
    "windows_package_members": "147",
    "windows_package_bytes": "1207711",
    "windows_package_sha256": "70794B4D13C1C38662425DEB5700865728955F4FAC78DC2D083436F63FB99493",
    "failed_ci_run": "30728545428",
    "cancelled_ci_runs": "30728750267,30730118039,30730292106",
    "document_structure_sha256": "6917609244202E3C118A4602E1DDEDD50E177458B8A010C39C193D6A05B79D1F",
    "non_projection_text_sha256": "7DF73E89BE7E3FD9F36B8CE8426E2FB60E96E2A8052D7C9A8BDB68CB85AEB07B",
    "current_record": "Entry 100",
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


def _residual_starts_forbidden_markup(residual: str) -> bool:
    """Classify only HTMLParser's unprocessed tail at end of input."""

    return residual.lstrip(_HTML_WHITESPACE).startswith(("<!", "<?"))


@dataclass(frozen=True)
class CurrentStatus:
    schema: str
    product_version: str
    authority_scope: str
    target_ref: str
    containing_commit: str
    source_merge: str
    accepted_source: str
    correction_parent: str
    published_pr_head: str
    ci_subject: str
    ci_run: str
    ci_status: str
    accepted_source_review_subject: str
    accepted_source_review_status: str
    accepted_source_review_task: str
    review_subject: str
    review_status: str
    review_task: str
    predecessor_published_records_head: str
    predecessor_published_records_parent: str
    predecessor_records_ci_run: str
    predecessor_records_ci_status: str
    predecessor_records_review_status: str
    rejected_records_candidates: str
    rejected_records_candidates_status: str
    publication_action_at_commit: str
    final_receipt_ci_at_commit: str
    final_receipt_ci_live_authority: str
    package_build_status: str
    package_install_status: str
    runtime_restart_status: str
    physical_status: str
    permissions_status: str
    signing_status: str
    notarisation_status: str
    artifact_promotion_status: str
    deployment_status: str
    public_release_status: str
    rollback_acceptance_status: str
    owner_acceptance_status: str
    product_tree_status: str
    package_tree_status: str
    workflow_status: str
    installer_status: str
    saved_checkout_status: str
    windows_package_members: str
    windows_package_bytes: str
    windows_package_sha256: str
    failed_ci_run: str
    cancelled_ci_runs: str
    document_structure_sha256: str
    non_projection_text_sha256: str
    current_record: str
    pr_number: str
    pr_status: str
    merge_status: str
    promotion_status: str
    replacement_ci_status: str
    open_issues: str
    acceptance_gates: str


@dataclass(frozen=True)
class _Projection:
    tag: str
    attributes: dict[str, str | None]
    text: str


_PROJECTION_TAGS = {
    "opening": "div",
    "package": "div",
    "tracker": "tr",
    "source-records": "tr",
    "packages": "tr",
    "ci": "tr",
    "runtime": "tr",
    "physical-release-owner": "tr",
    "verification": "p",
    "issue-30": "tr",
    "published-integration": "tr",
}
_PROJECTION_CLASSES = {"opening": "plain", "package": "callout"}


def _state_attributes(**fields: str) -> dict[str, str]:
    return {f"data-state-{name.replace('_', '-')}": value for name, value in fields.items()}


_PROJECTION_FIELDS = {
    "opening": (
        "product_version", "authority_scope", "target_ref", "containing_commit",
        "pr_number", "pr_status", "source_merge", "merge_status", "accepted_source",
        "ci_run", "ci_status", "accepted_source_review_status",
        "predecessor_published_records_head", "predecessor_published_records_parent",
        "predecessor_records_ci_run", "predecessor_records_ci_status",
        "predecessor_records_review_status", "rejected_records_candidates",
        "rejected_records_candidates_status", "review_subject", "review_status",
        "review_task", "current_record", "publication_action_at_commit",
        "final_receipt_ci_at_commit", "final_receipt_ci_live_authority",
        "package_build_status", "package_install_status", "runtime_restart_status",
        "physical_status", "permissions_status", "signing_status", "notarisation_status",
        "artifact_promotion_status", "deployment_status", "public_release_status",
        "rollback_acceptance_status", "owner_acceptance_status", "open_issues",
    ),
    "package": (
        "source_merge", "ci_run", "package_build_status", "package_install_status",
        "signing_status", "notarisation_status", "artifact_promotion_status",
        "deployment_status", "public_release_status", "physical_status",
        "owner_acceptance_status", "windows_package_members", "windows_package_bytes",
        "windows_package_sha256",
    ),
    "tracker": ("open_issues", "acceptance_gates"),
    "source-records": (
        "authority_scope", "target_ref", "containing_commit", "pr_number", "pr_status",
        "merge_status", "source_merge", "accepted_source",
        "predecessor_published_records_head", "predecessor_published_records_parent",
        "predecessor_records_ci_status", "predecessor_records_review_status",
        "rejected_records_candidates", "rejected_records_candidates_status",
        "review_subject", "review_status", "review_task", "current_record",
        "publication_action_at_commit", "final_receipt_ci_at_commit",
        "final_receipt_ci_live_authority",
        "product_tree_status", "package_tree_status", "workflow_status", "installer_status",
    ),
    "packages": (
        "package_build_status", "package_install_status", "artifact_promotion_status",
        "signing_status", "notarisation_status", "deployment_status", "public_release_status",
        "physical_status", "owner_acceptance_status",
    ),
    "ci": (
        "ci_subject", "ci_run", "ci_status", "predecessor_published_records_head",
        "predecessor_records_ci_run", "predecessor_records_ci_status",
        "predecessor_records_review_status", "containing_commit",
        "final_receipt_ci_at_commit", "final_receipt_ci_live_authority",
        "failed_ci_run", "cancelled_ci_runs",
    ),
    "runtime": ("runtime_restart_status", "saved_checkout_status"),
    "physical-release-owner": (
        "physical_status", "package_install_status", "permissions_status", "signing_status",
        "notarisation_status", "deployment_status", "public_release_status",
        "rollback_acceptance_status", "owner_acceptance_status", "acceptance_gates",
    ),
    "verification": (
        "accepted_source", "source_merge", "ci_run", "ci_status", "authority_scope",
        "target_ref", "containing_commit", "predecessor_published_records_head",
        "predecessor_records_ci_run", "predecessor_records_ci_status",
        "predecessor_records_review_status", "review_subject", "review_status",
        "publication_action_at_commit", "final_receipt_ci_at_commit",
        "final_receipt_ci_live_authority", "package_install_status", "physical_status",
        "permissions_status", "signing_status", "notarisation_status", "deployment_status",
        "public_release_status", "rollback_acceptance_status", "owner_acceptance_status",
        "acceptance_gates",
    ),
    "issue-30": (
        "accepted_source", "source_merge", "pr_number", "pr_status", "merge_status", "ci_run",
        "ci_status", "artifact_promotion_status", "target_ref", "containing_commit",
        "predecessor_published_records_head", "predecessor_records_ci_run",
        "predecessor_records_ci_status", "predecessor_records_review_status",
        "review_subject", "review_status", "publication_action_at_commit",
        "final_receipt_ci_at_commit", "final_receipt_ci_live_authority",
        "physical_status", "package_install_status", "signing_status", "public_release_status",
        "rollback_acceptance_status", "owner_acceptance_status", "acceptance_gates",
    ),
    "published-integration": (
        "authority_scope", "target_ref", "containing_commit",
        "predecessor_published_records_head", "predecessor_published_records_parent",
        "source_merge", "accepted_source", "pr_number", "pr_status", "merge_status",
        "ci_run", "ci_status", "predecessor_records_ci_run",
        "predecessor_records_ci_status", "predecessor_records_review_status",
        "rejected_records_candidates", "rejected_records_candidates_status",
        "review_subject", "review_status", "review_task", "current_record",
        "publication_action_at_commit", "final_receipt_ci_at_commit",
        "final_receipt_ci_live_authority", "package_install_status", "runtime_restart_status",
        "physical_status", "permissions_status", "signing_status", "notarisation_status",
        "artifact_promotion_status", "deployment_status", "public_release_status",
        "rollback_acceptance_status", "owner_acceptance_status",
    ),
}

_BASE_DOCUMENT_ATTRIBUTES = {
    "a": {"href"},
    "b": set(),
    "blockquote": set(),
    "body": set(),
    "code": set(),
    "div": {"class"},
    "em": set(),
    "h1": set(),
    "h2": set(),
    "head": set(),
    "header": {"class"},
    "html": {"lang"},
    "li": set(),
    "link": {"href", "rel"},
    "meta": {"charset", "content", "name"},
    "ol": set(),
    "p": set(),
    "script": {"data-current-state-authority", "id", "type"},
    "span": {"class"},
    "strong": set(),
    "table": set(),
    "tbody": set(),
    "td": set(),
    "th": set(),
    "thead": set(),
    "title": set(),
    "tr": {"data-evidence-boundary"},
    "ul": set(),
    "wbr": set(),
}
_ALLOWED_CLASS_VALUES = {
    "badge b-done",
    "badge b-exist",
    "badge b-gated",
    "callout",
    "doc-foot",
    "doc-head",
    "gold",
    "ico",
    "kicker",
    "l",
    "meta",
    "n",
    "plain",
    "sec",
    "tag",
    "tile",
    "tiles",
    "wrap",
}


def _document_attributes() -> dict[str, set[str]]:
    allowed = {tag: set(attributes) for tag, attributes in _BASE_DOCUMENT_ATTRIBUTES.items()}
    for key, fields in _PROJECTION_FIELDS.items():
        tag = _PROJECTION_TAGS[key]
        allowed[tag].update(
            {"id", "data-current-state-projection", "data-projection-schema"}
        )
        allowed[tag].update(f"data-state-{field.replace('_', '-')}" for field in fields)
    return allowed


_DOCUMENT_ATTRIBUTES = _document_attributes()


def _validate_document_element(
    tag: str, attributes: dict[str, str | None], *, is_authority: bool
) -> None:
    if tag not in _DOCUMENT_ATTRIBUTES:
        raise CurrentStatusContractError(f"HTML element {tag!r} is not allowed in STATUS")
    unexpected = sorted(set(attributes) - _DOCUMENT_ATTRIBUTES[tag])
    if unexpected:
        raise CurrentStatusContractError(
            f"HTML element {tag!r} has unexpected attributes {unexpected}"
        )

    class_value = attributes.get("class")
    if class_value is not None and class_value not in _ALLOWED_CLASS_VALUES:
        raise CurrentStatusContractError(
            f"HTML element {tag!r} has an unsupported class value"
        )
    if "data-evidence-boundary" in attributes and attributes["data-evidence-boundary"] != "merged-source":
        raise CurrentStatusContractError("STATUS has an unsupported evidence-boundary value")
    if tag == "html" and attributes != {"lang": "en"}:
        raise CurrentStatusContractError("STATUS html element must declare exact language")
    if tag == "meta" and attributes not in (
        {"charset": "utf-8"},
        {"name": "viewport", "content": "width=device-width, initial-scale=1"},
    ):
        raise CurrentStatusContractError("STATUS has an unsupported meta element")
    if tag == "link" and attributes != {"rel": "stylesheet", "href": "../_assets/docs.css"}:
        raise CurrentStatusContractError("STATUS has an unsupported linked resource")
    if tag == "script" and not is_authority:
        raise CurrentStatusContractError("only the canonical current-state script is allowed")
    if tag == "a":
        href = attributes.get("href")
        if href is None:
            raise CurrentStatusContractError("STATUS links must have an href")
        parsed = urlsplit(href)
        if parsed.scheme not in {"", "https"} or (not parsed.scheme and parsed.netloc):
            raise CurrentStatusContractError("STATUS link href is not a safe local or HTTPS target")


def _expected_projection_attributes(current: CurrentStatus) -> dict[str, dict[str, str]]:
    return {
        key: _state_attributes(**{field: getattr(current, field) for field in fields})
        for key, fields in _PROJECTION_FIELDS.items()
    }


def _expected_projection_text(current: CurrentStatus) -> dict[str, str]:
    open_issues = ", ".join(f"#{issue}" for issue in current.open_issues.split(","))
    cancelled_runs = ", ".join(current.cancelled_ci_runs.split(","))
    rejected_candidates = current.rejected_records_candidates.split(",")
    rejected_records = ", ".join(rejected_candidates)
    rejected_receipt = rejected_candidates[-1]
    gate_statuses = (
        f"package install={current.package_install_status}; runtime restart="
        f"{current.runtime_restart_status}; physical={current.physical_status}; permissions="
        f"{current.permissions_status}; signing={current.signing_status}; notarisation="
        f"{current.notarisation_status}; artifact promotion={current.artifact_promotion_status}; "
        f"deployment={current.deployment_status}; public release={current.public_release_status}; "
        f"rollback acceptance={current.rollback_acceptance_status}; owner acceptance="
        f"{current.owner_acceptance_status}"
    )
    scope_text = (
        f"Authority scope is {current.authority_scope}. Target ref is {current.target_ref}. "
        f"Symbolic containing commit {current.containing_commit} resolves to the Git commit "
        f"containing this STATUS file; this authority is current only when read from "
        f"{current.target_ref}."
    )
    receipt_ci_text = (
        f"At commit time, publication action was {current.publication_action_at_commit} and "
        f"final receipt CI status was {current.final_receipt_ci_at_commit}. Later live CI "
        f"authority is {current.final_receipt_ci_live_authority}; later push and CI outcomes "
        f"come from {current.target_ref} and GitHub checks, not a rewritten Core claim."
    )
    review_history_text = (
        f"Receipt predecessor {current.review_subject} has review status "
        f"{current.review_status} under task {current.review_task}: no P0–P2 findings and "
        f"one non-blocking P3 for raw AssertionError leakage. Rejected receipt "
        f"{rejected_receipt} corrected that P3 and is rejected solely for misreporting "
        f"the predecessor review."
    )
    return {
        "opening": (
            f"Current truth Mumble {current.product_version} publication receipt. {scope_text} "
            f"PR #{current.pr_number} status is {current.pr_status}; merge status is "
            f"{current.merge_status} at source merge {current.source_merge}, containing "
            f"independently accepted source {current.accepted_source}. Exact-source CI run "
            f"{current.ci_run} status is {current.ci_status}; accepted-source review status is "
            f"{current.accepted_source_review_status}. Predecessor published records head "
            f"{current.predecessor_published_records_head}, parent "
            f"{current.predecessor_published_records_parent}, has records CI run "
            f"{current.predecessor_records_ci_run} status "
            f"{current.predecessor_records_ci_status} and independent semantic review status "
            f"{current.predecessor_records_review_status}. Records correction chain "
            f"{rejected_records} has status {current.rejected_records_candidates_status}. "
            f"{review_history_text} "
            f"{current.current_record} records the symbolic non-self-referential receipt. "
            f"{receipt_ci_text} Package build status is {current.package_build_status}; "
            f"{gate_statuses}. Issues {open_issues} remain open."
        ),
        "package": (
            f"Published package distinction: Source merge {current.source_merge[:8]} retains two "
            f"tracked byte-identical {current.windows_package_members}-member Windows ZIPs, "
            f"{int(current.windows_package_bytes):,} bytes each, SHA-256 "
            f"{current.windows_package_sha256}. CI run {current.ci_run} package build status is "
            f"{current.package_build_status}. Package install={current.package_install_status}; "
            f"signing={current.signing_status}; notarisation={current.notarisation_status}; "
            f"artifact promotion={current.artifact_promotion_status}; deployment="
            f"{current.deployment_status}; public release={current.public_release_status}; "
            f"physical={current.physical_status}; owner acceptance="
            f"{current.owner_acceptance_status}."
        ),
        "tracker": (
            f"Planning and GitHub tracker Open publication and acceptance issues: {open_issues}. "
            f"Acceptance gates are {current.acceptance_gates}; source completion does not close them."
        ),
        "source-records": (
            f"Source and records {scope_text} PR #{current.pr_number} status is "
            f"{current.pr_status}; merge status is {current.merge_status}. Accepted source "
            f"{current.accepted_source} is contained by source merge {current.source_merge}. "
            f"Predecessor published records head {current.predecessor_published_records_head}, "
            f"parent {current.predecessor_published_records_parent}, has CI status "
            f"{current.predecessor_records_ci_status} and review status "
            f"{current.predecessor_records_review_status}. Records correction chain "
            f"{rejected_records} has status {current.rejected_records_candidates_status}. "
            f"{review_history_text} "
            f"{current.current_record} records the symbolic containing-commit receipt. "
            f"{receipt_ci_text} Product tree={current.product_tree_status}; package tree="
            f"{current.package_tree_status}; workflow={current.workflow_status}; installers="
            f"{current.installer_status}."
        ),
        "packages": (
            f"Packages Package build={current.package_build_status}; install="
            f"{current.package_install_status}; artifact promotion="
            f"{current.artifact_promotion_status}; signing={current.signing_status}; "
            f"notarisation={current.notarisation_status}; deployment="
            f"{current.deployment_status}; public release={current.public_release_status}; "
            f"physical={current.physical_status}; owner acceptance="
            f"{current.owner_acceptance_status}."
        ),
        "ci": (
            f"CI Exact-source run {current.ci_run} status is {current.ci_status} for subject "
            f"{current.ci_subject}. Predecessor records-head run "
            f"{current.predecessor_records_ci_run} status is "
            f"{current.predecessor_records_ci_status} for "
            f"{current.predecessor_published_records_head}; predecessor semantic review status "
            f"is {current.predecessor_records_review_status}. For symbolic containing commit "
            f"{current.containing_commit}, final receipt CI at commit time was "
            f"{current.final_receipt_ci_at_commit}; later live authority is "
            f"{current.final_receipt_ci_live_authority}. Failed run {current.failed_ci_run} "
            f"remains red evidence. Cancelled runs {cancelled_runs} are not evidence."
        ),
        "runtime": (
            f"Runtime Runtime restart status is {current.runtime_restart_status}; this Core-only "
            f"receipt leaves saved checkout status {current.saved_checkout_status}."
        ),
        "physical-release-owner": (
            f"Physical, release, and owner Package install={current.package_install_status}; "
            f"physical={current.physical_status}; permissions={current.permissions_status}; "
            f"signing={current.signing_status}; notarisation={current.notarisation_status}; "
            f"deployment={current.deployment_status}; public release="
            f"{current.public_release_status}; rollback acceptance="
            f"{current.rollback_acceptance_status}; owner acceptance="
            f"{current.owner_acceptance_status}. Acceptance gates are "
            f"{current.acceptance_gates}."
        ),
        "verification": (
            f"Accepted source {current.accepted_source}, contained by source merge "
            f"{current.source_merge}, has exact-source run {current.ci_run} status "
            f"{current.ci_status}. {scope_text} Predecessor published records head "
            f"{current.predecessor_published_records_head} has run "
            f"{current.predecessor_records_ci_run} status "
            f"{current.predecessor_records_ci_status} and review status "
            f"{current.predecessor_records_review_status}. {review_history_text} "
            f"{receipt_ci_text} {gate_statuses}. Acceptance gates are "
            f"{current.acceptance_gates}."
        ),
        "issue-30": (
            f"#30 final parity and promotion Acceptance gates are {current.acceptance_gates}. "
            f"Accepted source {current.accepted_source} is contained by source merge "
            f"{current.source_merge}; PR #{current.pr_number} status is {current.pr_status} and "
            f"merge status is {current.merge_status}. Exact-source run {current.ci_run} status "
            f"is {current.ci_status}; artifact promotion status is "
            f"{current.artifact_promotion_status}. Target ref {current.target_ref} resolves "
            f"symbolic containing commit {current.containing_commit}. Predecessor published "
            f"records head {current.predecessor_published_records_head} has run "
            f"{current.predecessor_records_ci_run} status "
            f"{current.predecessor_records_ci_status} and review status "
            f"{current.predecessor_records_review_status}. {review_history_text} "
            f"At commit time, publication action was {current.publication_action_at_commit} and "
            f"final receipt CI was {current.final_receipt_ci_at_commit}; later CI authority is "
            f"{current.final_receipt_ci_live_authority}. Package install="
            f"{current.package_install_status}; physical={current.physical_status}; signing="
            f"{current.signing_status}; public release={current.public_release_status}; "
            f"rollback acceptance={current.rollback_acceptance_status}; owner acceptance="
            f"{current.owner_acceptance_status}."
        ),
        "published-integration": (
            f"Current published integration receipt {scope_text} Predecessor published records "
            f"head {current.predecessor_published_records_head}, parent "
            f"{current.predecessor_published_records_parent}, is the prior published record. "
            f"Source merge {current.source_merge} contains accepted source "
            f"{current.accepted_source}. PR #{current.pr_number} status is "
            f"{current.pr_status}; merge status is {current.merge_status}; exact-source run "
            f"{current.ci_run} status is {current.ci_status}; predecessor records run "
            f"{current.predecessor_records_ci_run} status is "
            f"{current.predecessor_records_ci_status}; predecessor review status is "
            f"{current.predecessor_records_review_status}. Records correction chain "
            f"{rejected_records} has status {current.rejected_records_candidates_status}. "
            f"{review_history_text} "
            f"{current.current_record} records the symbolic receipt. {receipt_ci_text} "
            f"{gate_statuses}."
        ),
    }
def _validate_projections(
    projections: dict[str, _Projection], current: CurrentStatus
) -> None:
    expected_attributes = _expected_projection_attributes(current)
    expected_text = _expected_projection_text(current)
    expected_keys = set(_PROJECTION_TAGS)
    actual_keys = set(projections)
    if actual_keys != expected_keys:
        raise CurrentStatusContractError(
            f"current-state projection set differs; missing={sorted(expected_keys - actual_keys)}, "
            f"unexpected={sorted(actual_keys - expected_keys)}"
        )

    for key in _PROJECTION_TAGS:
        projection = projections[key]
        if projection.tag != _PROJECTION_TAGS[key]:
            raise CurrentStatusContractError(f"projection {key!r} has the wrong HTML element")
        required_base = {
            "id": f"current-state-{key}",
            "data-current-state-projection": key,
            "data-projection-schema": PROJECTION_SCHEMA,
        }
        if key in _PROJECTION_CLASSES:
            required_base["class"] = _PROJECTION_CLASSES[key]
        semantic = {
            name: value
            for name, value in projection.attributes.items()
            if name.startswith("data-state-")
        }
        nonsemantic = {
            name: value
            for name, value in projection.attributes.items()
            if not name.startswith("data-state-")
        }
        if nonsemantic != required_base:
            raise CurrentStatusContractError(
                f"projection {key!r} identity attributes differ from the contract"
            )
        if semantic != expected_attributes[key]:
            raise CurrentStatusContractError(
                f"projection {key!r} semantic attributes differ from current state"
            )
        if projection.text != expected_text[key]:
            raise CurrentStatusContractError(
                f"projection {key!r} visible text differs from the canonical rendering"
            )


class _AuthorityHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.authorities: list[str] = []
        self._capturing = False
        self._content: list[str] = []
        self.projections: dict[str, _Projection] = {}
        self._projection_key: str | None = None
        self._projection_tag: str | None = None
        self._projection_attributes: dict[str, str | None] = {}
        self._projection_content: list[str] = []
        self._projection_depth = 0
        self._outside_projection_content: list[str] = []
        self._document_tokens: list[str] = []
        self._doctype_count = 0

    def handle_decl(self, decl: str) -> None:
        if decl != "DOCTYPE html" or self._doctype_count:
            raise CurrentStatusContractError(_FORBIDDEN_MARKUP_ERROR)
        self._doctype_count += 1
        self._document_tokens.append("D:DOCTYPE html")

    def handle_comment(self, data: str) -> None:
        raise CurrentStatusContractError(_FORBIDDEN_MARKUP_ERROR)

    def handle_pi(self, data: str) -> None:
        raise CurrentStatusContractError(_FORBIDDEN_MARKUP_ERROR)

    def unknown_decl(self, data: str) -> None:
        raise CurrentStatusContractError(_FORBIDDEN_MARKUP_ERROR)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attribute_names = [name for name, _value in attrs]
        attributes = dict(attrs)
        is_authority = any(
            (name == "id" and value == AUTHORITY_ID)
            or name == "data-current-state-authority"
            for name, value in attrs
        )
        projection_key = attributes.get("data-current-state-projection")

        if len(attribute_names) != len(set(attribute_names)):
            raise CurrentStatusContractError(
                f"HTML element {tag!r} has duplicate HTML attributes"
            )
        _validate_document_element(tag, attributes, is_authority=is_authority)
        self._document_tokens.append(
            "S:" + json.dumps([tag, sorted(attrs)], separators=(",", ":"))
        )

        if projection_key is not None:
            if self._projection_key is not None:
                raise CurrentStatusContractError("current-state projections may not be nested")
            if projection_key in self.projections:
                raise CurrentStatusContractError(
                    f"current-state projection {projection_key!r} appears more than once"
                )
            self._projection_key = projection_key
            self._projection_tag = tag
            self._projection_attributes = attributes
            self._projection_content = []
            self._projection_depth = 1
        elif self._projection_key is not None:
            if tag in {"div", "p", "td", "th", "li"}:
                self._projection_content.append(" ")
            self._projection_depth += 1

        if is_authority:
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
        else:
            normalized = " ".join(data.split())
            if normalized:
                self._document_tokens.append(
                    "T:" + json.dumps(normalized, separators=(",", ":"))
                )
            if self._projection_key is not None:
                self._projection_content.append(data)
            else:
                self._outside_projection_content.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag not in _DOCUMENT_ATTRIBUTES:
            raise CurrentStatusContractError(f"HTML element {tag!r} is not allowed in STATUS")
        self._document_tokens.append(f"E:{tag}")
        if self._capturing and tag == "script":
            self.authorities.append("".join(self._content))
            self._capturing = False
            self._content = []
        if self._projection_key is not None:
            if tag in {"div", "p", "td", "th", "li"}:
                self._projection_content.append(" ")
            self._projection_depth -= 1
            if self._projection_depth == 0:
                key = self._projection_key
                if tag != self._projection_tag:
                    raise CurrentStatusContractError(
                        f"current-state projection {key!r} is not closed correctly"
                    )
                self.projections[key] = _Projection(
                    tag=self._projection_tag,
                    attributes=self._projection_attributes,
                    text=" ".join("".join(self._projection_content).split()),
                )
                self._projection_key = None
                self._projection_tag = None
                self._projection_attributes = {}
                self._projection_content = []

    def non_projection_text_sha256(self) -> str:
        normalized = " ".join(" ".join(self._outside_projection_content).split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest().upper()

    def document_structure_sha256(self) -> str:
        canonical = "\n".join(self._document_tokens)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest().upper()


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
    try:
        parser.feed(status_html)
        residual_at_eof = parser.rawdata
        if _residual_starts_forbidden_markup(residual_at_eof):
            raise CurrentStatusContractError(_FORBIDDEN_MARKUP_ERROR)
        parser.close()
    except CurrentStatusContractError:
        raise
    except AssertionError as exc:
        message = str(exc)
        if not (
            re.fullmatch(r"unknown status keyword '.+' in marked section", message)
            or message.startswith("expected name token at '")
        ):
            raise
        raise CurrentStatusContractError(_FORBIDDEN_MARKUP_ERROR) from exc
    if parser._doctype_count != 1:
        raise CurrentStatusContractError("STATUS must have one exact HTML doctype")
    if parser._capturing:
        raise CurrentStatusContractError("current-state authority is not closed")
    if parser._projection_key is not None:
        raise CurrentStatusContractError("current-state projection is not closed")
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
    if fields["accepted_source_review_subject"] != fields["accepted_source"]:
        raise CurrentStatusContractError(
            "the accepted source review subject must be the accepted source"
        )
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
    if fields["predecessor_published_records_parent"] != fields["source_merge"]:
        raise CurrentStatusContractError(
            "the predecessor published records parent must be the source merge"
        )
    rejected_candidates = fields["rejected_records_candidates"].split(",")
    if len(rejected_candidates) != 4 or any(
        not re.fullmatch(r"[0-9a-f]{40}", candidate)
        for candidate in rejected_candidates
    ):
        raise CurrentStatusContractError(
            "the rejected records correction chain must contain four exact commit identities"
        )
    if fields["review_subject"] in rejected_candidates:
        raise CurrentStatusContractError(
            "the accepted receipt predecessor cannot also be in the rejected correction chain"
        )
    allowed_values = {
        "ci_status": {"passed", "failed", "not-run", "cancelled"},
        "accepted_source_review_status": {"accepted", "rejected"},
        "review_status": {"accepted", "rejected"},
        "predecessor_records_ci_status": {"passed", "failed", "not-run", "cancelled"},
        "predecessor_records_review_status": {"accepted", "rejected"},
        "rejected_records_candidates_status": {"accepted", "rejected"},
        "publication_action_at_commit": {"not-yet-pushed"},
        "final_receipt_ci_at_commit": {"not-run"},
        "pr_status": {"open", "merged"},
        "merge_status": {"unmerged", "merged"},
        "promotion_status": {"not-promoted", "source-merged"},
        "package_build_status": {"passed", "failed", "not-run"},
        "package_install_status": {"passed", "failed", "not-run"},
        "runtime_restart_status": {"passed", "failed", "not-run"},
        "physical_status": {"passed", "failed", "not-run"},
        "permissions_status": {"passed", "failed", "not-run"},
        "signing_status": {"passed", "failed", "not-run"},
        "notarisation_status": {"passed", "failed", "not-run"},
        "artifact_promotion_status": {"completed", "failed", "not-run"},
        "deployment_status": {"completed", "failed", "not-run"},
        "public_release_status": {"completed", "failed", "not-run"},
        "rollback_acceptance_status": {"passed", "failed", "not-run"},
        "owner_acceptance_status": {"accepted", "rejected", "not-run"},
        "acceptance_gates": {"open", "closed"},
        "product_tree_status": {"unchanged", "changed"},
        "package_tree_status": {"unchanged", "changed"},
        "workflow_status": {"unchanged", "changed"},
        "installer_status": {"unchanged", "changed"},
        "saved_checkout_status": {"unchanged", "changed"},
    }
    for field, allowed in allowed_values.items():
        if fields[field] not in allowed:
            raise CurrentStatusContractError(
                f"current-state field {field!r} has unsupported value {fields[field]!r}"
            )
    if not fields["windows_package_members"].isdigit():
        raise CurrentStatusContractError("Windows package member count must be numeric")
    if not fields["windows_package_bytes"].isdigit():
        raise CurrentStatusContractError("Windows package byte count must be numeric")
    if not re.fullmatch(r"[0-9A-F]{64}", fields["windows_package_sha256"]):
        raise CurrentStatusContractError("Windows package SHA-256 must be uppercase hexadecimal")
    if parser.non_projection_text_sha256() != fields["non_projection_text_sha256"]:
        raise CurrentStatusContractError(
            "human-visible text outside current-state projections differs from the contract"
        )
    current = CurrentStatus(**fields)
    _validate_projections(parser.projections, current)
    if parser.document_structure_sha256() != fields["document_structure_sha256"]:
        raise CurrentStatusContractError(
            "canonical STATUS document structure or attribute values differ from the contract"
        )
    return current


def _canonical_status_html() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "Development Files"
        / "Core"
        / "STATUS.html"
    ).read_text(encoding="utf-8")


def test_current_status_contract_accepts_the_canonical_authority() -> None:
    current = parse_current_status(_canonical_status_html())

    assert current.current_record == "Entry 100"


def test_every_present_state_row_agrees_with_the_canonical_authority() -> None:
    status = _canonical_status_html()
    current = parse_current_status(status)
    parser = _AuthorityHTMLParser()
    parser.feed(status)
    parser.close()

    assert set(parser.projections) == set(_PROJECTION_TAGS)
    _validate_projections(parser.projections, current)


@pytest.mark.parametrize(
    "dispatch_method",
    ("unknown_decl", "handle_comment", "assertion"),
)
@pytest.mark.parametrize(
    "attack",
    (
        "<![CDATA[PR #49 is open and CI failed]]>",
        "<![IGNORE[PR #49 is open]]>",
        (
            "<![CDATA[<p>PR #49 is open; CI failed; source unmerged; "
            "release completed.</p>]]>"
        ),
        "<![cDaTa[PR #49 is open and CI failed]]>",
        "<![ignore[PR #49 is open]]>",
    ),
)
def test_marked_declaration_error_is_stable_across_parser_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    dispatch_method: str,
    attack: str,
) -> None:
    status = _canonical_status_html()
    mutated = status.replace("<body>", f"<body>{attack}", 1)
    if dispatch_method == "assertion":
        def simulated_dispatch(_parser: object, _data: str) -> None:
            raise AssertionError("unknown status keyword 'CDATA' in marked section")
    else:
        simulated_dispatch = getattr(_AuthorityHTMLParser, dispatch_method)
    monkeypatch.setattr(_AuthorityHTMLParser, "unknown_decl", simulated_dispatch)
    monkeypatch.setattr(_AuthorityHTMLParser, "handle_comment", simulated_dispatch)

    with pytest.raises(CurrentStatusContractError) as caught:
        parse_current_status(mutated)

    assert type(caught.value) is CurrentStatusContractError
    assert str(caught.value) == _FORBIDDEN_MARKUP_ERROR


@pytest.mark.parametrize(
    "attack",
    (
        "<!-- forbidden comment -->",
        "<?forbidden processing instruction?>",
        "<![BOGUS[forbidden declaration]]>",
        "<!DOCTYPE html>",
        "<!doctype html>",
        "<![x",
    ),
)
def test_forbidden_markup_routes_share_one_exact_domain_error(attack: str) -> None:
    status = _canonical_status_html()
    mutated = status.replace("<body>", f"<body>{attack}", 1)

    with pytest.raises(CurrentStatusContractError) as caught:
        parse_current_status(mutated)

    assert type(caught.value) is CurrentStatusContractError
    assert str(caught.value) == _FORBIDDEN_MARKUP_ERROR


@pytest.mark.parametrize(
    "attack",
    (
        "<!--",
        "<!-- unclosed",
        "<!-->",
        "<!--->",
        "<!--!>",
        "<!-- --!>",
        "<![",
        "<![C",
        "<![CDATA",
        "<![CDATA[",
        "<![CDATA[value",
        "<![IGNORE",
        "<![IGNORE[",
        "<![IGNORE[value",
        "<![BOGUS",
        "<!",
        "<!D",
        "<!DOCTYPE",
        "<!DOCTYPE html",
        "<?",
        "<?probe",
        "<?probe value",
    ),
)
def test_incomplete_forbidden_markup_at_eof_uses_exact_domain_error(
    attack: str,
) -> None:
    with pytest.raises(CurrentStatusContractError) as caught:
        parse_current_status(_canonical_status_html() + attack)

    assert type(caught.value) is CurrentStatusContractError
    assert str(caught.value) == _FORBIDDEN_MARKUP_ERROR


@pytest.mark.parametrize(
    "attack",
    (
        "<!-- unclosed",
        "<![CDATA[value",
        "<![IGNORE[value",
        "<![BOGUS",
        "<!DOCTYPE html",
        "<?probe value",
    ),
)
def test_html_parser_residual_starts_at_incomplete_forbidden_markup(
    attack: str,
) -> None:
    parser = _AuthorityHTMLParser()

    parser.feed(_canonical_status_html() + "ordinary text \t\r\n" + attack)
    residual_before_close = parser.rawdata
    parser.close()

    assert residual_before_close == attack
    assert parser.rawdata == ""


def test_canonical_status_leaves_no_html_parser_residual() -> None:
    parser = _AuthorityHTMLParser()

    parser.feed(_canonical_status_html())
    residual_before_close = parser.rawdata
    parser.close()

    assert residual_before_close == ""
    assert parser.rawdata == ""


def test_deferred_ordinary_text_reaches_the_fingerprint_contract() -> None:
    parser = _AuthorityHTMLParser()
    parser.feed(_canonical_status_html() + "ordinary text &")
    residual_before_close = parser.rawdata
    parser.close()

    assert residual_before_close == "\nordinary text &"
    assert parser.rawdata == ""

    with pytest.raises(CurrentStatusContractError) as caught:
        parse_current_status(_canonical_status_html() + "ordinary text &")

    assert type(caught.value) is CurrentStatusContractError
    assert str(caught.value) == (
        "human-visible text outside current-state projections differs from the contract"
    )


@pytest.mark.parametrize(
    "tail",
    ("text &", "&", "&#", "<", "<div", "</div"),
)
def test_non_forbidden_residual_tails_reach_the_fingerprint_contract(
    tail: str,
) -> None:
    with pytest.raises(CurrentStatusContractError) as caught:
        parse_current_status(_canonical_status_html() + tail)

    assert type(caught.value) is CurrentStatusContractError
    assert str(caught.value) == (
        "human-visible text outside current-state projections differs from the contract"
    )


def test_malformed_declaration_assertion_from_close_uses_forbidden_markup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_with_declaration_assertion(_parser: object) -> None:
        raise AssertionError("expected name token at '![x'")

    monkeypatch.setattr(_AuthorityHTMLParser, "close", fail_with_declaration_assertion)

    with pytest.raises(CurrentStatusContractError) as caught:
        parse_current_status(_canonical_status_html())

    assert type(caught.value) is CurrentStatusContractError
    assert str(caught.value) == _FORBIDDEN_MARKUP_ERROR


@pytest.mark.parametrize(
    ("anchor", "replacement", "expected_error"),
    (
        (
            "<body>",
            "<body><!-- literal <![CDATA[not markup]]> -->",
            _FORBIDDEN_MARKUP_ERROR,
        ),
        (
            '"product_version": "0.95"',
            '"product_version": "0.95 <![CDATA[not markup]]>"',
            "current-state field 'product_version' must be '0.95', got '0.95 <![CDATA[not markup]]>'",
        ),
        (
            "<body>",
            "<body data-probe='literal <![CDATA[not > markup]]>'>",
            "HTML element 'body' has unexpected attributes ['data-probe']",
        ),
        (
            "<body>",
            '<body><script>const marker = "<![CDATA[not markup]]>";</script>',
            "only the canonical current-state script is allowed",
        ),
        (
            "<body>",
            '<body><style>.probe::after { content: "<![CDATA[not markup]]>"; }</style>',
            "HTML element 'style' is not allowed in STATUS",
        ),
        (
            "<body>",
            "<body><title>literal &lt;![CDATA[not markup]]&gt;</title>",
            "human-visible text outside current-state projections differs from the contract",
        ),
        (
            "<body>",
            "<body><textarea>literal &lt;![CDATA[not markup]]&gt;</textarea>",
            "HTML element 'textarea' is not allowed in STATUS",
        ),
    ),
)
def test_marked_literals_reach_their_context_specific_contract_error(
    anchor: str,
    replacement: str,
    expected_error: str,
) -> None:
    status = _canonical_status_html()
    mutated = status.replace(anchor, replacement, 1)

    with pytest.raises(CurrentStatusContractError) as caught:
        parse_current_status(mutated)

    assert type(caught.value) is CurrentStatusContractError
    assert str(caught.value) == expected_error
