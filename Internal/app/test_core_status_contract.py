from pathlib import Path
import re

import pytest

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


def test_current_status_rejects_two_current_main_identities() -> None:
    mutated = _duplicate_json_field(_status_html(), "main", "1111111111111111111111111111111111111111")

    with pytest.raises(CurrentStatusContractError, match="field 'main' appears more than once"):
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
            "Published records head <code>ce28abb4c665fc5216aecb083bdc8e5e30e8cc8f</code>",
            "Published records head <code>1111111111111111111111111111111111111111</code>",
        ),
        (
            "Accepted source <code>b6fe674f6332a5cfb20bf35568152fe22798f55e</code>",
            "Accepted source <code>c86e48f770083490e4621ef9770e654ab0d38b1e</code>",
        ),
        (
            "Acceptance gates are open. Accepted source <code>b6fe674f6332a5cfb20bf35568152fe22798f55e</code>",
            "Acceptance gates are open. Accepted local source <code>c86e48f770083490e4621ef9770e654ab0d38b1e</code>",
        ),
        (
            "Remote <code>main</code> is <code>ce28abb4c665fc5216aecb083bdc8e5e30e8cc8f</code>",
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

    with pytest.raises(CurrentStatusContractError, match="projection"):
        parse_current_status(mutated)


@pytest.mark.parametrize(
    ("anchor", "additive_conflict"),
    (
        (
            "Exact-source CI run <code>30730116960</code> status is passed.",
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
            "<code>30730292106</code> are not evidence.",
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

    with pytest.raises(CurrentStatusContractError, match="outside current-state projections"):
        parse_current_status(mutated)


def test_current_status_rejects_additive_visible_attribute_inside_projection() -> None:
    status_html = _status_html()
    anchor = "Exact-source CI run <code>30730116960</code> status is passed."
    contradiction = (
        '<input value="PR #49 remains open and unmerged; exact-source CI failed" />'
    )
    mutated = status_html.replace(anchor, anchor + contradiction, 1)

    with pytest.raises(CurrentStatusContractError, match="projection"):
        parse_current_status(mutated)


def test_current_status_rejects_additive_visible_attribute_outside_projections() -> None:
    status_html = _status_html()
    anchor = '<h2><span class="sec">4</span>Programme dependency board</h2>'
    contradiction = (
        '<img alt="PR #49 remains open and unmerged; exact-source CI failed" />'
    )
    mutated = status_html.replace(anchor, contradiction + anchor, 1)

    with pytest.raises(CurrentStatusContractError, match="outside current-state projections"):
        parse_current_status(mutated)


def test_current_status_rejects_duplicate_projection_attributes() -> None:
    mutated = _status_html().replace(
        'data-state-runtime-restart-status="not-run"',
        'data-state-runtime-restart-status="not-run" data-state-runtime-restart-status="passed"',
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

    with pytest.raises(CurrentStatusContractError, match="semantic attributes differ"):
        parse_current_status(mutated)


def test_current_status_rejects_unexpected_projection_attributes() -> None:
    mutated = _status_html().replace(
        'data-current-state-projection="runtime"',
        'data-current-state-projection="runtime" data-state-release-magic="completed"',
        1,
    )

    with pytest.raises(CurrentStatusContractError, match="semantic attributes differ"):
        parse_current_status(mutated)


def test_current_status_rejects_wrong_projection_identity() -> None:
    mutated = _status_html().replace(
        'id="current-state-runtime" data-current-state-projection="runtime"',
        'id="wrong-runtime" data-current-state-projection="runtime"',
        1,
    )

    with pytest.raises(CurrentStatusContractError, match="identity attributes differ"):
        parse_current_status(mutated)
