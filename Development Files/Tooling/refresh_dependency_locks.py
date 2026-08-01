#!/usr/bin/env python3
"""Refresh reviewed, fully pinned runtime dependency closure authorities.

This maintenance command uses PyPI's immutable version metadata. Builders never
call the network: they consume only the reviewed JSON and text files emitted
here. Run deliberately after changing a direct requirement, inspect the diff,
then run the focused provenance and package-builder tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]


def pins(text: str) -> dict[str, str]:
    result = {}
    for item in text.split():
        name, version = item.split("==", 1)
        result[name] = version
    return result


COMMON = pins("""
anyio==4.14.2 av==18.0.0 beautifulsoup4==4.15.0 bottle==0.13.4
certifi==2026.7.22 cffi==2.1.0 click==8.4.2 ctranslate2==4.8.1 defusedxml==0.7.1
EbookLib==0.20 et_xmlfile==2.0.0 faster-whisper==1.2.1 filelock==3.32.2
flatbuffers==25.12.19 fsspec==2026.7.0 h11==0.16.0 hf-xet==1.5.2
httpcore==1.0.9 httpx==0.28.1 huggingface_hub==1.26.0 idna==3.18
lxml==6.1.1 numpy==2.2.6 odfpy==1.4.1 openpyxl==3.1.5 packaging==26.2
pillow==12.3.0 protobuf==7.35.1 proxy_tools==0.1.0 pycparser==3.0
PyMuPDF==1.27.2.3 pyperclip==1.11.0 pystray==0.19.5
python-docx==1.2.0 python-pptx==1.0.2 pywebview==6.2.1 PyYAML==6.0.3
scipy==1.18.0 setuptools==83.0.0 six==1.17.0 sounddevice==0.5.5
soundfile==0.14.0 soupsieve==2.9.1 tokenizers==0.23.1 tqdm==4.70.0
typing_extensions==4.16.0 xlsxwriter==3.2.9
""")

WINDOWS = {
    **COMMON,
    **pins("""
colorama==0.4.6 clr_loader==0.3.1 comtypes==1.4.16 keyboard==0.13.5
mouse==0.7.1 onnxruntime==1.28.0 pythonnet==3.1.0 pywin32==312
sherpa_onnx==1.13.4 sherpa-onnx-core==1.13.4
"""),
}

MACOS = {
    **COMMON,
    "av": "14.2.0",
    **pins("""
coloredlogs==15.0.1 humanfriendly==10.0 mpmath==1.3.0 onnxruntime==1.19.2
pynput==1.8.2 pyobjc-core==12.2.1
pyobjc-framework-ApplicationServices==12.2.1 pyobjc-framework-Cocoa==12.2.1
pyobjc-framework-Quartz==12.2.1 pyobjc-framework-Security==12.2.1
pyobjc-framework-UniformTypeIdentifiers==12.2.1
pyobjc-framework-WebKit==12.2.1 sympy==1.14.0
"""),
}

MACOS313 = {**MACOS, "onnxruntime": "1.23.2"}

LINUX = {
    **COMMON,
    **pins("""
keyboard==0.13.5 mouse==0.7.1 onnxruntime==1.28.0 python-xlib==0.33
"""),
}

PROFILES = {
    "windows-x86_64-cp313": {
        "platform": "windows", "architecture": "x86_64",
        "python": "CPython 3.13", "packages": WINDOWS,
    },
    "macos-arm64-cp312": {
        "platform": "macos", "architecture": "arm64",
        "python": "CPython 3.12", "packages": MACOS,
    },
    "macos-arm64-cp313": {
        "platform": "macos", "architecture": "arm64",
        "python": "CPython 3.13", "packages": MACOS313,
    },
    "macos-x86_64-cp312": {
        "platform": "macos", "architecture": "x86_64",
        "python": "CPython 3.12", "packages": MACOS,
    },
    "macos-x86_64-cp313": {
        "platform": "macos", "architecture": "x86_64",
        "python": "CPython 3.13", "packages": MACOS313,
    },
    "linux-x86_64-cp313": {
        "platform": "linux", "architecture": "x86_64",
        "python": "CPython 3.13", "packages": LINUX,
    },
}

LOCK_PATHS = {
    "windows-x86_64-cp313": ROOT / "Internal/app/requirements-lock-win-x86_64-cp313.txt",
    "macos-arm64-cp312": ROOT / "Internal/app/Ports/macOS/app/requirements-lock-macos-arm64-cp312.txt",
    "macos-arm64-cp313": ROOT / "Internal/app/Ports/macOS/app/requirements-lock-macos-arm64-cp313.txt",
    "macos-x86_64-cp312": ROOT / "Internal/app/Ports/macOS/app/requirements-lock-macos-x86_64-cp312.txt",
    "macos-x86_64-cp313": ROOT / "Internal/app/Ports/macOS/app/requirements-lock-macos-x86_64-cp313.txt",
    "linux-x86_64-cp313": ROOT / "Internal/app/Ports/Linux/app/requirements-lock-linux-x86_64-cp313.txt",
}

LICENSE_OVERRIDES = {
    # Upstream release metadata omits the expression; the linked project licence is BSD-3-Clause.
    "fsspec": "BSD-3-Clause",
}
RELEASE_FILES: dict[tuple[str, str], list[dict[str, str]]] = {}


def metadata(name: str, version: str) -> dict[str, object]:
    with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
        release = json.load(response)
    info = release["info"]
    RELEASE_FILES[(name.casefold(), version)] = [
        {"filename": row["filename"], "sha256": row["digests"]["sha256"]}
        for row in release["urls"] if row.get("digests", {}).get("sha256")
    ]
    classifiers = [
        value.removeprefix("License :: ")
        for value in info.get("classifiers", [])
        if value.startswith("License :: ")
    ]
    licence = (
        info.get("license_expression") or info.get("license")
        or "; ".join(classifiers) or LICENSE_OVERRIDES.get(name.casefold())
    )
    if not str(licence).strip():
        raise RuntimeError(f"PyPI licence metadata is missing for {name}=={version}")
    return {
        "license": str(licence).strip(),
        "name": info.get("name") or name,
        "source": f"https://pypi.org/project/{name}/{version}/",
        "version": version,
    }


def compatible_files(profile_name: str, name: str, version: str) -> list[dict[str, str]]:
    files = RELEASE_FILES[(name.casefold(), version)]
    if profile_name.startswith("windows-"):
        platform_tokens = ("win_",)
        binary_tokens = ("win_amd64",)
        python_tokens = ("cp313", "abi3")
    elif profile_name.startswith("macos-arm64-"):
        platform_tokens = ("macosx_",)
        binary_tokens = ("arm64", "universal2")
        python_tokens = (profile_name.rsplit("-", 1)[1], "abi3")
    elif profile_name.startswith("macos-x86_64-"):
        platform_tokens = ("macosx_",)
        binary_tokens = ("x86_64", "universal2")
        python_tokens = (profile_name.rsplit("-", 1)[1], "abi3")
    else:
        platform_tokens = ("manylinux", "musllinux")
        binary_tokens = ("x86_64",)
        python_tokens = ("cp313", "abi3")
    pure = [row for row in files if "-none-any.whl" in row["filename"]]
    binary = [
        row for row in files
        if row["filename"].endswith(".whl")
        and any(token in row["filename"] for token in platform_tokens)
        and any(token in row["filename"] for token in binary_tokens)
        and (any(token in row["filename"] for token in python_tokens)
             or "-py3-none-" in row["filename"])
    ]
    source = [
        row for row in files
        if row["filename"].endswith((".tar.gz", ".zip"))
    ]
    candidates = pure + binary + source
    if not candidates:
        raise RuntimeError(f"no target-compatible artifact for {profile_name}: {name}=={version}")
    return sorted(candidates, key=lambda row: row["filename"])


def main() -> int:
    components = {}
    for profile in PROFILES.values():
        for name, version in profile["packages"].items():
            components.setdefault((name.casefold(), version), metadata(name, version))

    document = {
        "components": [components[key] for key in sorted(components)],
        "profiles": {},
        "schema": "mumble.dependency-closure.v1",
    }
    for profile_name, profile in PROFILES.items():
        lock_path = LOCK_PATHS[profile_name]
        artifacts = {
            name: compatible_files(profile_name, name, version)
            for name, version in sorted(
                profile["packages"].items(), key=lambda row: row[0].casefold())
        }
        rows = [
            "# Generated by Development Files/Tooling/refresh_dependency_locks.py.",
            f"# Profile: {profile_name}; {profile['python']}.",
            "# Every distribution and permitted upstream artifact is exact-pinned.",
        ]
        for name, version in sorted(
                profile["packages"].items(), key=lambda row: row[0].casefold()):
            hashes = [row["sha256"] for row in artifacts[name]]
            rows.append(f"{name}=={version} \\")
            rows.extend(
                f"    --hash=sha256:{digest}" + (" \\" if index < len(hashes) - 1 else "")
                for index, digest in enumerate(hashes)
            )
        rows.append("")
        lock_path.write_text("\n".join(rows), encoding="utf-8", newline="\n")
        document["profiles"][profile_name] = {
            "architecture": profile["architecture"],
            "artifacts": artifacts,
            "lock_path": lock_path.relative_to(ROOT).as_posix(),
            "packages": [
                {"name": name, "version": version}
                for name, version in sorted(
                    profile["packages"].items(), key=lambda row: row[0].casefold())
            ],
            "platform": profile["platform"],
            "python": profile["python"],
            "resolution": "exact direct and transitive pins; installer rejects an unlisted distribution",
            "target": profile_name,
        }
    target = ROOT / "Development Files/Legal/dependency-lock.json"
    target.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
