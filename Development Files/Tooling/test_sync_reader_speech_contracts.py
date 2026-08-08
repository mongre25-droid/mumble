import importlib.util
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("sync_reader_speech_contracts.py")
ROOT = Path(__file__).resolve().parents[2]
HANDBOOK = ROOT / "Development Files" / "Core" / "HANDBOOK.html"
TOOLING_README = ROOT / "Development Files" / "Tooling" / "README.md"

FIXTURE_BLOCKS = (
    (
        Path("webui/app.js"),
        "async function confirmReaderCloudUse(kind) {",
        "function readerBuildPane(text) {",
    ),
    (
        Path("webui_shell.py"),
        "    def reader_tts(self, text, model=None, voice=None, provider=None):",
        '        pid = provider or self.settings.get("reader_tts_provider", "openrouter")',
    ),
    (
        Path("ai/tts_providers.py"),
        '\"\"\"TTS (text-to-speech) provider abstraction for Mumble.',
        "\n\nimport io",
    ),
    (
        Path("test_tts_providers.py"),
        '\"\"\"Tests for ai/tts_providers.py',
        "\n\nimport os",
    ),
    (
        Path("ai/__init__.py"),
        "            ordered = [model or route_decision.model]",
        "                attempts.append((pid, m, voice_id if primary else None, primary))",
    ),
    (
        Path("test_reader.py"),
        "# Monkey-patch get_tts_provider so we control the fallback order.",
        "    audio, ctype, meta = ai.synthesize_with_fallback(",
    ),
)
FIXTURE_PORTS = (
    Path("Internal/app/Ports/macOS/app"),
    Path("Internal/app/Ports/Linux/app"),
)


def _contract_text(start_marker, end_marker, payload):
    return f"prefix\n{start_marker}\n{payload}\n{end_marker}\nsuffix\n"


def _fixture_repository(tmp_path):
    repository = tmp_path / "repo"
    tooling = repository / "Development Files" / "Tooling"
    tooling.mkdir(parents=True)
    shutil.copy2(SCRIPT, tooling / SCRIPT.name)

    authority = repository / "Internal" / "app"
    targets = []
    for relative, start_marker, end_marker in FIXTURE_BLOCKS:
        source = authority / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            _contract_text(start_marker, end_marker, f"authority:{relative}"),
            encoding="utf-8",
        )
        for port in FIXTURE_PORTS:
            target = repository / port / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                _contract_text(start_marker, end_marker, f"authority:{relative}"),
                encoding="utf-8",
            )
            targets.append(target)
    return repository, tooling / SCRIPT.name, targets


def _link_directory(link, target):
    if sys.platform == "win32":
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        link.symlink_to(target, target_is_directory=True)


def _load_fixture_script(script):
    module_name = f"reader_sync_fixture_{id(script)}"
    specification = importlib.util.spec_from_file_location(module_name, script)
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    specification.loader.exec_module(module)
    return module


def test_reader_speech_contracts_are_synchronized():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert SCRIPT.name in HANDBOOK.read_text(encoding="utf-8")
    assert SCRIPT.name in TOOLING_README.read_text(encoding="utf-8")


def test_late_validation_failure_leaves_every_target_unchanged(tmp_path):
    repository, script, targets = _fixture_repository(tmp_path)
    first_target = repository / FIXTURE_PORTS[0] / FIXTURE_BLOCKS[0][0]
    first_target.write_text(
        _contract_text(
            FIXTURE_BLOCKS[0][1],
            FIXTURE_BLOCKS[0][2],
            "stale earlier target",
        ),
        encoding="utf-8",
    )
    malformed_target = repository / FIXTURE_PORTS[-1] / FIXTURE_BLOCKS[-1][0]
    malformed_target.write_text(
        f"prefix\n{FIXTURE_BLOCKS[-1][1]}\nmissing later end marker\n",
        encoding="utf-8",
    )
    before = {target: target.read_bytes() for target in targets}

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "missing end marker" in result.stderr
    assert {target: target.read_bytes() for target in targets} == before


def test_outside_maintained_port_is_rejected_before_writes(tmp_path):
    repository, script, targets = _fixture_repository(tmp_path)
    first_target = repository / FIXTURE_PORTS[0] / FIXTURE_BLOCKS[0][0]
    first_target.write_text(
        _contract_text(
            FIXTURE_BLOCKS[0][1],
            FIXTURE_BLOCKS[0][2],
            "stale earlier target",
        ),
        encoding="utf-8",
    )

    linux_root = repository / FIXTURE_PORTS[-1]
    outside_root = tmp_path / "outside-linux-port"
    shutil.copytree(linux_root, outside_root)
    shutil.rmtree(linux_root)
    _link_directory(linux_root, outside_root)
    before = {target: target.read_bytes() for target in targets}

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "approved maintained port" in result.stderr
    assert {target: target.read_bytes() for target in targets} == before


def test_successful_synchronization_is_idempotent(tmp_path):
    repository, script, targets = _fixture_repository(tmp_path)
    drifted = (
        repository / FIXTURE_PORTS[0] / FIXTURE_BLOCKS[0][0],
        repository / FIXTURE_PORTS[-1] / FIXTURE_BLOCKS[-1][0],
    )
    for target in drifted:
        target.write_text(
            target.read_text(encoding="utf-8").replace("authority:", "stale:"),
            encoding="utf-8",
        )

    first = subprocess.run(
        [sys.executable, str(script)],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=30,
    )
    after_first = {target: target.read_bytes() for target in targets}
    second = subprocess.run(
        [sys.executable, str(script)],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert first.returncode == 0, first.stdout + first.stderr
    assert "Synchronized 2 Reader speech contract blocks." in first.stdout
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Reader speech contracts are synchronized." in second.stdout
    assert {target: target.read_bytes() for target in targets} == after_first


def test_write_failure_restores_every_target(tmp_path):
    repository, script, targets = _fixture_repository(tmp_path)
    first_target = repository / FIXTURE_PORTS[0] / FIXTURE_BLOCKS[0][0]
    blocked_target = repository / FIXTURE_PORTS[-1] / FIXTURE_BLOCKS[0][0]
    for target in (first_target, blocked_target):
        target.write_text(
            target.read_text(encoding="utf-8").replace("authority:", "stale:"),
            encoding="utf-8",
        )
    before = {target: target.read_bytes() for target in targets}
    blocked_target.chmod(stat.S_IREAD)

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        blocked_target.chmod(stat.S_IREAD | stat.S_IWRITE)

    assert result.returncode != 0
    assert {target: target.read_bytes() for target in targets} == before
    assert list(repository.rglob("*.reader-sync-*")) == []


@pytest.mark.parametrize("unsafe_shape", ["traversal", "duplicate"])
def test_unsafe_contract_paths_are_rejected_before_writes(tmp_path, unsafe_shape):
    _repository, script, targets = _fixture_repository(tmp_path)
    module = _load_fixture_script(script)
    if unsafe_shape == "traversal":
        unsafe = module.ContractBlock(
            Path("../outside.py"),
            "unsafe start",
            "unsafe end",
        )
        expected_error = "unsafe Reader contract path"
    else:
        unsafe = module.CONTRACT_BLOCKS[0]
        expected_error = "duplicate Reader contract target path"
    module.CONTRACT_BLOCKS = (*module.CONTRACT_BLOCKS, unsafe)
    before = {target: target.read_bytes() for target in targets}

    with pytest.raises(RuntimeError, match=expected_error):
        module.main(["--check"])

    assert {target: target.read_bytes() for target in targets} == before
