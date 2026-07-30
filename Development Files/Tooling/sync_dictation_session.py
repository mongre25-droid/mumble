"""Synchronize the Windows durable-dictation authority into Linux.

The Windows runtime source is authoritative. The generated Linux copy always
uses LF newlines so checkout settings cannot create false drift. ``--check`` is
read-only and exits non-zero when the maintained copy is stale.
"""

from argparse import ArgumentParser
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "Internal" / "app" / "dictation_session.py"
TARGET = (ROOT / "Internal" / "app" / "Ports" / "Linux" / "app" /
          "dictation_session.py")


def normalized(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = not TARGET.exists() or normalized(TARGET) != normalized(SOURCE)
    if args.check:
        if stale:
            print(f"stale durable-dictation authority: {TARGET.relative_to(ROOT)}")
            return 1
        print("durable-dictation Linux package copy is synchronized")
        return 0
    TARGET.write_text(normalized(SOURCE), encoding="utf-8", newline="\n")
    print(f"synchronized {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
