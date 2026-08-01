#!/usr/bin/env python3
"""Public-boundary tests for deterministic macOS release candidates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import build_release


class MacOSReleaseBuilderTests(unittest.TestCase):
    def test_builds_distinct_deterministic_supported_architecture_candidates(self):
        with (
            tempfile.TemporaryDirectory() as first_raw,
            tempfile.TemporaryDirectory() as second_raw,
        ):
            first = Path(first_raw)
            second = Path(second_raw)
            first_outputs = build_release.build(first, ("arm64", "x86_64"))
            second_outputs = build_release.build(second, ("arm64", "x86_64"))

            expected = {
                "Mumble-v0.95-macos-arm64.zip",
                "Mumble-v0.95-macos-arm64.zip.sha256",
                "Mumble-v0.95-macos-x86_64.zip",
                "Mumble-v0.95-macos-x86_64.zip.sha256",
            }
            self.assertEqual({path.name for path in first_outputs}, expected)
            for name in expected:
                self.assertEqual(
                    hashlib.sha256((first / name).read_bytes()).digest(),
                    hashlib.sha256((second / name).read_bytes()).digest(),
                )

            for architecture in ("arm64", "x86_64"):
                candidate = first / f"Mumble-v0.95-macos-{architecture}.zip"
                with zipfile.ZipFile(candidate) as archive:
                    names = archive.namelist()
                    root = "MacMumble"
                    required = {
                        f"{root}/LICENSE",
                        f"{root}/THIRD_PARTY_NOTICES.md",
                        f"{root}/RELEASE-INVENTORY.json",
                        f"{root}/DEPENDENCY-CLOSURE.json",
                        f"{root}/RELEASE-PROVENANCE.json",
                        f"{root}/app/cloud_schema.sql",
                        f"{root}/app/model_provenance.py",
                        f"{root}/app/requirements.txt",
                    }
                    self.assertTrue(required.issubset(names))
                    provenance = json.loads(
                        archive.read(f"{root}/RELEASE-PROVENANCE.json"))
                    self.assertEqual(provenance["package"]["platform"], "macos")
                    self.assertEqual(
                        provenance["package"]["architecture"], architecture)
                    closure = provenance["dependency_closure"]["transitive_inventory"]
                    profiles = closure["profiles"]
                    self.assertEqual(
                        [profile["name"] for profile in profiles],
                        [f"macos-{architecture}-cp312", f"macos-{architecture}-cp313"],
                    )
                    artifact_names = [
                        row["filename"]
                        for profile in profiles
                        for rows in profile["artifacts"].values() for row in rows
                    ]
                    platform_wheels = [
                        name for name in artifact_names
                        if name.endswith(".whl") and "-none-any.whl" not in name
                    ]
                    self.assertTrue(platform_wheels)
                    self.assertTrue(all("macosx_" in name for name in platform_wheels))
                    self.assertTrue(all(
                        architecture in name or "universal2" in name
                        for name in platform_wheels
                    ))

    def test_rejects_unsupported_architecture(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(ValueError):
                build_release.build(Path(raw), ("universal2",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
