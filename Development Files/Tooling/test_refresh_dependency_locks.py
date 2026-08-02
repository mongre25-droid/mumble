#!/usr/bin/env python3
"""Regression tests for exact wheel-tag profile selection."""

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).with_name("refresh_dependency_locks.py")
SPEC = importlib.util.spec_from_file_location("refresh_dependency_locks", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
locks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(locks)


class WheelCompatibilityTests(unittest.TestCase):
    def _accepted(self, profile, filename):
        key = ("example", "1.0")
        previous = locks.RELEASE_FILES.get(key)
        locks.RELEASE_FILES[key] = [{"filename": filename, "sha256": "0" * 64}]
        try:
            try:
                locks.compatible_files(profile, "example", "1.0")
            except RuntimeError:
                return False
            return True
        finally:
            if previous is None:
                del locks.RELEASE_FILES[key]
            else:
                locks.RELEASE_FILES[key] = previous

    def test_interpreter_and_abi_tags_are_exact(self):
        cases = (
            ("windows-x86_64-cp313", "example-1.0-cp313-cp313-win_amd64.whl", True),
            ("windows-x86_64-cp313", "example-1.0-cp313-cp313t-win_amd64.whl", False),
            ("windows-x86_64-cp313t", "example-1.0-cp313-cp313t-win_amd64.whl", True),
            ("windows-x86_64-cp313t", "example-1.0-cp313-cp313-win_amd64.whl", False),
            ("macos-arm64-cp312", "example-1.0-cp312-cp312-macosx_11_0_arm64.whl", True),
            ("macos-arm64-cp312", "example-1.0-cp313-cp313-macosx_11_0_arm64.whl", False),
            ("macos-arm64-cp312", "example-1.0-cp313-cp313t-macosx_11_0_arm64.whl", False),
            ("linux-x86_64-cp313", "example-1.0-cp37-abi3-manylinux_2_17_x86_64.whl", True),
        )
        for profile, filename, expected in cases:
            with self.subTest(profile=profile, filename=filename):
                self.assertEqual(self._accepted(profile, filename), expected)

    def test_platform_and_architecture_tags_are_isolated(self):
        cases = (
            ("windows-x86_64-cp313", "example-1.0-cp313-cp313-win_amd64.whl", True),
            ("linux-x86_64-cp313", "example-1.0-cp313-cp313-win_amd64.whl", False),
            ("macos-x86_64-cp313", "example-1.0-cp313-cp313-win_amd64.whl", False),
            ("linux-x86_64-cp313", "example-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", True),
            ("windows-x86_64-cp313", "example-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", False),
            ("macos-arm64-cp313", "example-1.0-cp313-cp313-macosx_11_0_arm64.whl", True),
            ("macos-x86_64-cp313", "example-1.0-cp313-cp313-macosx_11_0_arm64.whl", False),
            ("macos-x86_64-cp313", "example-1.0-cp313-cp313-macosx_11_0_x86_64.whl", True),
            ("macos-arm64-cp313", "example-1.0-cp313-cp313-macosx_11_0_x86_64.whl", False),
            ("macos-arm64-cp313", "example-1.0-cp313-cp313-macosx_11_0_universal2.whl", True),
            ("macos-x86_64-cp313", "example-1.0-cp313-cp313-macosx_11_0_universal2.whl", True),
        )
        for profile, filename, expected in cases:
            with self.subTest(profile=profile, filename=filename):
                self.assertEqual(self._accepted(profile, filename), expected)

    def test_universal_wheels_apply_only_to_matching_python(self):
        cases = (
            ("windows-x86_64-cp313", "example-1.0-py3-none-any.whl", True),
            ("macos-arm64-cp312", "example-1.0-py3-none-any.whl", True),
            ("linux-x86_64-cp313", "example-1.0-py2-none-any.whl", False),
            ("linux-x86_64-cp313", "example-1.0-cp313-none-any.whl", True),
            ("macos-arm64-cp312", "example-1.0-cp313-none-any.whl", False),
            ("windows-x86_64-cp313t", "example-1.0-cp313-none-any.whl", True),
        )
        for profile, filename, expected in cases:
            with self.subTest(profile=profile, filename=filename):
                self.assertEqual(self._accepted(profile, filename), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
