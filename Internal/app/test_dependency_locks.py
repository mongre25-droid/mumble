"""Repository dependency-lock consistency required by the CI contract."""

from pathlib import Path


APP = Path(__file__).parent
RUNTIME_REQUIREMENTS = [
    APP / "requirements.txt",
    APP / "Ports" / "macOS" / "app" / "requirements.txt",
    APP / "Ports" / "Linux" / "app" / "requirements.txt",
]
DEV_REQUIREMENTS = [
    APP / "requirements-dev.txt",
    APP / "Ports" / "macOS" / "app" / "requirements-dev.txt",
    APP / "Ports" / "Linux" / "app" / "requirements-dev.txt",
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


def test_process_measurement_dependency_is_consistent_across_test_platforms():
    pins = [_pin(path, "psutil") for path in DEV_REQUIREMENTS]

    assert pins == ["7.2.2", "7.2.2", "7.2.2"]


def test_installers_enforce_complete_hashed_runtime_profiles():
    installers = [
        APP / "install.ps1",
        APP / "Ports" / "macOS" / "Install Mumble.command",
        APP / "Ports" / "Linux" / "install.sh",
    ]
    assert all("--require-hashes" in path.read_text(encoding="utf-8") for path in installers)

    locks = [APP / "requirements-lock-win-x86_64-cp313.txt"]
    locks += list((APP / "Ports" / "macOS" / "app").glob("requirements-lock-macos-*.txt"))
    locks += [APP / "Ports" / "Linux" / "app" / "requirements-lock-linux-x86_64-cp313.txt"]
    assert len(locks) == 6
    for path in locks:
        text = path.read_text(encoding="utf-8")
        assert "==" in text
        assert "--hash=sha256:" in text
