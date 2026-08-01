# Mumble third-party notices

Mumble itself is released under the repository's MIT licence. The canonical
machine-readable direct-dependency, runtime, model and tokenizer tracker is
`RELEASE-INVENTORY.json` in every candidate package (sourced from
`Development Files/Legal/release-inventory.json`). `RELEASE-PROVENANCE.json`
hashes that tracker, this notice, applicable retained licence texts, the exact
requirements lock and every other package member.

The installers resolve the exact reviewed direct-and-transitive profile into a
per-user virtual environment, then reject missing, extra, incompatible, or
version-drifted distributions. `DEPENDENCY-CLOSURE.json` is the exhaustive
machine-readable dependency licence/source and compatible-artifact hash tracker;
its matching profile lock is also packaged. Dependency wheels are not bundled,
so these facts are provenance rather than installed-machine evidence. The direct
inventory covers faster-whisper, sounddevice, NumPy, the
platform input-hook packages, pyperclip, pystray, Pillow, pywebview, the Windows
comtypes/pywin32/sherpa-onnx additions, soundfile, SciPy, PyMuPDF, python-docx,
Beautiful Soup, EbookLib, openpyxl, python-pptx and odfpy at their exact pins.

PyMuPDF is recorded as AGPL-3.0-only or an Artifex commercial licence, and
EbookLib as AGPL-3.0-only. Their inclusion in an installer lock is not an owner
or legal redistribution approval. Public distribution remains gated on the
explicit owner/legal decision and any required corresponding-source, notice or
commercial-licence work.

## Mumble Find

`experimental/system_search/` is original Mumble code. Its Windows Search
`SystemIndex` route uses `win32com.client` from the already pinned pywin32 312
runtime to access ADO, while the explicit Copy action uses the existing
`pyperclip` runtime. pywin32 is unmodified from PyPI and is distributed under
its BSD-style licence; the retained licence text is
`licenses/computer_control/pywin32-BSD.txt`. No source code, assets or binaries
from the launchers studied for this feature are incorporated, and Mumble does
not bundle or modify Windows Search.

Flow Launcher, Ueli and Microsoft PowerToys were reviewed as permissively
licensed product references. Their high-level patterns — provider-based local
discovery, bounded indexing, keyboard navigation, recent/favourite ranking and
explicit result actions — informed the design. They are not runtime
dependencies and their source was not copied.

## Issue #19 local-AI benchmark inventory

The versioned inventory under
`Development Files/Research/local-ai-benchmark/v1/` is research and benchmark
metadata only. It does not add Moonshine, Parakeet, Qwen, Granite, Model2Vec,
`llama.cpp`, or any new model weights/runtime to the distributed Mumble
application. The retained faster-whisper, CTranslate2, ONNX Runtime, and
sherpa-onnx notices remain governed by the existing dependency records.

Each candidate inventory entry keeps source code, runtime dependencies, model
weights, tokenizer, dataset/conversion provenance, redistribution, attribution,
access gating, and branding as separate checks. Official source links and exact
artifact hashes are supply-chain evidence, not permission to redistribute or a
claim that a candidate passed Windows, packaging, quality, or adoption gates.
No GPL implementation code from the researched dictation projects was copied;
only compatible high-level architecture was adapted through Mumble-owned test
and cache seams. Before any future integration or distribution, retain the exact
licence and NOTICE texts for the selected runtime and artifacts and complete the
owner/legal packaging decision recorded by the benchmark gate.

## Runtime components with notices retained here

### comtypes 1.4.16

- Project: https://github.com/enthought/comtypes
- Licence: MIT
- Use: bounded Windows target-field observation for correction learning
- Modifications: none; installed from PyPI
- Licence text: `licenses/computer_control/comtypes-MIT.txt`

### pywin32 312

- Project: https://github.com/mhammond/pywin32
- Licence: BSD-style / PSF family
- Use: Windows integration required by existing runtime paths
- Modifications: none; installed from PyPI
- Core notice: `licenses/computer_control/pywin32-BSD.txt`

### sherpa-onnx 1.13.4 and sherpa-onnx-core 1.13.4

- Project: https://github.com/k2-fsa/sherpa-onnx
- Licence: Apache License 2.0
- Use: optional SenseVoice + DirectML transcription acceleration
- Modifications: none; installed from PyPI
- Licence text: `licenses/computer_control/Apache-2.0.txt`

The `licenses/computer_control/` directory name is retained for packaging
compatibility from an earlier experiment; the remaining files above are general
runtime notices. The removed computer-control, pywinauto, six, UFO reference and
keyword-spotting model notices are no longer distributed.

When changing a pinned dependency, re-check its repository licence, separately
distributed model/asset licences, NOTICE requirements and binary-distribution
conditions, then update the canonical release inventory in the same change.
This inventory records engineering compliance work and is not legal advice.
