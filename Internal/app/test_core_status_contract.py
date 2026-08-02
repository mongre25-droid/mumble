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
