import copy
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "Development Files" / "Tooling" / "release_provenance.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("release_provenance", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _windows_members():
    app = ROOT / "Internal" / "app"
    legal = ROOT / "Development Files" / "Legal"
    prefix = "Internal/app/"
    return {
        "Mumble.exe": (b"launcher", 0o644),
        "LICENSE": (ROOT / "LICENSE").read_bytes(),
        "RELEASE-INVENTORY.json": (legal / "release-inventory.json").read_bytes(),
        "DEPENDENCY-CLOSURE.json": (legal / "dependency-lock.json").read_bytes(),
        prefix + "requirements-lock-win-x86_64-cp313.txt": (
            app / "requirements-lock-win-x86_64-cp313.txt"
        ).read_bytes(),
        prefix + "THIRD_PARTY_NOTICES.md": (app / "THIRD_PARTY_NOTICES.md").read_bytes(),
        prefix + "licenses/computer_control/Apache-2.0.txt": (
            app / "licenses" / "computer_control" / "Apache-2.0.txt"
        ).read_bytes(),
        prefix + "licenses/computer_control/comtypes-MIT.txt": (
            app / "licenses" / "computer_control" / "comtypes-MIT.txt"
        ).read_bytes(),
        prefix + "licenses/computer_control/pywin32-BSD.txt": (
            app / "licenses" / "computer_control" / "pywin32-BSD.txt"
        ).read_bytes(),
        prefix + "requirements.txt": (app / "requirements.txt").read_bytes().replace(
            b"\r\n", b"\n"
        ).replace(b"\r", b"\n"),
        prefix + "assets/mumble.ico": (app / "assets" / "mumble.ico").read_bytes(),
        prefix + "webui/mumble.png": (app / "webui" / "mumble.png").read_bytes(),
        prefix + "settings.py": (app / "settings.py").read_bytes(),
        prefix + "cloud_schema.sql": (app / "cloud_schema.sql").read_bytes(),
        prefix + "update.py": (app / "update.py").read_bytes(),
        prefix + "mumble.py": (app / "mumble.py").read_bytes(),
        prefix + "model_provenance.py": (app / "model_provenance.py").read_bytes(),
        prefix + "install.ps1": (app / "install.ps1").read_bytes(),
        prefix + "uninstall.ps1": (app / "uninstall.ps1").read_bytes(),
    }


def _evidence():
    return {
        "update": {
            "status": "unavailable",
            "reference": None,
            "reason": "No disposable installed-machine update environment is available.",
        },
        "rollback": {
            "status": "unavailable",
            "reference": None,
            "reason": "No owner-authorised rollback exercise was performed.",
        },
        "smoke": {
            "status": "passed",
            "reference": "pytest:test_release_provenance",
            "reason": "Automated package inspection only; not physical installation.",
        },
        "physical": {
            "status": "unavailable",
            "reference": None,
            "reason": "No physical Windows evidence is available in this build environment.",
        },
    }


def _generate(module, members=None, **overrides):
    arguments = {
        "repo_root": ROOT,
        "platform": "windows",
        "architecture": "x86_64",
        "package_format": "zip",
        "members": members if members is not None else _windows_members(),
        "entrypoint": "Mumble.exe",
        "installer": "Internal/app/install.ps1",
        "uninstaller": "Internal/app/uninstall.ps1",
        "assets": [
            "Internal/app/assets/mumble.ico", "Internal/app/webui/mumble.png",
        ],
        "migrations_config": [
            "Internal/app/settings.py", "Internal/app/cloud_schema.sql",
            "Internal/app/update.py",
        ],
        "evidence": _evidence(),
    }
    arguments.update(overrides)
    return module.generate_release_provenance(**arguments)


def _package(module):
    members = _windows_members()
    provenance = _generate(module, members)
    members["RELEASE-PROVENANCE.json"] = provenance
    return provenance, members


def test_generate_and_validate_complete_canonical_provenance():
    module = _load_module()
    provenance, members = _package(module)

    decoded = module.validate_release_provenance(
        provenance, repo_root=ROOT, members=members
    )

    assert provenance.endswith(b"\n")
    assert provenance == (
        json.dumps(decoded, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    assert decoded["schema"] == "mumble.release-provenance.v1"
    assert decoded["package"] == {
        "architecture": "x86_64",
        "format": "zip",
        "platform": "windows",
    }
    assert decoded["dependency_closure"]["boundary"] == (
        "external installer with exact direct and transitive distribution pins; no wheels are bundled"
    )
    assert decoded["sbom"]["components"]
    assert decoded["runtime"]["python"]
    assert decoded["downloadable_artifacts"]["models"]
    assert decoded["downloadable_artifacts"]["tokenizers"]
    assert decoded["evidence"]["physical"]["status"] == "unavailable"


def test_validation_rejects_stale_source_and_member_hash():
    module = _load_module()
    provenance, members = _package(module)
    stale = json.loads(provenance)
    stale["source"]["commit"] = "0" * 40
    stale_bytes = (json.dumps(stale, indent=2, sort_keys=True) + "\n").encode()
    members["RELEASE-PROVENANCE.json"] = stale_bytes
    with pytest.raises(module.ProvenanceError, match="source commit"):
        module.validate_release_provenance(stale_bytes, repo_root=ROOT, members=members)

    provenance, members = _package(module)
    stale = json.loads(provenance)
    stale["source"]["commit"] = subprocess.run(
        ["git", "rev-parse", "HEAD^"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    closure = {
        "source_commit": stale["source"]["commit"],
        "package": stale["package"],
        "members": stale["member_manifest"],
    }
    closure_bytes = (
        json.dumps(closure, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()
    stale["input_closure"]["digest"] = hashlib.sha256(closure_bytes).hexdigest()
    stale_bytes = (json.dumps(stale, indent=2, sort_keys=True) + "\n").encode()
    members["RELEASE-PROVENANCE.json"] = stale_bytes
    with pytest.raises(module.ProvenanceError, match="source commit"):
        module.validate_release_provenance(stale_bytes, repo_root=ROOT, members=members)

    provenance, members = _package(module)
    members["Internal/app/settings.py"] += b"\n# drift"
    with pytest.raises(module.ProvenanceError, match="member manifest"):
        module.validate_release_provenance(provenance, repo_root=ROOT, members=members)


def test_generation_rejects_missing_notices_or_licences():
    module = _load_module()
    members = _windows_members()
    del members["Internal/app/THIRD_PARTY_NOTICES.md"]
    with pytest.raises(module.ProvenanceError, match="THIRD_PARTY_NOTICES"):
        _generate(module, members)

    members = _windows_members()
    del members["Internal/app/licenses/computer_control/Apache-2.0.txt"]
    with pytest.raises(module.ProvenanceError, match="licen[cs]e"):
        _generate(module, members)


def test_generation_rejects_dependency_drift():
    module = _load_module()
    members = _windows_members()
    members["Internal/app/requirements.txt"] += b"unexpected==9.9.9\n"
    with pytest.raises(module.ProvenanceError, match="requirements.*canonical"):
        _generate(module, members)


def test_generation_requires_verified_transitive_dependency_closure():
    module = _load_module()
    provenance, _ = _package(module)
    document = json.loads(provenance)
    closure = document["dependency_closure"]["transitive_inventory"]
    assert closure["status"] == "verified"
    assert [profile["name"] for profile in closure["profiles"]] == [
        "windows-x86_64-cp313"
    ]
    assert closure["profiles"][0]["components"]
    assert all(
        row["name"] and row["version"]
        for row in closure["profiles"][0]["components"]
    )


def test_generation_rejects_stale_runtime_model_wiring():
    module = _load_module()
    members = _windows_members()
    members["Internal/app/model_provenance.py"] = members[
        "Internal/app/model_provenance.py"
    ].replace(
        b"d1d751a5f8271d482d14ca55d9e2deeebbae577f", b"0" * 40,
    )
    with pytest.raises(module.ProvenanceError, match="model provenance"):
        _generate(module, members)


def test_validation_rejects_unexpected_members():
    module = _load_module()
    provenance, members = _package(module)
    members["surprise.bin"] = b"not declared"
    with pytest.raises(module.ProvenanceError, match="unexpected.*member"):
        module.validate_release_provenance(provenance, repo_root=ROOT, members=members)


def test_validation_rejects_contradictory_entrypoint_and_resource_facts():
    module = _load_module()
    provenance, members = _package(module)
    damaged = json.loads(provenance)
    damaged["entrypoint"] = "LICENSE"
    damaged["assets"] = ["LICENSE"]
    damaged["migrations_config"] = ["LICENSE"]
    damaged_bytes = (json.dumps(damaged, indent=2, sort_keys=True) + "\n").encode()
    members["RELEASE-PROVENANCE.json"] = damaged_bytes
    with pytest.raises(module.ProvenanceError, match="entrypoint|asset"):
        module.validate_release_provenance(
            damaged_bytes, repo_root=ROOT, members=members
        )

    damaged = json.loads(provenance)
    damaged["surprise_fact"] = "not in schema"
    damaged_bytes = (json.dumps(damaged, indent=2, sort_keys=True) + "\n").encode()
    members["RELEASE-PROVENANCE.json"] = damaged_bytes
    with pytest.raises(module.ProvenanceError, match="unexpected: surprise_fact"):
        module.validate_release_provenance(
            damaged_bytes, repo_root=ROOT, members=members
        )


@pytest.mark.parametrize("missing", ["runtime", "downloadable_artifacts"])
def test_validation_rejects_missing_runtime_or_model_provenance(missing):
    module = _load_module()
    provenance, members = _package(module)
    damaged = json.loads(provenance)
    del damaged[missing]
    damaged_bytes = (json.dumps(damaged, indent=2, sort_keys=True) + "\n").encode()
    members["RELEASE-PROVENANCE.json"] = damaged_bytes
    with pytest.raises(module.ProvenanceError, match=missing.replace("_", ".*")):
        module.validate_release_provenance(
            damaged_bytes, repo_root=ROOT, members=members
        )


def test_generation_rejects_inconsistent_platform_facts():
    module = _load_module()
    with pytest.raises(module.ProvenanceError, match="supported package tuple"):
        _generate(module, architecture="arm64")


def test_generation_rejects_false_physical_pass():
    module = _load_module()
    evidence = copy.deepcopy(_evidence())
    evidence["physical"] = {
        "status": "passed",
        "reference": "automated:test",
        "reason": "A package inspection was mislabeled as physical evidence.",
    }
    with pytest.raises(module.ProvenanceError, match="physical.*passed"):
        _generate(module, evidence=evidence)
