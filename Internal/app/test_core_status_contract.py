from pathlib import Path
import re

import pytest

import test_core_status_support as status_support
from test_core_status_support import CurrentStatusContractError, parse_current_status


ROOT = Path(__file__).resolve().parents[2]
STATUS_PATH = ROOT / "Development Files" / "Core" / "STATUS.html"


def _status_html() -> str:
    return STATUS_PATH.read_text(encoding="utf-8")


def _duplicate_json_field(status_html: str, field: str, conflicting_value: str) -> str:
    pattern = rf'(\s+"{re.escape(field)}"\s*:\s*"[^"]+",)'
    mutated = re.sub(
        pattern,
        rf'\1\n    "{field}": "{conflicting_value}",',
        status_html,
        count=1,
    )
    assert mutated != status_html
    return mutated


def _projection_element(status_html: str, key: str) -> str:
    match = re.search(
        rf'<(?P<tag>\w+)[^>]*data-current-state-projection="{re.escape(key)}"[^>]*>'
        rf'.*?</(?P=tag)>',
        status_html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def test_current_status_rejects_two_containing_commit_identities() -> None:
    mutated = _duplicate_json_field(
        _status_html(), "containing_commit", "1111111111111111111111111111111111111111"
    )

    with pytest.raises(
        CurrentStatusContractError,
        match="field 'containing_commit' appears more than once",
    ):
        parse_current_status(mutated)


def test_current_status_rejects_failed_and_passed_ci_simultaneously() -> None:
    mutated = _duplicate_json_field(_status_html(), "ci_status", "passed")

    with pytest.raises(CurrentStatusContractError, match="field 'ci_status' appears more than once"):
        parse_current_status(mutated)


def test_current_status_rejects_unmerged_and_merged_promotion_simultaneously() -> None:
    mutated = _duplicate_json_field(_status_html(), "merge_status", "merged")
    mutated = _duplicate_json_field(mutated, "promotion_status", "promoted")

    with pytest.raises(CurrentStatusContractError, match="field 'merge_status' appears more than once"):
        parse_current_status(mutated)


def test_current_status_rejects_duplicate_candidate_and_status_fields() -> None:
    mutated = _duplicate_json_field(
        _status_html(), "correction_parent", "2222222222222222222222222222222222222222"
    )
    mutated = _duplicate_json_field(mutated, "review_status", "accepted")

    with pytest.raises(CurrentStatusContractError, match="field 'correction_parent' appears more than once"):
        parse_current_status(mutated)


def test_current_status_rejects_a_second_current_authority() -> None:
    status_html = _status_html()
    authority = re.search(
        r'<script id="mumble-current-state".*?</script>', status_html, flags=re.DOTALL
    )
    assert authority is not None
    mutated = status_html.replace(authority.group(0), authority.group(0) * 2, 1)

    with pytest.raises(CurrentStatusContractError, match="exactly one current-state authority"):
        parse_current_status(mutated)


@pytest.mark.parametrize(
    ("current_fragment", "contradictory_fragment"),
    (
        (
            "Predecessor published records head ce28abb4c665fc5216aecb083bdc8e5e30e8cc8f",
            "Predecessor published records head 1111111111111111111111111111111111111111",
        ),
        (
            "Accepted source b6fe674f6332a5cfb20bf35568152fe22798f55e",
            "Accepted source c86e48f770083490e4621ef9770e654ab0d38b1e",
        ),
        (
            "Acceptance gates are open. Accepted source b6fe674f6332a5cfb20bf35568152fe22798f55e",
            "Acceptance gates are open. Accepted local source c86e48f770083490e4621ef9770e654ab0d38b1e",
        ),
        (
            "Symbolic containing commit @self resolves to the Git commit containing this STATUS file",
            "Unchanged main 2f000b43; no push, PR, merge",
        ),
    ),
)
def test_current_status_rejects_each_contradictory_prose_authority(
    current_fragment: str, contradictory_fragment: str
) -> None:
    status_html = _status_html()
    assert current_fragment in status_html
    mutated = status_html.replace(current_fragment, contradictory_fragment, 1)

    with pytest.raises(CurrentStatusContractError, match="not allowed|projection"):
        parse_current_status(mutated)


@pytest.mark.parametrize(
    ("anchor", "additive_conflict"),
    (
        (
            "Exact-source CI run 30730116960 status is passed;",
            " PR #49 remains open and unmerged.",
        ),
        (
            "Package install=not-run; signing=not-run; notarisation=not-run; artifact promotion=not-run; deployment=not-run; public release=not-run; physical=not-run; owner acceptance=not-run.",
            " The package was owner-installed, signed, publicly released, and physically accepted.",
        ),
        (
            "Product tree=unchanged; package tree=unchanged; workflow=unchanged; installers=unchanged.",
            " Nothing has been merged.",
        ),
        (
            "30730292106 are not evidence.",
            " Replacement CI has not run.",
        ),
        (
            "Acceptance gates are open.</p>",
            " Exact-source CI failed.",
        ),
        (
            "artifact promotion status is not-run.",
            " Artifact promotion completed.",
        ),
        (
            "package install=not-run; runtime restart=not-run; physical=not-run; permissions=not-run; signing=not-run; notarisation=not-run; artifact promotion=not-run; deployment=not-run; public release=not-run; rollback acceptance=not-run; owner acceptance=not-run.</td></tr>",
            " PR #49 remains open and unmerged.",
        ),
    ),
)
def test_current_status_rejects_each_additive_projection_conflict(
    anchor: str, additive_conflict: str
) -> None:
    status_html = _status_html()
    assert status_html.count(anchor) == 1
    mutated = status_html.replace(anchor, anchor + additive_conflict, 1)

    with pytest.raises(CurrentStatusContractError, match="projection"):
        parse_current_status(mutated)


def test_current_status_rejects_a_missing_projection() -> None:
    status_html = _status_html()
    projection = _projection_element(status_html, "runtime")
    mutated = status_html.replace(projection, "", 1)

    with pytest.raises(CurrentStatusContractError, match="projection set differs.*runtime"):
        parse_current_status(mutated)


def test_current_status_rejects_a_duplicate_projection() -> None:
    status_html = _status_html()
    projection = _projection_element(status_html, "runtime")
    mutated = status_html.replace(projection, projection * 2, 1)

    with pytest.raises(CurrentStatusContractError, match="appears more than once"):
        parse_current_status(mutated)


def test_current_status_rejects_an_unexpected_projection() -> None:
    mutated = _status_html().replace(
        'data-current-state-projection="runtime"',
        'data-current-state-projection="unexpected-runtime"',
        1,
    )

    with pytest.raises(CurrentStatusContractError, match="projection set differs.*unexpected"):
        parse_current_status(mutated)


def test_current_status_rejects_unmarked_human_visible_current_claims() -> None:
    status_html = _status_html()
    anchor = '<h2><span class="sec">4</span>Programme dependency board</h2>'
    contradictory_paragraph = (
        "<p>Current publication truth: PR #49 remains open and unmerged; exact-source CI "
        "failed; artifacts were publicly released.</p>"
    )
    mutated = status_html.replace(anchor, contradictory_paragraph + anchor, 1)

    with pytest.raises(
        CurrentStatusContractError,
        match="not allowed|outside current-state projections",
    ):
        parse_current_status(mutated)


def test_current_status_rejects_additive_visible_attribute_inside_projection() -> None:
    status_html = _status_html()
    anchor = "Exact-source CI run 30730116960 status is passed;"
    contradiction = (
        '<input value="PR #49 remains open and unmerged; exact-source CI failed" />'
    )
    mutated = status_html.replace(anchor, anchor + contradiction, 1)

    with pytest.raises(CurrentStatusContractError, match="not allowed|projection"):
        parse_current_status(mutated)


def test_current_status_rejects_additive_visible_attribute_outside_projections() -> None:
    status_html = _status_html()
    anchor = '<h2><span class="sec">4</span>Programme dependency board</h2>'
    contradiction = (
        '<img alt="PR #49 remains open and unmerged; exact-source CI failed" />'
    )
    mutated = status_html.replace(anchor, contradiction + anchor, 1)

    with pytest.raises(
        CurrentStatusContractError,
        match="not allowed|outside current-state projections",
    ):
        parse_current_status(mutated)


@pytest.mark.parametrize(
    "attack",
    (
        '<span aria-valuetext="PR #49 is open; exact-source CI failed"></span>',
        '<span aria-roledescription="PR #49 is open; exact-source CI failed"></span>',
    ),
)
def test_current_status_rejects_reported_accessibility_attacks_inside_projection(
    attack: str,
) -> None:
    status_html = _status_html()
    anchor = "Exact-source CI run 30730116960 status is passed;"
    mutated = status_html.replace(anchor, anchor + attack, 1)

    with pytest.raises(CurrentStatusContractError):
        parse_current_status(mutated)


def test_current_status_rejects_reported_aria_valuetext_attack_outside_projections() -> None:
    status_html = _status_html()
    anchor = '<h2><span class="sec">4</span>Programme dependency board</h2>'
    attack = '<span aria-valuetext="PR #49 is open; exact-source CI failed"></span>'
    mutated = status_html.replace(anchor, attack + anchor, 1)

    with pytest.raises(CurrentStatusContractError):
        parse_current_status(mutated)


def test_current_status_rejects_reported_iframe_srcdoc_attack_outside_projections() -> None:
    status_html = _status_html()
    anchor = '<h2><span class="sec">4</span>Programme dependency board</h2>'
    attack = '<iframe srcdoc="PR #49 is open; exact-source CI failed; release completed"></iframe>'
    mutated = status_html.replace(anchor, attack + anchor, 1)

    with pytest.raises(CurrentStatusContractError):
        parse_current_status(mutated)


def test_current_status_rejects_duplicate_projection_attributes() -> None:
    mutated = _status_html().replace(
        'data-state-runtime-restart-status="not-run"',
        'data-state-runtime-restart-status="not-run" data-state-runtime-restart-status="passed"',
        1,
    )

    with pytest.raises(CurrentStatusContractError, match="duplicate HTML attributes"):
        parse_current_status(mutated)


def test_current_status_rejects_duplicate_attributes_on_nested_elements() -> None:
    mutated = _status_html().replace(
        '<div class="tag">Current truth</div>',
        '<div class="tag" class="other">Current truth</div>',
        1,
    )

    with pytest.raises(CurrentStatusContractError, match="duplicate HTML attributes"):
        parse_current_status(mutated)


def test_current_status_rejects_duplicate_attributes_outside_projections() -> None:
    mutated = _status_html().replace(
        '<span class="sec">4</span>',
        '<span class="sec" class="other">4</span>',
        1,
    )

    with pytest.raises(CurrentStatusContractError, match="duplicate HTML attributes"):
        parse_current_status(mutated)


def test_current_status_rejects_missing_projection_attributes() -> None:
    mutated = _status_html().replace(
        ' data-state-runtime-restart-status="not-run"',
        "",
        1,
    )

    with pytest.raises(
        CurrentStatusContractError,
        match="unexpected attributes|semantic attributes differ",
    ):
        parse_current_status(mutated)


def test_current_status_rejects_unexpected_projection_attributes() -> None:
    mutated = _status_html().replace(
        'data-current-state-projection="runtime"',
        'data-current-state-projection="runtime" data-state-release-magic="completed"',
        1,
    )

    with pytest.raises(
        CurrentStatusContractError,
        match="unexpected attributes|semantic attributes differ",
    ):
        parse_current_status(mutated)


def test_current_status_rejects_wrong_projection_identity() -> None:
    mutated = _status_html().replace(
        'id="current-state-runtime" data-current-state-projection="runtime"',
        'id="wrong-runtime" data-current-state-projection="runtime"',
        1,
    )

    with pytest.raises(CurrentStatusContractError, match="identity attributes differ"):
        parse_current_status(mutated)


@pytest.mark.parametrize(
    "attack",
    (
        '<span aria-hidden="false"></span>',
        '<span data-current-claim="PR #49 is open"></span>',
    ),
)
def test_current_status_rejects_unapproved_aria_and_data_attributes(attack: str) -> None:
    status_html = _status_html()
    anchor = '<h2><span class="sec">4</span>Programme dependency board</h2>'
    mutated = status_html.replace(anchor, attack + anchor, 1)

    with pytest.raises(CurrentStatusContractError):
        parse_current_status(mutated)


@pytest.mark.parametrize(
    "attack",
    (
        "<section></section>",
        '<code style="display:block"></code>',
    ),
)
def test_current_status_rejects_unexpected_safe_looking_markup(attack: str) -> None:
    status_html = _status_html()
    anchor = '<h2><span class="sec">4</span>Programme dependency board</h2>'
    mutated = status_html.replace(anchor, attack + anchor, 1)

    with pytest.raises(CurrentStatusContractError):
        parse_current_status(mutated)


def test_current_status_rejects_an_added_allowed_attribute_value() -> None:
    status_html = _status_html()
    anchor = '<h2><span class="sec">4</span>Programme dependency board</h2>'
    mutated = status_html.replace(anchor, '<span class="gold"></span>' + anchor, 1)

    with pytest.raises(CurrentStatusContractError, match="document structure"):
        parse_current_status(mutated)


def test_current_status_rejects_a_changed_allowed_link_destination() -> None:
    status_html = _status_html()
    current = "https://github.com/mongre25-droid/mumble/issues/15"
    mutated = status_html.replace(
        current,
        "https://github.com/mongre25-droid/mumble/issues/999",
        1,
    )
    assert mutated != status_html

    with pytest.raises(CurrentStatusContractError, match="document structure"):
        parse_current_status(mutated)


@pytest.mark.parametrize(
    "attack",
    (
        '<iframe srcdoc="PR #49 is open"></iframe>',
        '<object data="PR #49 is open"></object>',
        '<embed src="PR #49 is open">',
        '<template data-claim="PR #49 is open"></template>',
        '<script src="unexpected.js"></script>',
    ),
)
def test_current_status_rejects_embedded_or_executable_channels(attack: str) -> None:
    status_html = _status_html()
    anchor = '<h2><span class="sec">4</span>Programme dependency board</h2>'
    mutated = status_html.replace(anchor, attack + anchor, 1)

    with pytest.raises(CurrentStatusContractError):
        parse_current_status(mutated)


def test_current_status_accepts_the_sole_canonical_authority_script() -> None:
    current = parse_current_status(_status_html())

    assert current.schema == "mumble.current-state.v1"


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
def test_current_status_rejects_marked_and_unknown_declarations(attack: str) -> None:
    status_html = _status_html()
    anchor = '<h2><span class="sec">4</span>Programme dependency board</h2>'
    mutated = status_html.replace(anchor, attack + anchor, 1)

    with pytest.raises(CurrentStatusContractError, match="marked or unknown declaration"):
        parse_current_status(mutated)


@pytest.mark.parametrize("attack", ("<![x", "<![]]>"))
def test_current_status_normalizes_malformed_declaration_assertions(attack: str) -> None:
    status_html = _status_html()
    anchor = '<h2><span class="sec">4</span>Programme dependency board</h2>'
    mutated = status_html.replace(anchor, attack + anchor, 1)

    with pytest.raises(CurrentStatusContractError, match="malformed HTML declaration"):
        parse_current_status(mutated)


def test_current_status_does_not_normalize_unrelated_programming_assertions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_with_unrelated_assertion(_parser: object, _status_html: str) -> None:
        raise AssertionError("unrelated programming error")

    monkeypatch.setattr(
        status_support._AuthorityHTMLParser,
        "feed",
        fail_with_unrelated_assertion,
    )

    with pytest.raises(AssertionError, match="unrelated programming error") as caught:
        status_support.parse_current_status(_status_html())

    assert type(caught.value) is AssertionError


def test_current_status_uses_stable_symbolic_publication_semantics() -> None:
    current = parse_current_status(_status_html())

    assert current.containing_commit == "@self"
    assert current.authority_scope == "valid-when-read-from-refs/heads/main"
    assert current.target_ref == "refs/heads/main"
    assert current.review_subject == "704921eb61014ac4d5b0f01a0399defab3028882"
    assert current.review_status == "accepted"
    assert current.review_task == "019fc013-3ab9-7bc0-9c39-f63385bb8359"
    assert current.rejected_records_candidates == (
        "bf778c698064023bd3fe8e33abb8a1093c1dd8a0,"
        "21c21567029b1232e07ba85ca4d196820f3cfed9,"
        "7a2e239d6c152413b9404844b681634436c79061"
    )
    assert current.predecessor_published_records_head == (
        "ce28abb4c665fc5216aecb083bdc8e5e30e8cc8f"
    )
    assert current.predecessor_records_ci_run == "30730441394"
    assert current.predecessor_records_ci_status == "passed"
    assert current.predecessor_records_review_status == "rejected"
    assert current.publication_action_at_commit == "not-yet-pushed"
    assert current.final_receipt_ci_at_commit == "not-run"
    assert current.final_receipt_ci_live_authority == "github-checks-for-@self"
    assert current.current_record == "Entry 99"
