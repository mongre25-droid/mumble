# Mumble legal files

This area holds development-facing legal material. The root `LICENSE` remains in place because GitHub, package builders, and users expect to find the project licence there.

`release-inventory.json` is the canonical machine-readable release inventory. It
records every direct, exactly pinned runtime dependency by applicable platform,
its declared upstream licence and source, the external-installer dependency
boundary, the non-bundled Python runtime, and every model/tokenizer identity
currently exposed by the downloadable-model registry. Release builders must
package these exact bytes as `RELEASE-INVENTORY.json`; the shared provenance
module hashes that member and repeats the applicable component rows in the
package SBOM.

`dependency-lock.json` is the complete direct-and-transitive authority. Its six
profiles bind Windows x86_64, macOS arm64/x86_64 on CPython 3.12 and 3.13, and Linux x86_64 to an
exact CPython minor, exact distribution versions, upstream licence/source facts,
and SHA-256 identities for compatible PyPI artifacts. The generated profile lock
files are installed as a complete set and `verify_dependency_closure.py` rejects
missing, extra, or version-drifted installed distributions. Refresh this authority
only with `Development Files/Tooling/refresh_dependency_locks.py`, review the
licence and artifact diff, and rerun the focused provenance/build checks.

The inventory is an engineering tracker, not a legal approval. In particular,
the recorded PyMuPDF and EbookLib copyleft/commercial terms require an explicit
owner/legal redistribution decision before a public release. A complete tracker
does not make signing, publication, installation, physical testing, or owner
acceptance pass.
