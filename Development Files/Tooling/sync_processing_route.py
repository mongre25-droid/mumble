"""Synchronize the authoritative processing-route module into platform packages.

The Windows runtime source is authoritative.  Generated copies always use LF
newlines so checkout settings cannot create byte drift.  ``--check`` is
read-only and exits non-zero when either package copy is stale.
"""

from argparse import ArgumentParser
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "Internal" / "app" / "processing_route.py"
TARGETS = (
    ROOT / "Internal" / "app" / "Ports" / "macOS" / "app" /
    "processing_route.py",
    ROOT / "Internal" / "app" / "Ports" / "Linux" / "app" /
    "processing_route.py",
)


def normalized(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = normalized(SOURCE)
    stale = [target for target in TARGETS if not target.exists() or normalized(target) != source]
    if args.check:
        if stale:
            for target in stale:
                print(f"stale generated processing route: {target.relative_to(ROOT)}")
            return 1
        print("processing-route package copies are synchronized")
        return 0
    for target in TARGETS:
        target.write_text(source, encoding="utf-8", newline="\n")
        print(f"synchronized {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
