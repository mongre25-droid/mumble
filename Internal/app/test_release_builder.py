"""Focused regression for the canonical Windows/website release boundary."""

import hashlib
import importlib.util
import zipfile
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


@pytest.mark.parametrize(
    "archive_path",
    (
        REPO_ROOT / "Internal" / "Releases" / "Mumble.zip",
        REPO_ROOT / "Development Files" / "Marketing" / "Website" /
        "public" / "Mumble.zip",
    ),
)
def test_tracked_release_matches_canonical_runtime_membership_and_content(
        archive_path):
    builder = _load_builder()
    expected = {
        archive_name: builder.packaged_bytes(source)
        for source, archive_name in builder.release_sources(REPO_ROOT)
    }

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert archive.testzip() is None
        assert len(names) == len(set(names))
        assert set(names) == set(expected)
        assert all(
            archive.read(name) == expected[name]
            for name in names
        )


def test_sql_checkout_line_endings_produce_identical_release_bytes(tmp_path):
    builder = _load_builder()
    sql_archive_name = "Mumble/Internal/app/cloud_schema.sql"
    source_sql = (REPO_ROOT / "Internal" / "app" / "cloud_schema.sql").read_bytes()
    lf_sql = source_sql.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    sql_sources = (tmp_path / "lf.sql", tmp_path / "crlf.sql")
    sql_sources[0].write_bytes(lf_sql)
    sql_sources[1].write_bytes(lf_sql.replace(b"\n", b"\r\n"))

    archive_bytes = []
    for index, sql_source in enumerate(sql_sources):
        archive_path = tmp_path / f"candidate-{index}.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for source, archive_name in builder.release_sources(REPO_ROOT):
                if archive_name == sql_archive_name:
                    source = sql_source
                builder.write_file(archive, source, archive_name)
        with zipfile.ZipFile(archive_path) as archive:
            assert len(archive.namelist()) == 136
            assert archive.read(sql_archive_name) == lf_sql
        archive_bytes.append(archive_path.read_bytes())

    assert hashlib.sha256(archive_bytes[0]).digest() == hashlib.sha256(
        archive_bytes[1]
    ).digest()
    assert archive_bytes[0] == archive_bytes[1]
