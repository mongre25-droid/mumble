"""Shared parser for the canonical machine-readable Core present state."""

from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
from typing import Any


AUTHORITY_ID = "mumble-current-state"
AUTHORITY_SCHEMA = "mumble.current-state.v1"
PROJECTION_SCHEMA = "mumble.current-state-projection.v1"

_EXPECTED_FIELDS = {
    "schema": AUTHORITY_SCHEMA,
    "product_version": "0.95",
    "main": "ce28abb4c665fc5216aecb083bdc8e5e30e8cc8f",
    "source_merge": "66a3564ab2e7354f0b1c5b0649686611c758f8bc",
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
    "rejected_records_candidate": "bf778c698064023bd3fe8e33abb8a1093c1dd8a0",
    "rejected_records_candidate_status": "rejected",
    "records_correction_parent": "bf778c698064023bd3fe8e33abb8a1093c1dd8a0",
    "records_review_task": "019fc013-3ab9-7bc0-9c39-f63385bb8359",
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
    "correction_self_sha_status": "omitted",
    "windows_package_members": "147",
    "windows_package_bytes": "1207711",
    "windows_package_sha256": "70794B4D13C1C38662425DEB5700865728955F4FAC78DC2D083436F63FB99493",
    "failed_ci_run": "30728545428",
    "cancelled_ci_runs": "30728750267,30730118039,30730292106",
    "non_projection_text_sha256": "7DF73E89BE7E3FD9F36B8CE8426E2FB60E96E2A8052D7C9A8BDB68CB85AEB07B",
    "current_record": "Entry 96",
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
    product_version: str
    main: str
    source_merge: str
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
    rejected_records_candidate: str
    rejected_records_candidate_status: str
    records_correction_parent: str
    records_review_task: str
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
    correction_self_sha_status: str
    windows_package_members: str
    windows_package_bytes: str
    windows_package_sha256: str
    failed_ci_run: str
    cancelled_ci_runs: str
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
_HUMAN_VISIBLE_ATTRIBUTES = {
    "alt",
    "aria-description",
    "aria-label",
    "placeholder",
    "title",
    "value",
}


def _state_attributes(**fields: str) -> dict[str, str]:
    return {f"data-state-{name.replace('_', '-')}": value for name, value in fields.items()}


_PROJECTION_FIELDS = {
    "opening": (
        "product_version", "pr_number", "pr_status", "main", "source_merge", "merge_status",
        "accepted_source", "ci_run", "ci_status", "published_records_head",
        "published_records_parent", "records_ci_run", "records_ci_status",
        "records_review_status", "rejected_records_candidate",
        "rejected_records_candidate_status", "current_record", "record_candidate_status",
        "package_build_status", "package_install_status", "runtime_restart_status",
        "physical_status", "permissions_status", "signing_status", "notarisation_status",
        "artifact_promotion_status", "deployment_status", "public_release_status",
        "rollback_acceptance_status", "owner_acceptance_status", "open_issues",
        "correction_self_sha_status",
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
        "pr_number", "pr_status", "merge_status", "main", "source_merge", "accepted_source",
        "published_records_head", "published_records_parent", "records_ci_status",
        "records_review_status", "rejected_records_candidate",
        "rejected_records_candidate_status", "current_record", "record_candidate_status",
        "product_tree_status", "package_tree_status", "workflow_status", "installer_status",
        "correction_self_sha_status",
    ),
    "packages": (
        "package_build_status", "package_install_status", "artifact_promotion_status",
        "signing_status", "notarisation_status", "deployment_status", "public_release_status",
        "physical_status", "owner_acceptance_status",
    ),
    "ci": (
        "ci_subject", "ci_run", "ci_status", "published_records_head", "records_ci_run",
        "records_ci_status", "records_review_status", "failed_ci_run", "cancelled_ci_runs",
    ),
    "runtime": ("runtime_restart_status", "saved_checkout_status"),
    "physical-release-owner": (
        "physical_status", "package_install_status", "permissions_status", "signing_status",
        "notarisation_status", "deployment_status", "public_release_status",
        "rollback_acceptance_status", "owner_acceptance_status", "acceptance_gates",
    ),
    "verification": (
        "accepted_source", "source_merge", "ci_run", "ci_status", "main",
        "published_records_head", "records_ci_run", "records_ci_status",
        "records_review_status", "package_install_status", "physical_status",
        "permissions_status", "signing_status", "notarisation_status", "deployment_status",
        "public_release_status", "rollback_acceptance_status", "owner_acceptance_status",
        "acceptance_gates",
    ),
    "issue-30": (
        "accepted_source", "source_merge", "pr_number", "pr_status", "merge_status", "ci_run",
        "ci_status", "artifact_promotion_status", "published_records_head", "records_ci_run",
        "records_ci_status", "records_review_status", "record_candidate_status",
        "physical_status", "package_install_status", "signing_status", "public_release_status",
        "rollback_acceptance_status", "owner_acceptance_status", "acceptance_gates",
    ),
    "published-integration": (
        "main", "published_records_head", "published_records_parent", "source_merge",
        "accepted_source", "pr_number", "pr_status", "merge_status", "ci_run", "ci_status",
        "records_ci_run", "records_ci_status", "records_review_status",
        "rejected_records_candidate", "rejected_records_candidate_status", "current_record",
        "record_candidate_status", "correction_self_sha_status", "package_install_status",
        "runtime_restart_status",
        "physical_status", "permissions_status", "signing_status", "notarisation_status",
        "artifact_promotion_status", "deployment_status", "public_release_status",
        "rollback_acceptance_status", "owner_acceptance_status",
    ),
}


def _expected_projection_attributes(current: CurrentStatus) -> dict[str, dict[str, str]]:
    return {
        key: _state_attributes(**{field: getattr(current, field) for field in fields})
        for key, fields in _PROJECTION_FIELDS.items()
    }


def _expected_projection_text(current: CurrentStatus) -> dict[str, str]:
    open_issues = ", ".join(f"#{issue}" for issue in current.open_issues.split(","))
    cancelled_runs = ", ".join(current.cancelled_ci_runs.split(","))
    gate_statuses = (
        f"package install={current.package_install_status}; runtime restart="
        f"{current.runtime_restart_status}; physical={current.physical_status}; permissions="
        f"{current.permissions_status}; signing={current.signing_status}; notarisation="
        f"{current.notarisation_status}; artifact promotion={current.artifact_promotion_status}; "
        f"deployment={current.deployment_status}; public release={current.public_release_status}; "
        f"rollback acceptance={current.rollback_acceptance_status}; owner acceptance="
        f"{current.owner_acceptance_status}"
    )
    return {
        "opening": (
            f"Current truth Mumble {current.product_version} publication truth. Remote main is "
            f"{current.main}. PR #{current.pr_number} status is {current.pr_status}; merge status is "
            f"{current.merge_status} at source merge {current.source_merge}, containing independently "
            f"accepted source {current.accepted_source}. Exact-source CI run {current.ci_run} status is "
            f"{current.ci_status}. Published records head {current.published_records_head}, parent "
            f"{current.published_records_parent}, has records CI run {current.records_ci_run} status "
            f"{current.records_ci_status} and review status {current.records_review_status}. Local "
            f"records candidate {current.rejected_records_candidate} has review status "
            f"{current.rejected_records_candidate_status}; {current.current_record} documents its "
            f"direct-child correction with candidate status {current.record_candidate_status}; "
            f"correction self-SHA status is {current.correction_self_sha_status}. Package build status "
            f"is {current.package_build_status}; {gate_statuses}. Issues "
            f"{open_issues} remain open."
        ),
        "package": (
            f"Published package distinction: Source merge {current.source_merge[:8]} retains two tracked "
            f"byte-identical {current.windows_package_members}-member Windows ZIPs, "
            f"{int(current.windows_package_bytes):,} bytes each, SHA-256 "
            f"{current.windows_package_sha256}. CI run {current.ci_run} package build status is "
            f"{current.package_build_status}. Package install={current.package_install_status}; signing="
            f"{current.signing_status}; notarisation={current.notarisation_status}; artifact promotion="
            f"{current.artifact_promotion_status}; deployment={current.deployment_status}; public release="
            f"{current.public_release_status}; physical={current.physical_status}; owner acceptance="
            f"{current.owner_acceptance_status}."
        ),
        "tracker": (
            f"Planning and GitHub tracker Open publication and acceptance issues: {open_issues}. "
            f"Acceptance gates are {current.acceptance_gates}; source completion does not close them."
        ),
        "source-records": (
            f"Source and records Remote main is {current.main}. PR #{current.pr_number} status is "
            f"{current.pr_status}; merge status is {current.merge_status}. Accepted source "
            f"{current.accepted_source} is contained by source merge {current.source_merge}. Published "
            f"records head {current.published_records_head}, parent {current.published_records_parent}, "
            f"has CI status {current.records_ci_status} and review status "
            f"{current.records_review_status}. Local records candidate "
            f"{current.rejected_records_candidate} has review status "
            f"{current.rejected_records_candidate_status}. {current.current_record} records one "
            f"direct-child correction with candidate status {current.record_candidate_status}; correction "
            f"self-SHA status is {current.correction_self_sha_status}. Product tree="
            f"{current.product_tree_status}; package tree={current.package_tree_status}; workflow="
            f"{current.workflow_status}; installers={current.installer_status}."
        ),
        "packages": (
            f"Packages Package build={current.package_build_status}; install="
            f"{current.package_install_status}; artifact promotion={current.artifact_promotion_status}; "
            f"signing={current.signing_status}; notarisation={current.notarisation_status}; deployment="
            f"{current.deployment_status}; public release={current.public_release_status}; physical="
            f"{current.physical_status}; owner acceptance={current.owner_acceptance_status}."
        ),
        "ci": (
            f"CI Exact-source run {current.ci_run} status is {current.ci_status} for subject "
            f"{current.ci_subject}. Records-head run {current.records_ci_run} status is "
            f"{current.records_ci_status} for {current.published_records_head}; records review status is "
            f"{current.records_review_status}. Failed run {current.failed_ci_run} remains red evidence. "
            f"Cancelled runs {cancelled_runs} are not evidence."
        ),
        "runtime": (
            f"Runtime Runtime restart status is {current.runtime_restart_status}; publication and both "
            f"records corrections leave saved checkout status {current.saved_checkout_status}."
        ),
        "physical-release-owner": (
            f"Physical, release, and owner Package install={current.package_install_status}; physical="
            f"{current.physical_status}; permissions={current.permissions_status}; signing="
            f"{current.signing_status}; notarisation={current.notarisation_status}; deployment="
            f"{current.deployment_status}; public release={current.public_release_status}; rollback "
            f"acceptance={current.rollback_acceptance_status}; owner acceptance="
            f"{current.owner_acceptance_status}. Acceptance gates are {current.acceptance_gates}."
        ),
        "verification": (
            f"Accepted source {current.accepted_source}, contained by source merge "
            f"{current.source_merge}, has exact-source run {current.ci_run} status "
            f"{current.ci_status}. Remote main {current.main} is published records head "
            f"{current.published_records_head}; records run {current.records_ci_run} status is "
            f"{current.records_ci_status} and review status is {current.records_review_status}. "
            f"{gate_statuses}. Acceptance gates are {current.acceptance_gates}."
        ),
        "issue-30": (
            f"#30 final parity and promotion Acceptance gates are {current.acceptance_gates}. Accepted "
            f"source {current.accepted_source} is contained by source merge {current.source_merge}; PR "
            f"#{current.pr_number} status is {current.pr_status} and merge status is "
            f"{current.merge_status}. Exact-source run {current.ci_run} status is {current.ci_status}; "
            f"artifact promotion status is {current.artifact_promotion_status}. Published records head "
            f"{current.published_records_head} has run {current.records_ci_run} status "
            f"{current.records_ci_status} and review status {current.records_review_status}. Local "
            f"correction status is {current.record_candidate_status}. Package install="
            f"{current.package_install_status}; physical={current.physical_status}; signing="
            f"{current.signing_status}; public release={current.public_release_status}; rollback "
            f"acceptance={current.rollback_acceptance_status}; owner acceptance="
            f"{current.owner_acceptance_status}."
        ),
        "published-integration": (
            f"Current published integration state Remote main is {current.main}, the published records "
            f"head {current.published_records_head}; its parent is source merge "
            f"{current.published_records_parent}, which equals source-merge authority "
            f"{current.source_merge} and contains accepted source {current.accepted_source}. PR "
            f"#{current.pr_number} status is {current.pr_status}; merge status is "
            f"{current.merge_status}; exact-source run {current.ci_run} status is {current.ci_status}; "
            f"records run {current.records_ci_run} status is {current.records_ci_status}; records review "
            f"status is {current.records_review_status}. Local child "
            f"{current.rejected_records_candidate} review status is "
            f"{current.rejected_records_candidate_status}; {current.current_record} documents its "
            f"direct-child correction with status {current.record_candidate_status}; correction self-SHA "
            f"status is {current.correction_self_sha_status}. "
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attribute_names = [name for name, _value in attrs]
        attributes = dict(attrs)
        is_authority = any(
            (name == "id" and value == AUTHORITY_ID)
            or name == "data-current-state-authority"
            for name, value in attrs
        )
        projection_key = attributes.get("data-current-state-projection")
        visible_attribute_text = [
            value
            for name, value in attrs
            if name in _HUMAN_VISIBLE_ATTRIBUTES and value is not None
        ]

        if (is_authority or projection_key is not None) and len(attribute_names) != len(
            set(attribute_names)
        ):
            kind = "current-state authority" if is_authority else "current-state projection"
            raise CurrentStatusContractError(f"{kind} has duplicate HTML attributes")

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
            self._projection_content.extend(visible_attribute_text)
            self._projection_depth += 1
        elif not is_authority:
            self._outside_projection_content.extend(visible_attribute_text)

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
        elif self._projection_key is not None:
            self._projection_content.append(data)
        else:
            self._outside_projection_content.append(data)

    def handle_endtag(self, tag: str) -> None:
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
    if fields["published_records_head"] != fields["main"]:
        raise CurrentStatusContractError("the published records head must be remote main")
    if fields["published_records_parent"] != fields["source_merge"]:
        raise CurrentStatusContractError("the published records parent must be the source merge")
    if fields["records_review_status"] == "rejected" and fields["record_candidate_status"] != "awaiting-review":
        raise CurrentStatusContractError("a rejected records head requires a fresh review candidate")
    allowed_values = {
        "ci_status": {"passed", "failed", "not-run", "cancelled"},
        "records_ci_status": {"passed", "failed", "not-run", "cancelled"},
        "review_status": {"accepted", "rejected"},
        "records_review_status": {"accepted", "rejected"},
        "record_candidate_status": {"awaiting-review", "accepted", "rejected"},
        "rejected_records_candidate_status": {"accepted", "rejected"},
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
        "correction_self_sha_status": {"omitted", "present"},
    }
    for field, allowed in allowed_values.items():
        if fields[field] not in allowed:
            raise CurrentStatusContractError(
                f"current-state field {field!r} has unsupported value {fields[field]!r}"
            )
    if fields["records_correction_parent"] != fields["rejected_records_candidate"]:
        raise CurrentStatusContractError(
            "the local records correction must directly follow the rejected records candidate"
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
    return current


def test_current_status_contract_accepts_the_canonical_authority() -> None:
    status_path = (
        Path(__file__).resolve().parents[2]
        / "Development Files"
        / "Core"
        / "STATUS.html"
    )

    current = parse_current_status(status_path.read_text(encoding="utf-8"))

    assert current.current_record == "Entry 96"


def test_every_present_state_row_agrees_with_the_canonical_authority() -> None:
    status_path = (
        Path(__file__).resolve().parents[2]
        / "Development Files"
        / "Core"
        / "STATUS.html"
    )
    status = status_path.read_text(encoding="utf-8")
    current = parse_current_status(status)
    parser = _AuthorityHTMLParser()
    parser.feed(status)
    parser.close()

    assert set(parser.projections) == set(_PROJECTION_TAGS)
    _validate_projections(parser.projections, current)
