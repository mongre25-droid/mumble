"""Synchronize the authoritative processing-route module into platform packages.

The Windows runtime source is authoritative.  Generated copies always use LF
newlines so checkout settings cannot create byte drift.  ``--check`` is
read-only and exits non-zero when either package copy is stale.
"""

from argparse import ArgumentParser
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    ROOT / "Internal" / "app" / "processing_route.py",
    ROOT / "Internal" / "app" / "model_authority.py",
    ROOT / "Internal" / "app" / "model_provenance.py",
    ROOT / "Internal" / "app" / "verify_dependency_closure.py",
)
PORTS = ("macOS", "Linux")


def normalized(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    pairs = [
        (source, ROOT / "Internal" / "app" / "Ports" / port / "app" / source.name)
        for source in SOURCES for port in PORTS
    ]
    stale = [
        target for source, target in pairs
        if not target.exists() or normalized(target) != normalized(source)
    ]
    if args.check:
        if stale:
            for target in stale:
                print(f"stale generated route authority: {target.relative_to(ROOT)}")
            return 1
        print("processing-route and model-authority package copies are synchronized")
        return 0
    for source, target in pairs:
        target.write_text(normalized(source), encoding="utf-8", newline="\n")
        print(f"synchronized {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
