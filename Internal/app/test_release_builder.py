"""Focused regression for the canonical Windows/website release boundary."""

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parents[2]
BUILDER_PATH = (
    REPO_ROOT / "Development Files" / "Tooling" / "_rebuild_zip.py"
)


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "mumble_release_builder_regression", BUILDER_PATH
    )
    builder = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(builder)
    return builder


def test_website_release_sync_uses_required_maintained_destination(tmp_path):
    builder = _load_builder()
    canonical = tmp_path / "Internal" / "Releases" / "Mumble.zip"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"canonical release bytes")

    expected = (
        tmp_path / "Development Files" / "Marketing" / "Website" /
        "public" / "Mumble.zip"
    )
    assert builder.website_archive_path(tmp_path) == expected

    with pytest.raises(FileNotFoundError, match="Marketing.*Website.*public"):
        builder.sync_website_archive(tmp_path, canonical)
    assert not (tmp_path / "Development Files" / "Other" / "website").exists()

    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"stale website bytes")
    result = builder.sync_website_archive(tmp_path, canonical)

    assert result == expected
    assert expected.read_bytes() == canonical.read_bytes()
