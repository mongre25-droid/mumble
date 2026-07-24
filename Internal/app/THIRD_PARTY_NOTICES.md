# Mumble third-party notices

Mumble itself is released under the repository's MIT licence. The complete
dependency inventory and the PyMuPDF distribution analysis live in the Core
licensing documentation.

## Mumble Search

`experimental/system_search/` is original Mumble code and uses only Python's
standard library plus the existing `pyperclip` runtime for the explicit Copy
path action. No source code, assets or binaries from the launchers studied for
this feature are incorporated.

Flow Launcher, Ueli and Microsoft PowerToys were reviewed as permissively
licensed product references. Their high-level patterns — provider-based local
discovery, bounded indexing, keyboard navigation, recent/favourite ranking and
explicit result actions — informed the design. They are not runtime
dependencies and their source was not copied.

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
conditions. This inventory records engineering compliance work and is not legal
advice.
