"""Fail closed unless the installed runtime exactly matches a reviewed lock."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re
import sys
import sysconfig


BOOTSTRAP_DISTRIBUTIONS = {"pip", "wheel"}
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def expected_distributions(lock_path: Path) -> dict[str, str]:
    expected = {}
    for number, raw in enumerate(lock_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash=sha256:"):
            if re.fullmatch(r"--hash=sha256:[0-9a-f]{64}(?:\s+\\)?", line) is None:
                raise ValueError(f"invalid dependency artifact hash at line {number}")
            continue
        line = line.removesuffix("\\").rstrip()
        match = PIN.fullmatch(line)
        if match is None:
            raise ValueError(f"non-exact dependency lock entry at line {number}")
        name, version = match.groups()
        key = canonical_name(name)
        if key in expected:
            raise ValueError(f"duplicate dependency lock entry: {name}")
        expected[key] = version
    if not expected:
        raise ValueError("dependency lock is empty")
    return expected


def installed_distributions() -> dict[str, str]:
    paths = sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")})
    return {
        canonical_name(distribution.metadata["Name"]): distribution.version
        for distribution in metadata.distributions(path=paths)
        if distribution.metadata.get("Name")
    }


def verify(lock_path: Path) -> None:
    expected = expected_distributions(lock_path)
    installed = installed_distributions()
    runtime = {
        name: version for name, version in installed.items()
        if name not in BOOTSTRAP_DISTRIBUTIONS
    }
    missing = sorted(set(expected) - set(runtime))
    unexpected = sorted(set(runtime) - set(expected))
    drift = sorted(
        f"{name}: expected {expected[name]}, installed {runtime[name]}"
        for name in set(expected) & set(runtime) if expected[name] != runtime[name]
    )
    if missing or unexpected or drift:
        raise RuntimeError(
            "dependency closure mismatch; "
            f"missing={missing}; unexpected={unexpected}; drift={drift}"
        )


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: verify_dependency_closure.py LOCK_FILE", file=sys.stderr)
        return 2
    try:
        verify(Path(arguments[0]).resolve(strict=True))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Dependency verification failed: {exc}", file=sys.stderr)
        return 1
    print("Dependency closure verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
