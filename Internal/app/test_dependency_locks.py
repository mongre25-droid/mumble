"""Repository dependency-lock consistency required by the CI contract."""

from pathlib import Path


APP = Path(__file__).parent
RUNTIME_REQUIREMENTS = [
    APP / "requirements.txt",
    APP / "Ports" / "macOS" / "app" / "requirements.txt",
    APP / "Ports" / "Linux" / "app" / "requirements.txt",
]


def _pin(path, package):
    prefix = package.casefold() + "=="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.casefold().startswith(prefix):
            return line.split("==", 1)[1]
    raise AssertionError(f"{package} is not pinned in {path}")


def test_pillow_security_pin_is_consistent_across_platforms():
    pins = [_pin(path, "Pillow") for path in RUNTIME_REQUIREMENTS]

    assert pins == ["12.3.0", "12.3.0", "12.3.0"]
