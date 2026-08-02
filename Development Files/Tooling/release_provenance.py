#!/usr/bin/env python3
"""Canonical, fail-closed provenance for every supported Mumble package.

Builders supply the exact package payload (without ``RELEASE-PROVENANCE.json``)
to :func:`generate_release_provenance`, add the returned bytes, then call
:func:`validate_release_provenance` against the completed member mapping.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Mapping


SCHEMA = "mumble.release-provenance.v1"
PROVENANCE_NAME = "RELEASE-PROVENANCE.json"
INVENTORY_NAME = "RELEASE-INVENTORY.json"
DEPENDENCY_CLOSURE_NAME = "DEPENDENCY-CLOSURE.json"
SUPPORTED = {
    ("windows", "x86_64", "zip"),
    ("macos", "arm64", "zip"),
    ("macos", "x86_64", "zip"),
    ("linux", "x86_64", "tar.gz"),
    ("linux", "x86_64", "zip"),
}
REQUIREMENTS = {
    "windows": Path("Internal/app/requirements.txt"),
    "macos": Path("Internal/app/Ports/macOS/app/requirements.txt"),
    "linux": Path("Internal/app/Ports/Linux/app/requirements.txt"),
}
LOCK_PROFILES = {
    ("windows", "x86_64"): ("windows-x86_64-cp313",),
    ("macos", "arm64"): ("macos-arm64-cp312", "macos-arm64-cp313"),
    ("macos", "x86_64"): ("macos-x86_64-cp312", "macos-x86_64-cp313"),
    ("linux", "x86_64"): ("linux-x86_64-cp313",),
}
EVIDENCE_KINDS = ("update", "rollback", "smoke", "physical")
EVIDENCE_STATUSES = {"passed", "unavailable", "not_applicable"}
DOCUMENT_KEYS = {
    "assets", "dependency_closure", "downloadable_artifacts", "entrypoint",
    "evidence", "input_closure", "installer", "licences", "member_manifest",
    "member_manifest_exclusion", "migrations_config", "notices", "package",
    "release_inventory", "runtime", "sbom", "schema", "source", "uninstaller",
}
RESOURCE_CONTRACTS = {
    "windows": {
        "entrypoint": "Mumble.exe",
        "installer": "Internal/app/install.ps1",
        "uninstaller": "Internal/app/uninstall.ps1",
        "assets": ["Internal/app/assets/mumble.ico", "Internal/app/webui/mumble.png"],
        "migrations_config": [
            "Internal/app/settings.py", "Internal/app/cloud_schema.sql",
            "Internal/app/update.py",
        ],
    },
    "macos": {
        "entrypoint": "app/mumble_mac.py",
        "installer": "Install Mumble.command",
        "uninstaller": "Uninstall Mumble.command",
        "assets": [
            "app/assets/mumble.icns", "app/assets/mumble.png",
            "app/webui/mumble.png",
        ],
        "migrations_config": [
            "app/cloud_schema.sql", "app/settings.py", "app/update.py",
        ],
    },
    "linux": {
        "entrypoint": "app/mumble_linux.py",
        "installer": "install.sh",
        "uninstaller": "uninstall.sh",
        "assets": ["app/assets/mumble.png", "app/webui/mumble.png"],
        "migrations_config": [
            "app/cloud_schema.sql", "app/settings.py", "app/update.py",
        ],
    },
}
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")


class ProvenanceError(ValueError):
    """The candidate cannot prove the release provenance contract."""


def _distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _packaged_text(data: bytes) -> bytes:
    """Match the builders' checkout-independent newline normalisation."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _member_bytes(value: Any) -> tuple[bytes, int]:
    mode = 0o644
    if isinstance(value, tuple) and len(value) == 2:
        value, mode = value
    if not isinstance(value, bytes):
        raise ProvenanceError("member values must be bytes or (bytes, mode)")
    if isinstance(mode, bool) or not isinstance(mode, int) or mode < 0 or mode > 0o7777:
        raise ProvenanceError("member mode must be an integer permission value")
    return value, mode


def _normalise_members(members: Mapping[str, Any]) -> dict[str, tuple[bytes, int]]:
    if not isinstance(members, Mapping) or not members:
        raise ProvenanceError("members must be a non-empty mapping")
    result: dict[str, tuple[bytes, int]] = {}
    folded: set[str] = set()
    for raw_path, value in members.items():
        if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
            raise ProvenanceError("member paths must be non-empty POSIX paths")
        path = PurePosixPath(raw_path)
        if path.is_absolute() or path.as_posix() != raw_path or ".." in path.parts:
            raise ProvenanceError(f"unsafe or non-canonical member path: {raw_path}")
        key = raw_path.casefold()
        if key in folded:
            raise ProvenanceError(f"case-colliding member path: {raw_path}")
        folded.add(key)
        result[raw_path] = _member_bytes(value)
    return result


def _git_head(repo_root: Path) -> str:
    override = os.environ.get("MUMBLE_RELEASE_BASE_COMMIT", "").strip().lower()
    if override:
        return _require_git_commit(repo_root, override)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvenanceError("source commit is unavailable") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", result):
        raise ProvenanceError("source commit is not a full Git object identity")
    return result


def _inventory(repo_root: Path) -> tuple[dict[str, Any], bytes]:
    path = repo_root / "Development Files" / "Legal" / "release-inventory.json"
    try:
        raw = _packaged_text(path.read_bytes())
        decoded = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceError("canonical release inventory is missing or invalid") from exc
    if decoded.get("schema") != "mumble.release-inventory.v1":
        raise ProvenanceError("canonical release inventory is not schema v1 JSON")
    components = decoded.get("components")
    if not isinstance(components, list) or not components:
        raise ProvenanceError("canonical release inventory has no SBOM components")
    for component in components:
        required = ("name", "version", "license", "source", "platforms")
        if not isinstance(component, dict) or any(not component.get(key) for key in required):
            raise ProvenanceError("canonical release inventory has incomplete licence facts")
    runtime = decoded.get("runtime")
    artifacts = decoded.get("downloadable_artifacts")
    if (
        not isinstance(runtime, dict)
        or not runtime.get("implementation")
        or not isinstance(runtime.get("python"), dict)
        or not runtime["python"].get("provenance")
        or not isinstance(runtime.get("version"), dict)
    ):
        raise ProvenanceError("canonical release inventory has missing runtime provenance")
    if not isinstance(artifacts, dict) or not artifacts.get("models") or not artifacts.get(
        "tokenizers"
    ):
        raise ProvenanceError("canonical release inventory has missing model/tokenizer provenance")
    for kind in ("models", "tokenizers"):
        for artifact in artifacts[kind]:
            if (
                not isinstance(artifact, dict)
                or artifact.get("bundled") is not False
                or not artifact.get("identity")
                or not artifact.get("source")
                or not artifact.get("revision")
                or not isinstance(artifact.get("artifact_hash"), dict)
                or artifact["artifact_hash"].get("status")
                not in {"unavailable", "not_applicable"}
                or artifact["artifact_hash"].get("value") is not None
                or not artifact["artifact_hash"].get("reason")
            ):
                raise ProvenanceError(
                    f"canonical release inventory has incomplete {kind} provenance"
                )
            revision = artifact["revision"]
            if isinstance(revision, str):
                if not re.fullmatch(r"[0-9a-f]{40}", revision):
                    raise ProvenanceError(f"{kind} revision is not immutable")
            elif (
                not isinstance(revision, dict)
                or revision.get("status") != "unavailable"
                or revision.get("value") is not None
                or not revision.get("reason")
            ):
                raise ProvenanceError(f"{kind} revision lacks explicit unavailable facts")
    return decoded, raw


def _dependency_closure(repo_root: Path) -> tuple[dict[str, Any], bytes]:
    path = repo_root / "Development Files" / "Legal" / "dependency-lock.json"
    try:
        raw = _packaged_text(path.read_bytes())
        decoded = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceError("canonical dependency closure is missing or invalid") from exc
    if decoded.get("schema") != "mumble.dependency-closure.v1":
        raise ProvenanceError("canonical dependency closure has an unsupported schema")
    components = decoded.get("components")
    profiles = decoded.get("profiles")
    if not isinstance(components, list) or not components or not isinstance(profiles, dict):
        raise ProvenanceError("canonical dependency closure has missing components or profiles")
    component_keys = set()
    for component in components:
        if (not isinstance(component, dict)
                or any(not component.get(key) for key in ("name", "version", "license", "source"))):
            raise ProvenanceError("dependency closure has incomplete licence facts")
        key = (_distribution_name(component["name"]), str(component["version"]))
        if key in component_keys:
            raise ProvenanceError("dependency closure has duplicate component facts")
        component_keys.add(key)
    expected_profiles = {name for names in LOCK_PROFILES.values() for name in names}
    if set(profiles) != expected_profiles:
        raise ProvenanceError("dependency closure profiles are missing or unexpected")
    for name, profile in profiles.items():
        if (not isinstance(profile, dict)
                or set(profile) != {"architecture", "artifacts", "lock_path", "packages", "platform", "python", "resolution", "target"}
                or not profile["packages"]):
            raise ProvenanceError(f"dependency closure profile is incomplete: {name}")
        keys = {(_distribution_name(row.get("name", "")), str(row.get("version", "")))
                for row in profile["packages"] if isinstance(row, dict)}
        if len(keys) != len(profile["packages"]) or not keys <= component_keys:
            raise ProvenanceError(f"dependency closure profile has unknown components: {name}")
        package_names = {_distribution_name(row["name"]) for row in profile["packages"]}
        artifact_names = {_distribution_name(value) for value in profile["artifacts"]}
        if profile["target"] != name or artifact_names != package_names:
            raise ProvenanceError(f"dependency artifact profile is contradictory: {name}")
        for artifacts in profile["artifacts"].values():
            if (not isinstance(artifacts, list) or not artifacts
                    or any(not row.get("filename")
                           or re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")) is None
                           for row in artifacts if isinstance(row, dict))
                    or any(not isinstance(row, dict) for row in artifacts)):
                raise ProvenanceError(f"dependency artifact hashes are incomplete: {name}")
    return decoded, raw


def _require_git_commit(repo_root: Path, commit: Any) -> str:
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ProvenanceError("source commit is not a full Git object identity")
    try:
        subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvenanceError("source commit is stale or unavailable") from exc
    return commit


def _find_suffix(members: Mapping[str, Any], suffix: str) -> list[str]:
    suffix = suffix.casefold()
    return sorted(
        path for path in members if path.casefold() == suffix or path.casefold().endswith("/" + suffix)
    )


def _one_suffix(members: Mapping[str, Any], suffix: str, label: str) -> str:
    matches = _find_suffix(members, suffix)
    if len(matches) != 1:
        raise ProvenanceError(f"package must contain exactly one {label}: {suffix}")
    return matches[0]


def _pins(data: bytes) -> list[dict[str, str]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProvenanceError("requirements.txt must be UTF-8") from exc
    pins = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--hash=sha256:"):
            continue
        match = PIN.fullmatch(line.removesuffix("\\").rstrip())
        if not match:
            raise ProvenanceError(
                f"requirements.txt line {line_number} is not one exact == direct pin"
            )
        pins.append({"name": match.group(1), "version": match.group(2)})
    if not pins:
        raise ProvenanceError("requirements.txt contains no direct exact pins")
    return pins


def _locked_artifact_hashes(data: bytes) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    active: str | None = None
    for raw in data.decode("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pin = PIN.fullmatch(line.removesuffix("\\").rstrip())
        if pin is not None:
            active = _distribution_name(pin.group(1))
            if active in result:
                raise ProvenanceError("transitive lock contains a duplicate distribution")
            result[active] = set()
            continue
        match = re.fullmatch(r"--hash=sha256:([0-9a-f]{64})(?:\s+\\)?", line)
        if match is None or active is None:
            raise ProvenanceError("transitive lock contains an invalid artifact hash line")
        result[active].add(match.group(1))
    if not result or any(not hashes for hashes in result.values()):
        raise ProvenanceError("transitive lock has a distribution without artifact hashes")
    return result


def _validate_profile_lock(profile: Mapping[str, Any], data: bytes) -> None:
    pins = {(_distribution_name(row["name"]), row["version"]) for row in _pins(data)}
    expected_pins = {
        (_distribution_name(row["name"]), str(row["version"]))
        for row in profile["packages"]
    }
    if pins != expected_pins:
        raise ProvenanceError("transitive lock pins contradict its dependency profile")
    actual_hashes = _locked_artifact_hashes(data)
    expected_hashes = {
        _distribution_name(name): {row["sha256"] for row in artifacts}
        for name, artifacts in profile["artifacts"].items()
    }
    if actual_hashes != expected_hashes:
        raise ProvenanceError("transitive lock hashes contradict its artifact profile")


def _model_revisions(data: bytes) -> dict[str, str]:
    """Read the literal runtime revision map without executing package code."""
    try:
        tree = ast.parse(data.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ProvenanceError("model provenance module is not valid UTF-8 Python") from exc
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name)
                        and target.id == "MODEL_REVISIONS" for target in node.targets)):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError) as exc:
                raise ProvenanceError("MODEL_REVISIONS must be a literal mapping") from exc
            if (not isinstance(value, dict) or not value
                    or any(not isinstance(key, str) or not isinstance(revision, str)
                           or re.fullmatch(r"[0-9a-f]{40}", revision) is None
                           for key, revision in value.items())):
                raise ProvenanceError("MODEL_REVISIONS contains incomplete immutable facts")
            return value
    raise ProvenanceError("model provenance module has no MODEL_REVISIONS authority")


def _validate_runtime_model_wiring(
    root: Path,
    platform: str,
    members: Mapping[str, tuple[bytes, int]],
    inventory: Mapping[str, Any],
) -> None:
    model_path = _one_suffix(members, "model_provenance.py", "model provenance module")
    canonical = _packaged_text(
        (root / "Internal" / "app" / "model_provenance.py").read_bytes())
    if members[model_path][0] != canonical:
        raise ProvenanceError("packaged model provenance differs from canonical runtime facts")
    revisions = _model_revisions(canonical)
    inventory_revisions = [
        row.get("revision") for row in inventory["downloadable_artifacts"]["models"]
        if row.get("role") == "speech-to-text"
    ]
    if inventory_revisions != list(revisions.values()):
        raise ProvenanceError("runtime model revisions contradict release inventory")
    controller_suffix = {
        "windows": "Internal/app/mumble.py",
        "macos": "app/mumble_mac.py",
        "linux": "app/mumble_linux.py",
    }[platform]
    controller = _one_suffix(members, controller_suffix, "runtime controller")
    compact_controller = re.sub(rb"\s+", b"", members[controller][0])
    if b"revision=model_revision(name)" not in compact_controller:
        raise ProvenanceError("runtime controller does not enforce model revisions")


def _validate_evidence(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict) or set(evidence) != set(EVIDENCE_KINDS):
        raise ProvenanceError("evidence must contain exactly update, rollback, smoke, physical")
    result = {}
    for kind in EVIDENCE_KINDS:
        item = evidence[kind]
        if not isinstance(item, dict) or set(item) != {"status", "reference", "reason"}:
            raise ProvenanceError(f"{kind} evidence has inconsistent fields")
        status, reference, reason = item["status"], item["reference"], item["reason"]
        if status not in EVIDENCE_STATUSES or not isinstance(reason, str) or not reason.strip():
            raise ProvenanceError(f"{kind} evidence has invalid status or reason")
        if status == "passed":
            if not isinstance(reference, str) or not reference.strip():
                raise ProvenanceError(f"passed {kind} evidence requires an exact reference")
        elif reference is not None:
            raise ProvenanceError(f"{status} {kind} evidence must use a null reference")
        if kind == "physical" and status == "passed":
            raise ProvenanceError("physical evidence cannot be passed by an automated builder")
        result[kind] = {"status": status, "reference": reference, "reason": reason.strip()}
    return result


def _require_path(members: Mapping[str, Any], path: str, label: str) -> None:
    if path not in members:
        raise ProvenanceError(f"declared {label} is missing from package members: {path}")


def generate_release_provenance(
    *,
    repo_root: str | Path,
    platform: str,
    architecture: str,
    package_format: str,
    members: Mapping[str, Any],
    entrypoint: str,
    installer: str | None,
    uninstaller: str | None,
    assets: list[str] | tuple[str, ...],
    migrations_config: list[str] | tuple[str, ...],
    evidence: Mapping[str, Any],
) -> bytes:
    """Return canonical provenance bytes for one exact supported payload."""
    root = Path(repo_root).resolve(strict=True)
    package_tuple = (platform, architecture, package_format)
    if package_tuple not in SUPPORTED:
        raise ProvenanceError(f"unsupported package tuple: {package_tuple!r}")
    supplied_resources = {
        "entrypoint": entrypoint,
        "installer": installer,
        "uninstaller": uninstaller,
        "assets": list(assets),
        "migrations_config": list(migrations_config),
    }
    if supplied_resources != RESOURCE_CONTRACTS[platform]:
        raise ProvenanceError("platform entrypoint/assets/config resources contradict contract")
    normal = _normalise_members(members)
    if PROVENANCE_NAME in normal:
        raise ProvenanceError(f"generation input must exclude {PROVENANCE_NAME}")

    inventory, inventory_bytes = _inventory(root)
    inventory_path = _one_suffix(normal, INVENTORY_NAME, "release inventory")
    if normal[inventory_path][0] != inventory_bytes:
        raise ProvenanceError("packaged RELEASE-INVENTORY.json differs from canonical inventory")
    dependency_authority, dependency_bytes = _dependency_closure(root)
    dependency_path = _one_suffix(normal, DEPENDENCY_CLOSURE_NAME, "dependency closure")
    if normal[dependency_path][0] != dependency_bytes:
        raise ProvenanceError("packaged dependency closure differs from canonical authority")
    profile_names = LOCK_PROFILES[(platform, architecture)]
    profiles = []
    profile_keys: set[tuple[str, str]] = set()
    for profile_name in profile_names:
        profile = dependency_authority["profiles"][profile_name]
        profile_lock_path = root / profile["lock_path"]
        canonical_profile_lock = _packaged_text(profile_lock_path.read_bytes())
        _validate_profile_lock(profile, canonical_profile_lock)
        packaged_profile_lock = _one_suffix(
            normal, profile_lock_path.name, "transitive dependency lock")
        if normal[packaged_profile_lock][0] != canonical_profile_lock:
            raise ProvenanceError("packaged transitive dependency lock differs from authority")
        profiles.append({
            "artifacts": profile["artifacts"],
            "components": profile["packages"],
            "lock": {"path": packaged_profile_lock, "sha256": _digest(canonical_profile_lock)},
            "name": profile_name,
            "python": profile["python"],
        })
        profile_keys.update(
            (_distribution_name(row["name"]), str(row["version"]))
            for row in profile["packages"]
        )
    _validate_runtime_model_wiring(root, platform, normal, inventory)
    notices_path = _one_suffix(normal, "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES")
    project_license = _one_suffix(normal, "LICENSE", "project licence")

    requirement_path = _one_suffix(normal, "requirements.txt", "requirements lock")
    canonical_requirements = _packaged_text((root / REQUIREMENTS[platform]).read_bytes())
    if normal[requirement_path][0] != canonical_requirements:
        raise ProvenanceError("packaged requirements.txt differs from canonical platform lock")
    pins = _pins(canonical_requirements)

    applicable = [
        dict(component)
        for component in inventory["components"]
        if platform in component["platforms"]
    ]
    component_keys = {(_distribution_name(row["name"]), str(row["version"])) for row in applicable}
    pin_keys = {(_distribution_name(row["name"]), row["version"]) for row in pins}
    if component_keys != pin_keys:
        raise ProvenanceError("requirements pins and SBOM/licence inventory contradict each other")
    if not pin_keys <= profile_keys:
        raise ProvenanceError("direct requirements are absent or version-drifted in dependency closure")
    closure_components = [
        dict(row) for row in dependency_authority["components"]
        if (_distribution_name(row["name"]), str(row["version"])) in profile_keys
    ]

    licence_paths = {project_license, dependency_path}
    for component in applicable:
        for suffix in component.get("license_files", []):
            matches = _find_suffix(normal, suffix)
            if len(matches) != 1:
                raise ProvenanceError(f"required licence file is missing or ambiguous: {suffix}")
            licence_paths.add(matches[0])

    for path in (entrypoint, *(assets or ()), *(migrations_config or ())):
        _require_path(normal, path, "entrypoint/asset/migration-config")
    for path in (installer, uninstaller):
        if path is not None:
            _require_path(normal, path, "installer resource")
    if installer is not None and b"model_revision" not in normal[installer][0]:
        raise ProvenanceError("installer does not enforce pinned model provenance")
    if not assets:
        raise ProvenanceError("at least one declared packaged asset is required")
    if not migrations_config:
        raise ProvenanceError("at least one migration/config identity is required")

    manifest = [
        {
            "mode": f"{mode & 0o7777:04o}",
            "path": path,
            "sha256": _digest(data),
            "size": len(data),
        }
        for path, (data, mode) in sorted(normal.items())
    ]
    notices = [{"path": notices_path, "sha256": _digest(normal[notices_path][0])}]
    licences = [
        {"path": path, "sha256": _digest(normal[path][0])} for path in sorted(licence_paths)
    ]
    source_commit = _git_head(root)
    closure_value = {
        "source_commit": source_commit,
        "package": {
            "platform": platform,
            "architecture": architecture,
            "format": package_format,
        },
        "members": manifest,
    }
    document = {
        "assets": list(assets),
        "dependency_closure": {
            "boundary": inventory["dependency_boundary"],
            "lock": {"path": requirement_path, "sha256": _digest(canonical_requirements)},
            "pins": pins,
            "transitive_inventory": {
                "authority": {"path": dependency_path, "sha256": _digest(dependency_bytes)},
                "profiles": profiles,
                "status": "verified",
            },
        },
        "downloadable_artifacts": inventory["downloadable_artifacts"],
        "entrypoint": entrypoint,
        "evidence": _validate_evidence(dict(evidence)),
        "input_closure": {
            "algorithm": "sha256",
            "digest": _digest(_canonical(closure_value)),
            "scope": "source commit, package tuple, and every payload member except RELEASE-PROVENANCE.json",
        },
        "installer": installer,
        "licences": licences,
        "member_manifest": manifest,
        "member_manifest_exclusion": PROVENANCE_NAME,
        "migrations_config": list(migrations_config),
        "notices": notices,
        "package": {
            "architecture": architecture,
            "format": package_format,
            "platform": platform,
        },
        "release_inventory": {"path": inventory_path, "sha256": _digest(inventory_bytes)},
        "runtime": inventory["runtime"],
        "sbom": {
            "components": closure_components,
            "direct_components": applicable,
            "format": "Mumble dependency closure v1 plus release inventory v1",
        },
        "schema": SCHEMA,
        "source": {
            "commit": source_commit,
            "identity": "base Git commit plus input_closure.digest",
            "vcs": "git",
        },
        "uninstaller": uninstaller,
    }
    output = _canonical(document)
    completed = dict(normal)
    completed[PROVENANCE_NAME] = (output, 0o644)
    validate_release_provenance(output, repo_root=root, members=completed)
    return output


def validate_release_provenance(
    provenance_bytes: bytes,
    *,
    repo_root: str | Path,
    members: Mapping[str, Any],
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    """Validate canonical provenance against current Git and exact package bytes."""
    if not isinstance(provenance_bytes, bytes):
        raise ProvenanceError("provenance must be bytes")
    try:
        document = json.loads(provenance_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError("release provenance is not valid UTF-8 JSON") from exc
    if provenance_bytes != _canonical(document):
        raise ProvenanceError("release provenance is not canonical JSON")
    if document.get("schema") != SCHEMA:
        raise ProvenanceError("release provenance schema is missing or unsupported")
    document_keys = set(document)
    if document_keys != DOCUMENT_KEYS:
        missing = ", ".join(sorted(DOCUMENT_KEYS - document_keys)) or "none"
        unexpected = ", ".join(sorted(document_keys - DOCUMENT_KEYS)) or "none"
        raise ProvenanceError(
            "release provenance fields are contradictory; "
            f"missing: {missing}; unexpected: {unexpected}"
        )
    for key in (
        "source",
        "package",
        "member_manifest",
        "dependency_closure",
        "sbom",
        "runtime",
        "downloadable_artifacts",
        "notices",
        "licences",
        "release_inventory",
        "assets",
        "migrations_config",
        "evidence",
        "input_closure",
        "entrypoint",
        "installer",
        "uninstaller",
    ):
        if key not in document:
            raise ProvenanceError(f"release provenance has missing {key.replace('_', ' ')}")

    root = Path(repo_root).resolve(strict=True)
    source = document["source"]
    if not isinstance(source, dict) or source.get("vcs") != "git" or source.get(
        "identity"
    ) != "base Git commit plus input_closure.digest":
        raise ProvenanceError("source commit identity is contradictory")
    recorded_source = source.get("commit")
    _require_git_commit(root, recorded_source)
    expected_source = (expected_source_commit or _git_head(root)).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_source):
        raise ProvenanceError("expected source commit is not a full Git identity")
    if recorded_source != expected_source:
        raise ProvenanceError("source commit is stale or contradictory")
    package = document["package"]
    package_tuple = (package.get("platform"), package.get("architecture"), package.get("format"))
    if package_tuple not in SUPPORTED:
        raise ProvenanceError("provenance contains an unsupported package tuple")
    if set(package) != {"platform", "architecture", "format"}:
        raise ProvenanceError("package facts contain unexpected fields")
    actual_resources = {
        field: document[field]
        for field in ("entrypoint", "installer", "uninstaller", "assets", "migrations_config")
    }
    if actual_resources != RESOURCE_CONTRACTS[package["platform"]]:
        raise ProvenanceError("platform entrypoint/assets/config resources contradict contract")
    _validate_evidence(document["evidence"])

    normal = _normalise_members(members)
    if PROVENANCE_NAME not in normal or normal[PROVENANCE_NAME][0] != provenance_bytes:
        raise ProvenanceError("package provenance member is missing or contradictory")
    payload = {path: value for path, value in normal.items() if path != PROVENANCE_NAME}
    actual_manifest = [
        {
            "mode": f"{mode & 0o7777:04o}",
            "path": path,
            "sha256": _digest(data),
            "size": len(data),
        }
        for path, (data, mode) in sorted(payload.items())
    ]
    declared_paths = {row.get("path") for row in document["member_manifest"]}
    actual_paths = set(payload)
    extras = sorted(actual_paths - declared_paths)
    if extras:
        raise ProvenanceError("unexpected package member: " + ", ".join(extras))
    if actual_manifest != document["member_manifest"]:
        raise ProvenanceError("member manifest is stale or contradictory")

    entrypoint = document["entrypoint"]
    if not isinstance(entrypoint, str) or not entrypoint:
        raise ProvenanceError("entrypoint fact is missing or contradictory")
    _require_path(payload, entrypoint, "entrypoint")
    for field in ("installer", "uninstaller"):
        path = document[field]
        if path is not None:
            if not isinstance(path, str) or not path:
                raise ProvenanceError(f"{field} fact is contradictory")
            _require_path(payload, path, field)
    for field in ("assets", "migrations_config"):
        paths = document[field]
        if (not isinstance(paths, list) or not paths
                or any(not isinstance(path, str) or not path for path in paths)):
            raise ProvenanceError(f"{field.replace('_', '/')} facts are missing")

    closure_value = {
        "source_commit": document["source"]["commit"],
        "package": package,
        "members": actual_manifest,
    }
    expected_closure = _digest(_canonical(closure_value))
    if document["input_closure"].get("digest") != expected_closure:
        raise ProvenanceError("input closure digest is stale or contradictory")

    inventory, inventory_bytes = _inventory(root)
    inventory_path = document["release_inventory"].get("path")
    if inventory_path not in payload or payload[inventory_path][0] != inventory_bytes:
        raise ProvenanceError("release inventory member is missing or stale")
    if document["release_inventory"].get("sha256") != _digest(inventory_bytes):
        raise ProvenanceError("release inventory hash is stale")
    applicable = [
        dict(row) for row in inventory["components"] if package["platform"] in row["platforms"]
    ]
    if document["sbom"].get("direct_components") != applicable:
        raise ProvenanceError("direct SBOM/licence tracker is missing or contradictory")
    if document["runtime"] != inventory["runtime"]:
        raise ProvenanceError("runtime provenance is missing or contradictory")
    if document["downloadable_artifacts"] != inventory["downloadable_artifacts"]:
        raise ProvenanceError("downloadable model/tokenizer provenance is missing or contradictory")
    _validate_runtime_model_wiring(root, package["platform"], payload, inventory)
    installer = document["installer"]
    if installer is not None and b"model_revision" not in payload[installer][0]:
        raise ProvenanceError("installer does not enforce pinned model provenance")

    lock = document["dependency_closure"].get("lock", {})
    lock_path = lock.get("path")
    canonical_lock = _packaged_text(
        (root / REQUIREMENTS[package["platform"]]).read_bytes()
    )
    if lock_path not in payload or payload[lock_path][0] != canonical_lock:
        raise ProvenanceError("requirements lock is missing or differs from canonical platform lock")
    if lock.get("sha256") != _digest(canonical_lock):
        raise ProvenanceError("requirements lock hash is stale")
    if document["dependency_closure"].get("pins") != _pins(canonical_lock):
        raise ProvenanceError("dependency pins drifted from canonical requirements")
    if document["dependency_closure"].get("boundary") != inventory["dependency_boundary"]:
        raise ProvenanceError("dependency closure boundary is contradictory")
    dependency_authority, dependency_bytes = _dependency_closure(root)
    transitive = document["dependency_closure"].get("transitive_inventory", {})
    dependency_member = transitive.get("authority", {}).get("path")
    if (dependency_member not in payload
            or payload[dependency_member][0] != dependency_bytes
            or transitive.get("authority", {}).get("sha256") != _digest(dependency_bytes)):
        raise ProvenanceError("transitive dependency authority is missing or stale")
    if transitive.get("status") != "verified":
        raise ProvenanceError("transitive dependency closure is missing or contradictory")
    expected_profiles = []
    profile_keys: set[tuple[str, str]] = set()
    for profile_name in LOCK_PROFILES[(package["platform"], package["architecture"])]:
        profile = dependency_authority["profiles"][profile_name]
        profile_lock_path = root / profile["lock_path"]
        canonical_profile_lock = _packaged_text(profile_lock_path.read_bytes())
        _validate_profile_lock(profile, canonical_profile_lock)
        packaged_profile_lock = _one_suffix(
            payload, profile_lock_path.name, "transitive dependency lock")
        if payload[packaged_profile_lock][0] != canonical_profile_lock:
            raise ProvenanceError("transitive dependency lock is missing or stale")
        expected_profiles.append({
            "artifacts": profile["artifacts"],
            "components": profile["packages"],
            "lock": {"path": packaged_profile_lock, "sha256": _digest(canonical_profile_lock)},
            "name": profile_name,
            "python": profile["python"],
        })
        profile_keys.update(
            (_distribution_name(row["name"]), str(row["version"]))
            for row in profile["packages"]
        )
    if transitive.get("profiles") != expected_profiles:
        raise ProvenanceError("transitive dependency profiles are missing or contradictory")
    closure_components = [
        dict(row) for row in dependency_authority["components"]
        if (_distribution_name(row["name"]), str(row["version"])) in profile_keys
    ]
    if document["sbom"].get("components") != closure_components:
        raise ProvenanceError("transitive SBOM/licence tracker is missing or contradictory")
    direct_keys = {(_distribution_name(row["name"]), row["version"])
                   for row in document["dependency_closure"].get("pins", [])}
    if not direct_keys <= profile_keys:
        raise ProvenanceError("direct requirements drifted from transitive dependency closure")

    for collection, label in ((document["notices"], "notice"), (document["licences"], "licence")):
        if not isinstance(collection, list) or not collection:
            raise ProvenanceError(f"{label} hashes are missing")
        for record in collection:
            path = record.get("path")
            if path not in payload or record.get("sha256") != _digest(payload[path][0]):
                raise ProvenanceError(f"{label} file is missing or stale: {path}")
    for path in document["assets"]:
        if path not in payload:
            raise ProvenanceError(f"declared asset is missing: {path}")
    for path in document["migrations_config"]:
        if path not in payload:
            raise ProvenanceError(f"declared migration/config identity is missing: {path}")
    return document


__all__ = [
    "INVENTORY_NAME",
    "PROVENANCE_NAME",
    "ProvenanceError",
    "generate_release_provenance",
    "validate_release_provenance",
]
