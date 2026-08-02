from pathlib import Path

import pytest

import verify_dependency_closure as verifier


def test_exact_installed_closure_passes(monkeypatch, tmp_path: Path):
    lock = tmp_path / "lock.txt"
    lock.write_text("Alpha_Pkg==1.2.3\nbeta-pkg==4.5.6\n", encoding="utf-8")
    monkeypatch.setattr(
        verifier, "installed_distributions",
        lambda: {"alpha-pkg": "1.2.3", "beta-pkg": "4.5.6", "pip": "99"},
    )
    verifier.verify(lock)


@pytest.mark.parametrize(
    "installed, expected_message",
    [
        ({"alpha-pkg": "1.2.3"}, "missing"),
        ({"alpha-pkg": "1.2.3", "beta-pkg": "4.5.6", "surprise": "1"}, "unexpected"),
        ({"alpha-pkg": "9", "beta-pkg": "4.5.6"}, "drift"),
    ],
)
def test_missing_unexpected_and_drifted_distributions_fail_closed(
    monkeypatch, tmp_path: Path, installed, expected_message,
):
    lock = tmp_path / "lock.txt"
    lock.write_text("alpha-pkg==1.2.3\nbeta-pkg==4.5.6\n", encoding="utf-8")
    monkeypatch.setattr(verifier, "installed_distributions", lambda: installed)
    with pytest.raises(RuntimeError, match=expected_message):
        verifier.verify(lock)
