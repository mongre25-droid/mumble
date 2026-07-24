# Mumble CI and cross-platform parity research

**Issue:** [#9 — Define trustworthy CI and cross-platform parity evidence](https://github.com/mongre25-droid/mumble/issues/9)

**Research date:** 2026-07-24

**Audited checkout:** `5e19b1ef87e1276803d0fbb5090c7fa593339c8d`

**Primary failed run:** [Mumble CI 30061856259](https://github.com/mongre25-droid/mumble/actions/runs/30061856259)

**Scope:** evidence and decisions only; no workflow, dependency, application, port, packaging, release, or Core record changes

## Decision

Run 30061856259 is useful failure evidence, but **Mumble does not yet have a trustworthy cross-platform release gate**.

The immediate blockers are real:

1. Windows reached its offline script suite and reported 68 of 70 scripts passing, with an unavailable offline `base.en` model snapshot and a valid contained meeting-audio path being rejected.
2. macOS and Linux stopped at the same Pillow 12.2.0 advisory gate before their suites; Linux also skipped its built-archive install/uninstall lifecycle.
3. The website stopped at four npm advisories before either its production build or Astro validation.

There is also a deeper evidence defect: Windows and macOS invoke each test file with plain Python. Seven Windows files and six macOS files are pytest-style modules that can exit successfully without executing their test functions. Linux already detects this style and invokes pytest. Therefore even a future `70 passed` Windows script summary would not, by itself, prove that all intended tests ran.

The target is a non-permissive, observable CI design in which security audits and functional suites both run, each check has one stable meaning, packaged artifacts are promoted only after all required evidence succeeds, and physical operating-system behavior remains an explicit release gate rather than an inference from source or hosted runners.

The approved cross-platform product name is **Mumble Find**, with the visible action **Find apps & files**, as resolved by issue #6. The audited source still uses the older **Mumble Search** label and `system_search` identifiers. macOS currently lacks the local app/file/folder feature altogether; that is a parity defect to implement, not an intentional platform exclusion. Issue #7 is revising the shared indexing, rendering, drag, focus, and shortcut-lifecycle contract that Windows, macOS, and Linux must ultimately verify.

## Evidence boundary

This report distinguishes three kinds of evidence:

- **Source evidence:** a code path or test exists in the audited commit.
- **Automated execution evidence:** a named CI command actually ran at that commit and its result is recorded.
- **Physical evidence:** the installed application was exercised on real hardware/session types with real permissions, focus, audio, tray/menu, and input behavior.

Source presence is not runtime proof. A checksum-valid archive is not an installable or releasable product. A hosted macOS or Xvfb job is not a substitute for physical permission and desktop-session checks.

## Exact result of run 30061856259

The `push` run started at `2026-07-24T02:31:36Z`, completed at `02:36:43Z`, and tested commit `5e19b1ef87e1276803d0fbb5090c7fa593339c8d` on `main`.

| Job | Result | Last meaningful evidence | What did not run |
|---|---|---|---|
| `windows-offline` | Failed | Compile, restricted flake8, Bandit, pip-audit passed; runner reported 68/70 scripts | No Windows package, installer, installed-app smoke, update lifecycle, or physical behavior |
| `macos-offline` | Failed | Unsigned source archive built and SHA-256 matched; compile, restricted flake8, and Bandit passed | Entire `run_tests.py --all` suite; install, launch, permissions, signing, notarisation, Intel behavior |
| `linux-offline-and-packaging` | Failed | Shell validation, CPython 3.14 x64 wheel resolution, deterministic tar/zip build, compile, restricted flake8, and Bandit passed | Entire Xvfb suite and built-archive install/uninstall lifecycle; real X11/Wayland behavior |
| `website` | Failed | `npm ci` completed from `package-lock.json` | `npm run build` and `npx astro check` |

### Windows failures

The downloaded `windows-offline-test-results/test-results.json` records:

```text
mode: offline
total: 70
passed: 68
failed: 2
seconds: 159.51
```

The two failed script files and their exact failure signals are:

1. `test_lightweight.py`

   ```text
   [FAIL] WhisperModel base.en load: Cannot find an appropriate cached snapshot
   folder for the specified revision on the local disk and outgoing traffic has
   been disabled. To enable repo look-ups and downloads online, set
   'HF_HUB_OFFLINE=0' as environment variable.
   ```

   This is a test-fixture/precondition conflict: the offline suite requires a real cached `base.en` model, but the clean hosted runner does not contain it. It is not proof that ordinary installed dictation fails.

2. `test_service_platform_regressions.py`

   ```text
   [FAIL] meeting audio resolution accepts contained files
   Service/platform regressions: 54 passed, 1 failed
   ```

   This is a real Windows path-containment regression in the test scenario: a meeting audio file created below the isolated meeting directory is rejected. The same log later says `Recording contains no audio`; the first containment assertion is the precise failure to fix before interpreting downstream audio behavior.

### macOS and Linux advisory stop

Both ports pin `Pillow==12.2.0`. `pip-audit` reported `Found 20 known vulnerabilities in 1 package`, with 12.3.0 as the fixed version. The unique reported advisory identifiers were:

```text
PYSEC-2026-2253, PYSEC-2026-2254, PYSEC-2026-2255, PYSEC-2026-2256,
PYSEC-2026-2257, PYSEC-2026-3451, PYSEC-2026-3452, PYSEC-2026-3453,
PYSEC-2026-3454, PYSEC-2026-3493, PYSEC-2026-3494, PYSEC-2026-3495,
PYSEC-2026-3496
```

Some identifiers were emitted more than once in the 20 findings. Because the audit step is correctly non-permissive, all later steps stopped. No macOS or Linux `test-results.json` exists in the run artifacts.

### Website advisory stop

`npm audit --audit-level=moderate` reported four findings and exited 1:

| Package | Severity | Advisory |
|---|---|---|
| Astro 2.9.0–7.0.9 | Moderate | [GHSA-4g3v-8h47-v7g6](https://github.com/advisories/GHSA-4g3v-8h47-v7g6) |
| fast-uri 3.0.0–3.1.3 | High | [GHSA-v2hh-gcrm-f6hx](https://github.com/advisories/GHSA-v2hh-gcrm-f6hx) |
| sharp below 0.35.0 | High | [GHSA-f88m-g3jw-g9cj](https://github.com/advisories/GHSA-f88m-g3jw-g9cj) |
| svgo 4.0.0–4.0.1 | High | [GHSA-2p49-hgcm-8545](https://github.com/advisories/GHSA-2p49-hgcm-8545) |

This proves the locked dependency graph failed the configured security policy. It does **not** show whether the website builds, because build and Astro check were skipped.

### Artifact evidence

Three GitHub artifacts were attached to the failed run:

| Artifact | GitHub artifact digest | Contents verified after download |
|---|---|---|
| `windows-offline-test-results` | `sha256:238a9d9c670c7dfbc221ff3673bcac163dfce50b84e0ddfc63bdd06deec312c2` | `test-results.json`; 68/70 scripts, two failures |
| `macos-offline-test-results` | `sha256:7cd8fa34f3b6a7b5712f7ded3c180ccf6106470b273c83629265356535fb4677` | `Mumble-v0.95-macos.zip` and checksum; archive SHA-256 `8eda9a225cafaa3918f609783a2823d2064a9d30244fd09c86d3710bc86d3a54` matched |
| `linux-offline-and-release-results` | `sha256:a91bfac0d020ef8d0e2c5faec2c5d69f5f5650ddaa3126d4704ad2e132379c2e` | tar, zip, and sums; tar `589cec0e2e4fa2e4bf537ef9af10e0fe2083c8086696a6b2eb509786aa30982f`, zip `1d6e43c273b72cbf3fefdfec82179cb4005e6c43bd0b2725adf2cd0b0419c0cf`, both matched |

The GitHub artifact digests identify the uploaded artifact records; the archive hashes are the separate integrity evidence for the packaged files inside them.

The macOS zip is a `MacMumble/` source-and-installer archive, not a prebuilt `.app`. Its build script explicitly calls it unsigned. It includes neither the repository `LICENSE` nor `THIRD_PARTY_NOTICES.md`. The Linux archives include the repository `LICENSE` and a release manifest but no third-party notice file. These artifacts are diagnostic candidates produced before the later security/test gates, not release-qualified deliverables. GitHub describes workflow artifacts as stored workflow output, including logs, test results, and binaries; artifact existence itself is not an approval signal ([GitHub artifact documentation](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts)).

## Why a future green run could still mislead

### Test discovery gap

`Internal/app/run_tests.py` and the macOS port runner execute every selected file as:

```text
python test_file.py
```

That only works when a file runs its checks at module scope or has a script entry point. Static AST inspection, using the Linux runner's own `_uses_pytest` decision rule, found these pytest-style modules with test functions but no script entry point or top-level test invocation:

**Windows — seven apparent no-op successes:**

```text
test_core_engine_audit.py
test_prompt_template_registry.py
test_reader_end_to_end.py
test_recording_limits.py
test_tts_contract.py
test_ui_bug_regressions.py
test_voice_commands.py
```

**macOS — six apparent no-op successes if its suite reaches that point:**

```text
test_core_engine_audit.py
test_core_port_regressions.py
test_mac_ui_parity_remediation.py
test_recording_limits.py
test_ui_bug_regressions.py
test_ui_port_regressions.py
```

Linux's runner already uses pytest for this style and has a regression test that asserts the distinction. The target runner contract must be common across all three platforms and must report both script-file count and actual pytest test-case count. A zero-collected-test condition must fail.

### Sequential blind spots

There is no `continue-on-error`, so every executed gate is non-permissive. That is good. However, audit, tests, builds, and lifecycle checks are placed sequentially inside large jobs. An expected security failure therefore hides independent product evidence. Security must remain required while independent checks run as separate jobs or parallel steps whose results are all gathered by a final required summary.

### A successful archive build is currently over-signalled

Both port archive steps run before `pip-audit` and tests, and `upload-artifact` uses `if: always()`. That is useful for diagnosis but creates a naming risk: a failed run still exposes release-looking files. Diagnostic uploads should be named `candidate-*` or `diagnostic-*`. Only a final promotion job with `needs:` on every required check should create or attest a release artifact.

## Current workflow contract

The repository has one active workflow, `.github/workflows/ci.yml`, displayed as `Mumble CI`, with four fixed jobs. There is no job matrix.

### Triggers and permissions

- `push` on every branch and `pull_request` with no branch or path filter.
- No `workflow_dispatch`, scheduled run, release/tag trigger, or reusable workflow entry point.
- No concurrency rule, so superseded branch runs can continue consuming time.
- No workflow-level `permissions` declaration. Repository settings currently give the Actions token read permission and do not allow it to approve pull requests: `{"default_workflow_permissions":"read","can_approve_pull_request_reviews":false}`.
- The current effective default is appropriately narrow, but the workflow should still declare `permissions: contents: read` so its security contract travels with the file. GitHub supports workflow- and job-level least-privilege token permissions ([workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)).
- Third-party actions are referenced by movable major tags (`actions/checkout@v4`, `setup-python@v5`, `setup-node@v4`, `upload-artifact@v4`), not full immutable commit SHAs. GitHub supports policies requiring full-length SHA pins ([GitHub Actions policy documentation](https://docs.github.com/en/organizations/managing-organization-settings/disabling-or-limiting-github-actions-for-your-organization)).

### Working directories and caches

| Job | Default working directory | Runtime | Cache contract |
|---|---|---|---|
| Windows | `Internal/app` | Python 3.13 | pip cache keyed from `requirements-dev.txt` only |
| macOS | `Internal/app/Ports/macOS/app` | Python 3.12 on `macos-15` arm64 | pip cache keyed from port `requirements-dev.txt` only |
| Linux | `Internal/app/Ports/Linux/app` | hosted Ubuntu x64 system Python, asserted at least 3.12 | pip cache keyed from port `requirements-dev.txt` only |
| Website | `Development Files/Marketing/Website` | Node 24 | npm cache keyed from `package-lock.json` |

Each Python development requirements file contains `-r requirements.txt`. Hashing only the development file may fail to invalidate a cache when the referenced runtime file changes. GitHub's dependency-cache documentation recommends keys derived from the dependency files that actually define the graph ([dependency caching](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)). Target all relevant requirement files.

The Python files pin direct versions but do not include package hashes or a fully resolved transitive lock. The website has a lockfile and uses `npm ci`. The three Python runtime files intentionally differ by platform (`pynput` on macOS, keyboard/mouse and Windows-only packages on Windows, a newer NumPy pin on Linux), so parity means an explicit reviewed platform manifest—not byte identity.

## What the current checks actually prove

| Concern | Current evidence | Trust decision |
|---|---|---|
| Compile/import syntax | `compileall` ran before failures on all Python jobs | Useful source gate; not import/runtime closure |
| Lint | flake8 only selects `E9,F63,F7,F82` | Fatal/syntax-style errors only; not general lint quality |
| Static security | Bandit high severity/high confidence, excluding tests and other ports | Useful bounded gate; not a full security review |
| Dependency security | pip-audit/npm audit are required | Correctly blocking, but sequential placement hides tests/builds |
| Unit/integration tests | Windows runner reached 70 scripts; ports did not run | Windows has two failures plus seven no-op-risk files; no current port result |
| Assets/resources | Archive membership/checksum checks only; no common asset manifest | Partial, platform-specific evidence |
| Settings/config migration | Relevant tests exist in platform trees | Windows result is mixed; mac/Linux not executed; no stable dedicated check |
| Cloud schema migration | Windows `test_supabase_schema.py` exists | No dedicated check, no port parity, no live database migration evidence |
| Licence consistency | Root licence exists; Windows notice file exists; Linux archive includes root licence | No CI consistency gate; mac archive omits root licence; both port archives omit third-party notices |
| Website build | Command exists | Did not run in audited result |
| Windows build/installer | Scripts exist outside workflow | No CI evidence |
| macOS build/installer | Source zip built and checksummed | No app install/launch, signing, notarisation, or update proof |
| Linux build | Deterministic raw archives built and verified | Build evidence only; install/uninstall step was skipped |
| Update | Update code exists on all three platforms | Signed production channel disabled; no packaged update/restart/rollback gate |
| Smoke | Linux planned import smoke inside lifecycle | Skipped; no equivalent Windows/macOS installed smoke |
| Non-permissive behavior | No `continue-on-error`; commands fail jobs | Good, but monolithic sequencing creates missing evidence |

## Trustworthy target check set

Stable, unique job names matter because GitHub warns that duplicate job names across workflows can make required status checks ambiguous ([protected branch documentation](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)). The following names are the target contract, not an implementation in this ticket:

| Required check | Required meaning |
|---|---|
| `ci / source-policy` | YAML/shell syntax, full action SHA policy, compileall, fatal flake8, Bandit, licence/notice manifest, asset/resource manifest, no generated archive drift |
| `ci / dependency-audit-windows` | Locked Windows graph installs and passes pip-audit |
| `ci / dependency-audit-macos` | Locked macOS graph installs and passes pip-audit |
| `ci / dependency-audit-linux` | Locked Linux graph installs and passes pip-audit plus supported-wheel resolution |
| `ci / website-audit` | `npm ci` graph passes configured audit threshold |
| `ci / windows-tests` | Common honest runner executes every offline script/test case; zero collection fails; JSON/JUnit uploaded |
| `ci / macos-arm64-tests` | Same runner contract on hosted Apple silicon |
| `ci / macos-intel-tests` | Same runner contract on `macos-15-intel` or an explicitly approved replacement |
| `ci / linux-x11-tests` | Same runner contract under Xvfb, with explicit X11 adapter tests |
| `ci / website-build` | Release-content check, Astro build, Astro check, and output assertions |
| `ci / config-and-migrations` | Settings migrations, schema/RLS static contract, config round trip, downgrade/unknown-key preservation |
| `ci / parity-contract` | Declared feature/platform manifest requires the same approved product capabilities and names on every supported OS; only genuinely OS-required implementation differences may vary |
| `package / windows-candidate` | Build from clean checkout; installer install/start/smoke/update-uninstall checks in disposable Windows environment |
| `package / macos-candidate` | Build source/app candidate, verify bundle metadata/resources, install/launch smoke; no claim of notarisation |
| `package / linux-candidate` | Deterministic tar/zip, manifest/modes/checksums, install/start/import/uninstall lifecycle |
| `ci / required-summary` | Fails unless every required check above has succeeded; the only check selected for protection if GitHub plan limits make a single stable context preferable |

Release-only checks should remain separate:

| Release check | Meaning |
|---|---|
| `release / provenance` | Rebuild from the exact tag SHA, verify source identity, generate checksums/SBOM/attestations, and promote only already-qualified candidates |
| `release / windows-signing` | Authenticode/MSIX signature and install reputation path verified |
| `release / macos-sign-and-notarise` | Developer ID, hardened runtime, entitlements, notarisation log, stapling, and Gatekeeper launch verified |
| `release / physical-signoff` | Required real-device matrix attached and approved; never synthesized from CI |

### Target triggers

1. **Pull requests to `main`:** all `ci /` checks and candidate package checks. Cancel superseded runs for the same pull request.
2. **Pushes to `main`:** the same full checks, without canceling an already running main build merely because a newer commit arrives; retain an auditable result for every merged SHA.
3. **`workflow_dispatch`:** full checks with explicit optional diagnostic inputs; no bypass of required checks.
4. **Nightly or weekly schedule:** dependency audits, clean-cache install, current runner-image compatibility, and slower performance/stability baselines. A scheduled advisory failure should open/refresh work, not weaken the pull-request policy.
5. **Version tag/release:** release workflow only after the exact tag SHA already has a successful required summary; rebuild/promote with signing credentials held in an environment when the repository plan supports it.

Do not use broad path filters for the final required summary unless a separate deterministic change classifier guarantees that every pull request receives the same required context. A skipped required job can otherwise become a merge deadlock or a false omission.

### Matrix and failure behavior

- Use a matrix for supported Python/runtime variations where job names remain unique and stable. GitHub matrices are intended for operating-system and version combinations ([workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)).
- Set matrix `fail-fast: false` so one platform failure does not cancel evidence from other variants. Every variant remains required.
- Separate dependency audit, functional tests, and package lifecycle so all independent evidence runs even when one fails.
- Keep diagnostic artifact uploads under `if: always()`, but place release promotion in a final `needs:` job that only runs after success.
- Pin actions by full SHA, declare `permissions: contents: read`, use minimal job-specific additions only where unavoidable, and never expose release credentials to pull-request jobs.

## Formal cross-platform source parity audit

### Product surfaces

| Surface | Windows source | macOS source | Linux source | Current automated evidence | Decision / missing proof |
|---|---|---|---|---|---|
| Homepage | Present in web UI | Present, divergent copy | Present, divergent copy | No port suite ran | Source-only parity; establish common UI manifest/snapshot gate |
| Deck | Present with history/clipboard/prompts | Present | Present | Windows related scripts mixed; ports absent | Physical selection, focus, clipboard, and paste flow required |
| Stats | `stats.py` and UI present | Divergent `stats.py` present | Divergent `stats.py` present | Intended tests exist; no port result | Cross-platform data semantics need contract tests |
| Meetings | Recording/import/store/UI present | Port controller/store/UI present | Port controller/store/UI present | Windows containment regression; ports absent | Not parity-green until path fix and real audio/import tests |
| Reader | Parser/store/UI present | Present | Present | One Windows pytest-style end-to-end file can no-op; ports absent | Format, TTS, native file picker, and audio playback need physical checks |
| Web Search | Browser-provider fallback and result opening | Search hotkey opens configured web provider | Web fallback and external result opening | Source only | Browser choice, URL encoding, focus return, and offline error physical checks |
| Mumble Find | Approved name is absent from source; older `Mumble Search`/`system_search` engine and UI are present | Local app/file/folder feature is absent; the search hotkey currently performs Web Search | Older `Mumble Search`/`system_search` engine and UI are packaged | Windows script result is insufficient; Linux suite skipped; macOS has no feature to test | macOS absence is a parity defect. Implement and verify the issue #7 contract on all three platforms, then migrate visible wording to **Mumble Find** / **Find apps & files** |
| Settings | Full settings/bridge/UI | Port settings/bridge/UI | Port settings/bridge/UI | Windows migration scripts mixed; ports absent | Create a schema/route parity manifest; only OS-required shortcut, permission, path, or adapter defaults may differ |

The three web UI trees are copies, not one shared byte-identical build. Hashes differ for `index.html`, `app.js`, and `app.css`; macOS also lacks `remaster.css` and the Mumble Find system-search loader. A parity manifest may permit only genuinely OS-required implementation differences such as native shortcut notation, permissions, paths, window adapters, and packaging. It must never permit feature, naming, route, state-model, accessibility, or user-outcome drift. macOS's missing Mumble Find capability is therefore a defect, not an allowed manifest difference.

### Runtime and operating-system behavior

| Capability | Windows | macOS | Linux | Evidence status and required gate |
|---|---|---|---|---|
| Global shortcuts | `keyboard`/`mouse` bindings | `pynput`/Quartz, gated by Accessibility/Input Monitoring | read-only evdev hooks; input-group/device access needed | Source on all; physical press/hold/rebind/conflict/layout checks on each OS |
| Dictation | Controller and sounddevice capture | `mumble_mac.py` and macOS audio adapter | `mumble_linux.py` and sounddevice | Hosted tests cannot prove microphone permission, device switching, sleep/wake, Bluetooth, or long capture |
| Local transcription | faster-whisper plus optional Windows DirectML/Sherpa path | faster-whisper plus Apple-silicon adapter/fallback | faster-whisper CPU/platform path | Model fixture failure on Windows; no real model/hardware result on ports |
| Cloud transcription/processing | Provider routes and local fallback | Present | Present | Offline CI tests contracts only; no credentials/live provider proof by design |
| Paste and text insertion | Clipboard plus Win32 key injection | Pasteboard plus accessibility-controlled key injection | `wl-copy`/`wl-paste` or X11 tools plus `wtype`/`ydotool`/`xdotool` | Source/mock evidence only; focus preservation and hostile/slow target apps require physical matrix |
| Warm state | `_warm_model` present | `_warm_model` present | `_warm_model` present | No activation-to-visible-text or cold/warm hardware measurement in this run |
| Updates | Signed channel code present but production key/channel disabled | Platform swap script source exists | Platform swap/rollback source exists | No packaged update, rollback, permission preservation, or old-to-new physical gate |
| Packaging | Batch/PowerShell install files exist | CI builds unsigned source zip; installer constructs app locally | Deterministic tar/zip plus installer | Windows entirely absent; mac install absent; Linux lifecycle skipped |
| Permissions | Microphone/privacy and elevated-target realities | Microphone, Accessibility, Input Monitoring | evdev/uinput/device groups, desktop session, helper availability | Must be denied/allow/revoke/relaunch tested physically |
| Tray/menu | pystray menu source | pystray menu-bar source | pystray/AppIndicator path with documented GNOME fallback risk | No hosted physical tray/menu interaction evidence |
| Dragging/positioning | Overlay code | Cocoa/Tk coordinate and overlay code | GTK/pywebview overlay code | Source tests cover calculations; multi-monitor, DPI/scaling, menu bar/dock/panels require physical checks |
| Startup | Windows installer/startup integration source | LaunchAgent source and installer | XDG autostart desktop entry source | Linux simulated lifecycle skipped; no real login/reboot proof on any platform |
| Error recovery | Broad procedural tests | Port tests present | Port tests present | Windows partially ran; mac/Linux skipped; runner false-green risk must be fixed first |
| Performance/stability | `test_perf.py` and traces exist | `test_perf.py` exists | no equivalent `test_perf.py` in Linux port tree | No reliable cross-platform wall-clock, memory, CPU, long-session, suspend/resume, or crash-loop evidence |

## macOS-specific release gates

The current `macos-15` hosted job is arm64. GitHub also exposes `macos-15-intel`; both are needed for supported architecture evidence ([GitHub-hosted runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)). Mumble's archive is Python source with native wheels installed on the destination machine, so “universal” cannot be inferred from one arm64 job. Apple explains that universal deliverables contain both `arm64` and `x86_64` executable code and that architecture-specific compiled components must also be assessed ([Apple universal binary guidance](https://developer.apple.com/documentation/apple-silicon/building-a-universal-macos-binary)).

Required macOS gates:

1. **Microphone:** verify first prompt, deny, grant, revoke, device change, and relaunch. The installer writes `NSMicrophoneUsageDescription`, but no entitlements file is present. Apple requires explicit user authorization and the appropriate usage description/entitlement for protected capture ([Apple capture authorization](https://developer.apple.com/documentation/avfoundation/requesting-authorization-to-capture-and-save-media)).
2. **Accessibility and Input Monitoring:** verify global keyboard/mouse shortcuts, paste into another app, late permission grant, revoked permission, and clear recovery guidance. Apple exposes Input Monitoring as an explicit user-controlled permission ([Apple Support](https://support.apple.com/en-lamr/guide/mac-help/mchl4cedafb6/mac)).
3. **Menu bar and focus:** open every menu action; open/close Deck and Search without stealing or losing target-app focus; test full-screen Spaces and multiple displays. Apple warns that forced activation can steal focus and recommends cooperative activation ([AppKit activation guidance](https://developer.apple.com/documentation/appkit/nsapplication/activationoptions/activateignoringotherapps)).
4. **Signing/notarisation:** replace the current unsigned/ad-hoc path for public distribution with Developer ID signing, hardened runtime, reviewed entitlements, secure timestamp, notarisation, stapling, and Gatekeeper test. Apple requires Developer ID and hardened runtime for notarisation ([Apple notarisation guidance](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)).
5. **Architecture:** clean install and real use on Apple silicon and Intel; verify all Python/native wheels, audio, clipboard, hotkeys, menu bar, Reader, meetings, and update swap on both.
6. **Lifecycle:** fresh install, reinstall, start at login, logout/login, upgrade preserving privacy identity/settings, rollback, uninstall, and data-preservation/purge choices.
7. **Mumble Find:** after the issue #7 contract is revised and implemented, verify immediate first display, persistent/background indexing, visible-result-first icon loading, bounded/virtualised results, dedicated drag region, same-shortcut show/hide during active dictation, preserved dictation state, conflict handling, and focus restoration. Use the approved **Mumble Find** and **Find apps & files** wording.

## Linux-specific release gates

The hosted Linux job is Ubuntu x64 with Xvfb. It validates an X11-like headless path only; it does not start a Wayland compositor, exercise real evdev devices, own a desktop clipboard, show an AppIndicator, or capture audio.

The source currently:

- reads global input from evdev on X11 and Wayland;
- injects through `xdotool` on X11 and `wtype`/`ydotool` on Wayland, with uinput fallback;
- uses `xclip`/`xsel` or `wl-copy`/`wl-paste` for clipboard work;
- supports apt, dnf, pacman, and zypper package families in the installer;
- creates freedesktop desktop/autostart entries;
- uses pystray/AppIndicator where a desktop host is available.

Required Linux gates:

1. **X11:** real GNOME/KDE session, global press/hold shortcuts, selection/clipboard ownership, focus-preserving text insertion, tray/menu, overlay drag/position, audio, login autostart, update, uninstall.
2. **Wayland:** the same on at least GNOME and KDE, including a system without XWayland helpers. The standard XDG GlobalShortcuts portal creates user-approved global-shortcut sessions; the current direct-evdev implementation does not use it ([XDG portal documentation](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html)). That is an implementation decision for a later ticket, not a change made here.
3. **Permissions:** user not in `input` group, readable keyboard but unreadable mouse, no writable uinput, helper missing, helper denied, and permission granted after startup. No root-only happy path may be considered sufficient.
4. **Clipboard/text insertion:** plain text, Unicode/RTL, rich target, terminal, browser, Electron, image, slow clipboard owner, and target changing focus during processing.
5. **Audio:** PulseAudio and PipeWire, device unplug/replug, Bluetooth, no device, denied/sandboxed device, meeting plus dictation, and suspend/resume.
6. **Tray:** GNOME with and without AppIndicator extension, KDE, no tray host, and headless failure message.
7. **Packaging:** clean install/reinstall/uninstall on one current apt, dnf, and pacman-family distribution; validate every generated `.desktop` file against the freedesktop specification ([Desktop Entry Specification](https://specifications.freedesktop.org/desktop-entry/latest/)). Raw tar/zip portability is not proof of native-package integration.
8. **Architectures:** current CI resolves only x86_64 wheels. Either declare Linux x64 as the supported boundary or add arm64 dependency/build/runtime evidence.
9. **Mumble Find:** after the issue #7 contract is revised and implemented, verify the same indexing, visible-icon, bounded-result, drag, same-shortcut toggle, active-dictation preservation, conflict, and focus-restoration outcomes as Windows/macOS. Linux-native window, path, icon, and opener adapters may differ; the user-facing capability may not.

## Windows-specific release gates

Windows has the broadest current source/test tree but no package job. Required physical/package evidence includes:

1. clean install, repair/reinstall, normal launch, launch from shortcut, startup, update/rollback, uninstall, and both preserve-data/purge-data paths;
2. standard and elevated target applications, because Windows input injection can be restricted across integrity levels; verify an honest user-visible failure rather than silent clipboard-only success;
3. microphone deny/grant/revoke, device changes, Bluetooth, sleep/wake, and meeting plus dictation;
4. tray/menu, multi-monitor/DPI/taskbar positioning, focus return, Unicode/RTL, browser/Electron/Office/terminal paste targets;
5. signed distribution decision and signature validation. Microsoft warns that unsigned public Windows distribution receives a strong SmartScreen block and recommends a trusted signing/Store path ([Microsoft code-signing guidance](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)).

## Repository enforcement limits

Live GitHub API checks on 2026-07-24 found:

```text
repository: mongre25-droid/mumble
visibility: private
default branch: main
Actions default workflow permission: read
Actions may approve pull requests: false
environments: 0
CODEOWNERS files: 0
branch protection API: HTTP 403
rulesets API: HTTP 403
message: Upgrade to GitHub Pro or make this repository public to enable this feature.
```

GitHub documents branch protection for public repositories on Free and private repositories on Pro/Team/Enterprise ([protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)). Therefore the target required-check contract can be implemented as CI now, but it cannot currently be enforced against direct merge/push through repository branch protection. Changing repository visibility or buying a plan is an owner decision outside this ticket.

There are no deployment environments. On private repositories, environment availability/protection depends on plan, and required reviewers/wait timers have additional restrictions ([GitHub environment documentation](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments)). Until an eligible protected environment exists, signing and release credentials must not be introduced into this workflow.

## Staged remediation

### Stage 0 — restore honest evidence

1. Make one cross-platform test runner correctly execute procedural scripts, unittest files, and pytest modules; fail zero collection; emit JSON and JUnit with actual case counts.
2. Fix or deliberately reclassify the offline `base.en` model precondition without enabling uncontrolled network access inside the offline suite.
3. Fix the Windows contained meeting-audio path regression.
4. Review and update the Pillow and website lock graphs in bounded dependency tickets; keep audits blocking.

**Exit:** all independent suites, audits, builds, and lifecycle jobs run to completion and every failure is visible.

### Stage 1 — split trustworthy required checks

1. Split audits from functional suites and package lifecycles.
2. Add explicit permissions, concurrency, manual and scheduled triggers, stable names, full action SHA pins, complete cache dependency paths, timeouts, and diagnostic artifact names.
3. Add the final required summary and parity/config/licence/resource contracts.

**Exit:** a green SHA has one unambiguous evidence bundle, and a red audit never hides unrelated test/build results.

### Stage 2 — package candidates

1. Add a Windows build/install/smoke/update/uninstall lane.
2. Add macOS arm64 and Intel candidate install/launch lanes; keep unsigned candidates clearly labelled.
3. Restore Linux built-archive lifecycle and add an explicit support boundary for distributions/architectures.
4. Generate SBOM/licence/notice/resource manifests and verify they are inside every deliverable.

**Exit:** every candidate is built from the tested SHA and passes clean-environment lifecycle checks.

### Stage 3 — physical parity and public release

1. Run and record the macOS, Linux, and Windows physical matrices above.
2. Decide supported versions, architectures, desktops/session types, packaging formats, and signing channels.
3. Add protected release credentials/environment only after the repository plan and owner decisions permit it.
4. Promote—not rebuild independently—only candidates whose exact SHA and digest passed every required check.

**Exit:** release evidence includes automated provenance plus signed human physical sign-off; neither substitutes for the other.

## Recommended implementation tickets

Create these as separate, reviewable tickets; do not combine dependency, runner, product regression, packaging, and repository-plan work:

1. **Make the test runner execute every intended test case on Windows/macOS/Linux.** Include the 13 named no-op-risk files and zero-collection failure.
2. **Repair Windows clean-offline test fixtures and meeting path containment.** Acceptance is a clean runner with no network and the exact contained-file regression passing.
3. **Update audited dependency graphs.** Separate Python-port and website changes; record compatibility, licence, lock, build, and test evidence.
4. **Refactor CI into stable independent checks.** Implement the names/triggers/permissions/cache/concurrency/artifact rules above without product changes.
5. **Add licence, SBOM, asset/resource, config, and migration contracts.** Ensure every distributable carries consistent required notices.
6. **Add Windows candidate package lifecycle.** Build/install/start/smoke/update/rollback/uninstall in a disposable environment.
7. **Add macOS dual-architecture candidate CI and physical permission plan.** Keep signing/notarisation as a separately authorised release ticket.
8. **Add Linux X11/Wayland and distribution support plan.** Decide direct evdev versus portal integration explicitly; verify the issue #7 Mumble Find contract in both session types; do not silently call one desktop test “Linux parity.”
9. **Define and enforce the parity manifest.** Require Mumble Find and every other approved capability on Windows, macOS, and Linux. Permit only reviewed OS-required implementation differences, never feature drift.
10. **Choose repository enforcement model.** Owner decision: remain private/free without enforceable required checks, upgrade, or make public; then configure branch rules and a release environment if eligible.

## Exact audit commands and results

Commands were read-only except assigning/commenting on issue #9 and creating this report.

```powershell
gh auth status
# Authenticated to github.com as mongre25-droid; git protocol ssh; repo scope available.

gh issue edit 9 --add-assignee '@me'
# Issue assigned to mongre25-droid before substantive research.

gh run view 30061856259 --json status,conclusion,headSha,createdAt,updatedAt,url,jobs
# conclusion=failure; headSha=5e19b1ef...; four failed jobs.

gh run view 30061856259 --job <job-id> --log
# Exact failures recorded in this report.

gh api repos/mongre25-droid/mumble/actions/runs/30061856259/artifacts
# Three artifacts: Windows results, macOS candidate/checksum, Linux candidates/checksums.

gh run download 30061856259 --dir .issue9-artifacts
# Temporary local evidence only; removed before commit.

Get-FileHash -Algorithm SHA256 <downloaded archive>
# macOS and both Linux archive hashes matched their shipped checksum files.

tar -tf <downloaded archive>
# Inspected membership without treating archive presence as release approval.

gh api repos/mongre25-droid/mumble/actions/permissions/workflow
# {"default_workflow_permissions":"read","can_approve_pull_request_reviews":false}

gh api repos/mongre25-droid/mumble/environments
# {"total_count":0,"environments":[]}

gh api repos/mongre25-droid/mumble/branches/main/protection
gh api repos/mongre25-droid/mumble/rulesets
gh api repos/mongre25-droid/mumble/rules/branches/main
# Each returned HTTP 403 with the Pro/public upgrade message.

Get-ChildItem <platform-app> -Filter 'test_*.py'
# Windows 73 files (70 offline + 3 live); macOS 50; Linux 56.

# AST classification equivalent to Linux run_tests.py::_uses_pytest
# Windows: 7 pytest-style files can no-op under plain Python.
# macOS: 6 pytest-style files can no-op under plain Python.
# Linux: its runner dispatches this style through pytest.

Get-FileHash -Algorithm SHA256 <shared UI/runtime files>
# index.html/app.js/app.css and most service files differ across the three trees;
# no current common parity manifest explains allowed drift.
```

A later documentation-only push run, [30062640470](https://github.com/mongre25-droid/mumble/actions/runs/30062640470), completed with the same four failed jobs. Its logs independently reproduced the four website advisories, the 20 Pillow findings on macOS/Linux, and both Windows script failures. It is corroboration only, not a product-fix result.

## Uncertainty and non-claims

- No workflow or product fix was implemented or rerun by this ticket.
- No physical Windows, macOS, X11, or Wayland session was controlled during this audit.
- No signing identity, Apple Developer account, release secret, live AI credential, microphone, input device, or update server was used.
- The exact impact/exploitability of each dependency advisory was not independently reproduced; the report records the configured audit tools' primary output and fix versions.
- The AST test-discovery finding identifies files that the current command does not collect as pytest. It does not claim the assertions would fail when correctly executed.
- **Mumble Find** is the approved user-facing name from issue #6, and **Find apps & files** is the approved action. The audited source still uses the older `Mumble Search` label and `system_search` identifiers; that naming migration and the missing macOS implementation remain future work.
- GitHub plan/API state is time-sensitive and was verified on 2026-07-24.
- Source similarity and written tests do not prove cross-platform behavior. Physical gates remain mandatory for release truth.

## Primary references

- [GitHub Actions workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [GitHub dependency caching](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
- [GitHub workflow artifacts](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts)
- [GitHub-hosted runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [GitHub deployment environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [Apple capture authorization](https://developer.apple.com/documentation/avfoundation/requesting-authorization-to-capture-and-save-media)
- [Apple Input Monitoring](https://support.apple.com/en-lamr/guide/mac-help/mchl4cedafb6/mac)
- [Apple application activation](https://developer.apple.com/documentation/appkit/nsapplication/activationoptions/activateignoringotherapps)
- [Apple notarisation](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
- [Apple universal binary guidance](https://developer.apple.com/documentation/apple-silicon/building-a-universal-macos-binary)
- [XDG GlobalShortcuts portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html)
- [Freedesktop Desktop Entry Specification](https://specifications.freedesktop.org/desktop-entry/latest/)
- [Microsoft Windows code-signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)
