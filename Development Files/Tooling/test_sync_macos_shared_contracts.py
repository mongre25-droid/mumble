import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("sync_macos_shared_contracts.py")
SPEC = importlib.util.spec_from_file_location("sync_macos_shared_contracts", SCRIPT)
SYNC = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SYNC)


class SharedContractSynchronizationTests(unittest.TestCase):
    def test_identical_crlf_and_lf_content_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_bytes(b"first line\r\nsecond line\r\n")
            target.write_bytes(b"first line\nsecond line\n")

            self.assertTrue(SYNC.contents_match(source, target))

    def test_genuine_content_drift_does_not_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_bytes(b"first line\r\nexpected content\r\n")
            target.write_bytes(b"first line\nchanged content\n")

            self.assertFalse(SYNC.contents_match(source, target))


if __name__ == "__main__":
    unittest.main()
