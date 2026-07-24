[Skip to content](https://github.com/OpenAdaptAI/OpenAdapt#start-of-content)

You signed in with another tab or window. [Reload](https://github.com/OpenAdaptAI/OpenAdapt) to refresh your session.You signed out in another tab or window. [Reload](https://github.com/OpenAdaptAI/OpenAdapt) to refresh your session.You switched accounts on another tab or window. [Reload](https://github.com/OpenAdaptAI/OpenAdapt) to refresh your session.Dismiss alert

{{ message }}

### Uh oh!

There was an error while loading. [Please reload this page](https://github.com/OpenAdaptAI/OpenAdapt).

[OpenAdaptAI](https://github.com/OpenAdaptAI)/ **[OpenAdapt](https://github.com/OpenAdaptAI/OpenAdapt)** Public

- [Sponsor](https://github.com/sponsors/OpenAdaptAI)
- [Notifications](https://github.com/login?return_to=%2FOpenAdaptAI%2FOpenAdapt) You must be signed in to change notification settings
- [Fork\\
257](https://github.com/login?return_to=%2FOpenAdaptAI%2FOpenAdapt)
- [Star\\
1.6k](https://github.com/login?return_to=%2FOpenAdaptAI%2FOpenAdapt)


main

[**77** Branches](https://github.com/OpenAdaptAI/OpenAdapt/branches) [**118** Tags](https://github.com/OpenAdaptAI/OpenAdapt/tags)

[Go to Branches page](https://github.com/OpenAdaptAI/OpenAdapt/branches)[Go to Tags page](https://github.com/OpenAdaptAI/OpenAdapt/tags)

Go to file

Code

Open more actions menu

## Folders and files

| Name | Name | Last commit message | Last commit date |
| --- | --- | --- | --- |
| ## Latest commit<br>![author](https://github.githubassets.com/images/gravatars/gravatar-user-420.png?size=40)<br>OpenAdapt Bot<br>[1.2.5](https://github.com/OpenAdaptAI/OpenAdapt/commit/666f71633da247faa2b7fc1cbf28429d4dc9ce98)<br>Open commit detailssuccess<br>last monthJun 13, 2026<br>[666f716](https://github.com/OpenAdaptAI/OpenAdapt/commit/666f71633da247faa2b7fc1cbf28429d4dc9ce98) · last monthJun 13, 2026<br>## History<br>[1,013 Commits](https://github.com/OpenAdaptAI/OpenAdapt/commits/main/) <br>Open commit details<br>[View commit history for this file.](https://github.com/OpenAdaptAI/OpenAdapt/commits/main/) 1,013 Commits |
| [.github](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/.github ".github") | [.github](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/.github ".github") | [fix: phantom sibling exports, headless version/doctor; check all seam…](https://github.com/OpenAdaptAI/OpenAdapt/commit/e31c3e9ada705752f8649eea9bc322986bc09937 "fix: phantom sibling exports, headless version/doctor; check all seams in CI (#1003)  * fix: phantom grounding/retrieval exports, headless version/doctor; check all sibling seams  Extending the #999 guards to every sibling seam the meta-package touches. The extended checks immediately found more live bugs, all fixed here:  - openadapt/__init__.py lazy __getattr__ exported Grounder,   OmniGrounder, and GeminiGrounder from openadapt-grounding and   DemoRetriever/DemoLibrary from openadapt-retrieval. None of these   five names exist in those packages; every one always raised.   Replaced with the real exports (ElementLocator, OmniParserClient,   MultimodalDemoRetriever) and pointed DemoLibrary at openadapt-evals   where it actually lives. - `openadapt version` imported each sibling package to read   __version__. Importing openadapt-capture takes a screenshot at   module scope (recorder.py), which crashes headless environments   including CI. Now reads importlib.metadata instead of executing   package code. Same fix for `openadapt doctor` (find_spec instead of   __import__). - tests/test_import_integrity.py: EXTERNAL_PACKAGES extended from just   openadapt_ml to all six sibling packages. - CI installs all six siblings so every seam is checked on every PR.  The capture-side import side effect (screenshot at module scope) gets its own fix in openadapt-capture.  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>  * test: give the CI sibling-install check teeth  test_external_packages_installed_in_ci was a no-op: it only skipped or passed, so it could never fail and verified nothing. Convert it to a CI-gated assertion that fails if any sibling package is missing - which is the actual risk worth guarding (seam tests silently degrading to skips = the false-green failure mode #999 was about).  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>  ---------  Co-authored-by: Claude Fable 5 <noreply@anthropic.com>") | last monthJun 13, 2026 |
| [docs](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/docs "docs") | [docs](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/docs "docs") | [docs: reframe positioning with multi-pillar strategy (](https://github.com/OpenAdaptAI/OpenAdapt/commit/8ed98a73651f3f5d0e493e2d2172e04ba8c55f67 "docs: reframe positioning with multi-pillar strategy (#991)  * docs: reframe positioning with multi-pillar strategy and honest scoping  - README: Replace \"Demo-Conditioned Prompting\" with \"Trajectory-Conditioned   Disambiguation\" showing the 2x2 experimental matrix (prompting validated,   fine-tuning in progress). Add OpenCUA industry validation. - Landing page strategy: Lead with capture-to-deployment pipeline, add   specialization pillar, update competitor table for March 2026 landscape   (Agent S3, OpenCUA, Browser Use, CUA/Bytebot). Add honesty notes for   proof points.  Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>  * fix: correct OpenCUA attribution to macOS a11y code reuse  OpenCUA reused OpenAdapt's macOS accessibility tree capture code (AX API traversal functions + oa_atomacos dependency), not the full capture-to-deployment pipeline. The recorder architecture came from DuckTrack. Updated README, landing page strategy, competitor table, and proof points to reflect this accurately.  Evidence: arxiv.org/html/2508.09123v3 Section 2.2, OpenCUA README \"Acknowledge\" section.  Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>  * fix: review fixes — accuracy, claims, and add builders section  - Use 46.7% consistently (not 33-47% range) - Change \"core goal\" to \"planned\" in 2x2 matrix - Drop \"superhuman\" for Agent S3 (barely above human baseline) - Fix possessive \"our\" to \"OpenAdapt's\" in competitor table - Add \"Built for Builders\" section for non-technical users - Renumber subsequent sections  Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>  ---------  Co-authored-by: Claude Opus 4.6 <noreply@anthropic.com>") [#991](https://github.com/OpenAdaptAI/OpenAdapt/pull/991) [)](https://github.com/OpenAdaptAI/OpenAdapt/commit/8ed98a73651f3f5d0e493e2d2172e04ba8c55f67 "docs: reframe positioning with multi-pillar strategy (#991)  * docs: reframe positioning with multi-pillar strategy and honest scoping  - README: Replace \"Demo-Conditioned Prompting\" with \"Trajectory-Conditioned   Disambiguation\" showing the 2x2 experimental matrix (prompting validated,   fine-tuning in progress). Add OpenCUA industry validation. - Landing page strategy: Lead with capture-to-deployment pipeline, add   specialization pillar, update competitor table for March 2026 landscape   (Agent S3, OpenCUA, Browser Use, CUA/Bytebot). Add honesty notes for   proof points.  Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>  * fix: correct OpenCUA attribution to macOS a11y code reuse  OpenCUA reused OpenAdapt's macOS accessibility tree capture code (AX API traversal functions + oa_atomacos dependency), not the full capture-to-deployment pipeline. The recorder architecture came from DuckTrack. Updated README, landing page strategy, competitor table, and proof points to reflect this accurately.  Evidence: arxiv.org/html/2508.09123v3 Section 2.2, OpenCUA README \"Acknowledge\" section.  Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>  * fix: review fixes — accuracy, claims, and add builders section  - Use 46.7% consistently (not 33-47% range) - Change \"core goal\" to \"planned\" in 2x2 matrix - Drop \"superhuman\" for Agent S3 (barely above human baseline) - Fix possessive \"our\" to \"OpenAdapt's\" in competitor table - Add \"Built for Builders\" section for non-technical users - Renumber subsequent sections  Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>  ---------  Co-authored-by: Claude Opus 4.6 <noreply@anthropic.com>") | 4 months agoMar 3, 2026 |
| [examples](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/examples "examples") | [examples](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/examples "examples") | [feat: add MkDocs documentation and config module (](https://github.com/OpenAdaptAI/OpenAdapt/commit/f4e0c148637618d3c890aafd20a6fe4b7043f8d1 "feat: add MkDocs documentation and config module (#964)  - Set up MkDocs Material for docs.openadapt.ai - Add unified config module with pydantic-settings - Add example scripts for capture-train-eval and demo-retrieval workflows - Add GitHub Actions workflow for automatic docs deployment") [#964](https://github.com/OpenAdaptAI/OpenAdapt/pull/964) [)](https://github.com/OpenAdaptAI/OpenAdapt/commit/f4e0c148637618d3c890aafd20a6fe4b7043f8d1 "feat: add MkDocs documentation and config module (#964)  - Set up MkDocs Material for docs.openadapt.ai - Add unified config module with pydantic-settings - Add example scripts for capture-train-eval and demo-retrieval workflows - Add GitHub Actions workflow for automatic docs deployment") | 6 months agoJan 16, 2026 |
| [legacy](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/legacy "legacy") | [legacy](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/legacy "legacy") | [ci: simplify release workflow for meta-package architecture](https://github.com/OpenAdaptAI/OpenAdapt/commit/98942162279db0e6bddbc33842ea03abe4164e55 "ci: simplify release workflow for meta-package architecture  Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>") | 6 months agoJan 16, 2026 |
| [openadapt](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/openadapt "openadapt") | [openadapt](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/openadapt "openadapt") | [fix: phantom sibling exports, headless version/doctor; check all seam…](https://github.com/OpenAdaptAI/OpenAdapt/commit/e31c3e9ada705752f8649eea9bc322986bc09937 "fix: phantom sibling exports, headless version/doctor; check all seams in CI (#1003)  * fix: phantom grounding/retrieval exports, headless version/doctor; check all sibling seams  Extending the #999 guards to every sibling seam the meta-package touches. The extended checks immediately found more live bugs, all fixed here:  - openadapt/__init__.py lazy __getattr__ exported Grounder,   OmniGrounder, and GeminiGrounder from openadapt-grounding and   DemoRetriever/DemoLibrary from openadapt-retrieval. None of these   five names exist in those packages; every one always raised.   Replaced with the real exports (ElementLocator, OmniParserClient,   MultimodalDemoRetriever) and pointed DemoLibrary at openadapt-evals   where it actually lives. - `openadapt version` imported each sibling package to read   __version__. Importing openadapt-capture takes a screenshot at   module scope (recorder.py), which crashes headless environments   including CI. Now reads importlib.metadata instead of executing   package code. Same fix for `openadapt doctor` (find_spec instead of   __import__). - tests/test_import_integrity.py: EXTERNAL_PACKAGES extended from just   openadapt_ml to all six sibling packages. - CI installs all six siblings so every seam is checked on every PR.  The capture-side import side effect (screenshot at module scope) gets its own fix in openadapt-capture.  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>  * test: give the CI sibling-install check teeth  test_external_packages_installed_in_ci was a no-op: it only skipped or passed, so it could never fail and verified nothing. Convert it to a CI-gated assertion that fails if any sibling package is missing - which is the actual risk worth guarding (seam tests silently degrading to skips = the false-green failure mode #999 was about).  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>  ---------  Co-authored-by: Claude Fable 5 <noreply@anthropic.com>") | last monthJun 13, 2026 |
| [tests](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/tests "tests") | [tests](https://github.com/OpenAdaptAI/OpenAdapt/tree/main/tests "tests") | [fix: phantom sibling exports, headless version/doctor; check all seam…](https://github.com/OpenAdaptAI/OpenAdapt/commit/e31c3e9ada705752f8649eea9bc322986bc09937 "fix: phantom sibling exports, headless version/doctor; check all seams in CI (#1003)  * fix: phantom grounding/retrieval exports, headless version/doctor; check all sibling seams  Extending the #999 guards to every sibling seam the meta-package touches. The extended checks immediately found more live bugs, all fixed here:  - openadapt/__init__.py lazy __getattr__ exported Grounder,   OmniGrounder, and GeminiGrounder from openadapt-grounding and   DemoRetriever/DemoLibrary from openadapt-retrieval. None of these   five names exist in those packages; every one always raised.   Replaced with the real exports (ElementLocator, OmniParserClient,   MultimodalDemoRetriever) and pointed DemoLibrary at openadapt-evals   where it actually lives. - `openadapt version` imported each sibling package to read   __version__. Importing openadapt-capture takes a screenshot at   module scope (recorder.py), which crashes headless environments   including CI. Now reads importlib.metadata instead of executing   package code. Same fix for `openadapt doctor` (find_spec instead of   __import__). - tests/test_import_integrity.py: EXTERNAL_PACKAGES extended from just   openadapt_ml to all six sibling packages. - CI installs all six siblings so every seam is checked on every PR.  The capture-side import side effect (screenshot at module scope) gets its own fix in openadapt-capture.  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>  * test: give the CI sibling-install check teeth  test_external_packages_installed_in_ci was a no-op: it only skipped or passed, so it could never fail and verified nothing. Convert it to a CI-gated assertion that fails if any sibling package is missing - which is the actual risk worth guarding (seam tests silently degrading to skips = the false-green failure mode #999 was about).  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>  ---------  Co-authored-by: Claude Fable 5 <noreply@anthropic.com>") | last monthJun 13, 2026 |
| [.gitignore](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/.gitignore ".gitignore") | [.gitignore](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/.gitignore ".gitignore") | [refactor: Convert to meta-package architecture (](https://github.com/OpenAdaptAI/OpenAdapt/commit/c1efbeceb5e9f3f960f30a672a154d345b585f7f "refactor: Convert to meta-package architecture (#960)  * refactor: Convert to meta-package architecture  - Move all legacy code to legacy/ directory - Create new openadapt/ as meta-package with lazy imports - Add unified CLI (openadapt capture/train/eval/serve) - Remove openadapt-ml submodule (now a PyPI dependency) - Configure hatchling build with optional extras:   - pip install openadapt[capture] for GUI capture   - pip install openadapt[ml] for ML models   - pip install openadapt[privacy] for PII scrubbing   - pip install openadapt[all] for everything - Update .gitignore to exclude secrets and temp files  Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>  * ci: fix workflow and lint issues for meta-package  - Update GitHub Actions workflow to use pip install instead of Poetry - Replace shell script with modern pip + ruff workflow - Add ubuntu-latest and Python 3.11/3.12 to CI matrix - Fix ruff linting issues (unused imports, formatting, spacing) - Sort imports alphabetically with noqa comments  Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>  ---------  Co-authored-by: Claude Opus 4.5 <noreply@anthropic.com>") [#960](https://github.com/OpenAdaptAI/OpenAdapt/pull/960) [)](https://github.com/OpenAdaptAI/OpenAdapt/commit/c1efbeceb5e9f3f960f30a672a154d345b585f7f "refactor: Convert to meta-package architecture (#960)  * refactor: Convert to meta-package architecture  - Move all legacy code to legacy/ directory - Create new openadapt/ as meta-package with lazy imports - Add unified CLI (openadapt capture/train/eval/serve) - Remove openadapt-ml submodule (now a PyPI dependency) - Configure hatchling build with optional extras:   - pip install openadapt[capture] for GUI capture   - pip install openadapt[ml] for ML models   - pip install openadapt[privacy] for PII scrubbing   - pip install openadapt[all] for everything - Update .gitignore to exclude secrets and temp files  Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>  * ci: fix workflow and lint issues for meta-package  - Update GitHub Actions workflow to use pip install instead of Poetry - Replace shell script with modern pip + ruff workflow - Add ubuntu-latest and Python 3.11/3.12 to CI matrix - Fix ruff linting issues (unused imports, formatting, spacing) - Sort imports alphabetically with noqa comments  Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>  ---------  Co-authored-by: Claude Opus 4.5 <noreply@anthropic.com>") | 6 months agoJan 16, 2026 |
| [CHANGELOG.md](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/CHANGELOG.md "CHANGELOG.md") | [CHANGELOG.md](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/CHANGELOG.md "CHANGELOG.md") | [1.2.5](https://github.com/OpenAdaptAI/OpenAdapt/commit/666f71633da247faa2b7fc1cbf28429d4dc9ce98 "1.2.5  Automatically generated by python-semantic-release") | last monthJun 13, 2026 |
| [CLAUDE.md](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/CLAUDE.md "CLAUDE.md") | [CLAUDE.md](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/CLAUDE.md "CLAUDE.md") | [Add CLAUDE.md with development guidelines](https://github.com/OpenAdaptAI/OpenAdapt/commit/96089216994a3173dccbfe077681c8847249480c "Add CLAUDE.md with development guidelines  - Emphasizes always using PRs instead of direct pushes to main - Documents repository structure and sub-packages - Includes development setup instructions  Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>") | 6 months agoJan 16, 2026 |
| [CONTRIBUTING.md](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/CONTRIBUTING.md "CONTRIBUTING.md") | [CONTRIBUTING.md](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/CONTRIBUTING.md "CONTRIBUTING.md") | [chore: add CODEOWNERS, CONTRIBUTING, and dependabot config](https://github.com/OpenAdaptAI/OpenAdapt/commit/33e7998accbec35615582c77c26bfe641057c4c5 "chore: add CODEOWNERS, CONTRIBUTING, and dependabot config  - CODEOWNERS: @abrichr as default reviewer - dependabot.yml: weekly pip and github-actions updates - CONTRIBUTING.md: contributor guide with architecture overview  Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>") | 6 months agoJan 16, 2026 |
| [LICENSE](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/LICENSE "LICENSE") | [LICENSE](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/LICENSE "LICENSE") | [Update LICENSE (](https://github.com/OpenAdaptAI/OpenAdapt/commit/97478b7369602d90655b3a211c97fe35d39c5403 "Update LICENSE (#505)") [#505](https://github.com/OpenAdaptAI/OpenAdapt/pull/505) [)](https://github.com/OpenAdaptAI/OpenAdapt/commit/97478b7369602d90655b3a211c97fe35d39c5403 "Update LICENSE (#505)") | 3 years agoOct 19, 2023 |
| [README.md](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/README.md "README.md") | [README.md](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/README.md "README.md") | [fix: add inline caveat to 100% accuracy claim in README table (](https://github.com/OpenAdaptAI/OpenAdapt/commit/96907a365de430e6918739d5117daff143053644 "fix: add inline caveat to 100% accuracy claim in README table (#997)  The table cell now reads \"100% first-action (n=45, shared entry point)\" instead of just \"100% (validated, n=45)\" so the limitation is visible even when the table is read without the surrounding paragraph.  Co-authored-by: Claude Opus 4.6 <noreply@anthropic.com>") [#997](https://github.com/OpenAdaptAI/OpenAdapt/pull/997) [)](https://github.com/OpenAdaptAI/OpenAdapt/commit/96907a365de430e6918739d5117daff143053644 "fix: add inline caveat to 100% accuracy claim in README table (#997)  The table cell now reads \"100% first-action (n=45, shared entry point)\" instead of just \"100% (validated, n=45)\" so the limitation is visible even when the table is read without the surrounding paragraph.  Co-authored-by: Claude Opus 4.6 <noreply@anthropic.com>") | 4 months agoMar 3, 2026 |
| [mkdocs.yml](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/mkdocs.yml "mkdocs.yml") | [mkdocs.yml](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/mkdocs.yml "mkdocs.yml") | [fix: Add missing pages to MkDocs nav configuration](https://github.com/OpenAdaptAI/OpenAdapt/commit/9347117cbf56e08f266f06f6efb13e73f9bb7931 "fix: Add missing pages to MkDocs nav configuration  Added to nav: - Architecture Evolution document - Design docs (tray, telemetry, landing page, etc.) - Roadmap docs (priorities, publications) - macOS permissions reference - Legacy freeze documents  Fixes MkDocs strict mode warnings about orphaned pages.  Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>") | 6 months agoJan 16, 2026 |
| [pyproject.toml](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/pyproject.toml "pyproject.toml") | [pyproject.toml](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/pyproject.toml "pyproject.toml") | [1.2.5](https://github.com/OpenAdaptAI/OpenAdapt/commit/666f71633da247faa2b7fc1cbf28429d4dc9ce98 "1.2.5  Automatically generated by python-semantic-release") | last monthJun 13, 2026 |
| [uv.lock](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/uv.lock "uv.lock") | [uv.lock](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/uv.lock "uv.lock") | [feat: Update README.md by combining the best changes from branches (](https://github.com/OpenAdaptAI/OpenAdapt/commit/4d073596502224320c1e7dfa4a87dfc6e226645e "feat: Update README.md by combining the best changes from branches (#996)  Co-authored-by: Wright Bot <wright@openadaptai.noreply.github.com>") [#996](https://github.com/OpenAdaptAI/OpenAdapt/pull/996) | 4 months agoMar 3, 2026 |
| View all files |

## Repository files navigation

# OpenAdapt: AI-First Process Automation with Large Multimodal Models (LMMs)

[Permalink: OpenAdapt: AI-First Process Automation with Large Multimodal Models (LMMs)](https://github.com/OpenAdaptAI/OpenAdapt#openadapt-ai-first-process-automation-with-large-multimodal-models-lmms)

[![Build Status](https://github.com/OpenAdaptAI/OpenAdapt/actions/workflows/main.yml/badge.svg)](https://github.com/OpenAdaptAI/OpenAdapt/actions/workflows/main.yml)[![PyPI version](https://camo.githubusercontent.com/72663406e7649502b437e966ffe617e7a36e255169d089dca074515e6a62d08c/68747470733a2f2f696d672e736869656c64732e696f2f707970692f762f6f70656e61646170742e737667)](https://pypi.org/project/openadapt/)[![Downloads](https://camo.githubusercontent.com/3fab96abc07fdb794248deda699103e807f1839544db618567fddd991c604739/68747470733a2f2f696d672e736869656c64732e696f2f707970692f646d2f6f70656e61646170742e737667)](https://pypi.org/project/openadapt/)[![License: MIT](https://camo.githubusercontent.com/fdf2982b9f5d7489dcf44570e714e3a15fce6253e0cc6b5aa61a075aac2ff71b/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f4c6963656e73652d4d49542d79656c6c6f772e737667)](https://opensource.org/licenses/MIT)[![Python 3.10+](https://camo.githubusercontent.com/e801a66299d2c15286fe0fee660d9ffa666c1c5576e5e1536acc07b49ce8ac8f/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f707974686f6e2d332e31302532422d626c7565)](https://www.python.org/downloads/)[![Discord](https://camo.githubusercontent.com/a573cac911b5453ac9a829fd14dceac1ffd490366a216a7456f36c7af0b33f44/68747470733a2f2f696d672e736869656c64732e696f2f646973636f72642f313038343438313830343839363337343831343f636f6c6f723d373238396461266c6162656c3d446973636f7264266c6f676f3d646973636f7264266c6f676f436f6c6f723d7768697465)](https://discord.gg/yF527cQbDG)

**OpenAdapt** is the **open** source software **adapt** er between Large Multimodal Models (LMMs) and traditional desktop and web GUIs.

Record GUI demonstrations, train ML models, and evaluate agents - all from a unified CLI.

[Join us on Discord](https://discord.gg/yF527cQbDG) \| [Documentation](https://docs.openadapt.ai/) \| [OpenAdapt.ai](https://openadapt.ai/)

* * *

## Architecture

[Permalink: Architecture](https://github.com/OpenAdaptAI/OpenAdapt#architecture)

OpenAdapt v1.0+ uses a **modular meta-package architecture**. The main `openadapt` package provides a unified CLI and depends on focused sub-packages via PyPI:

| Package | Description | Repository |
| --- | --- | --- |
| `openadapt` | Meta-package with unified CLI | This repo |
| `openadapt-capture` | Event recording and storage | [openadapt-capture](https://github.com/OpenAdaptAI/openadapt-capture) |
| `openadapt-ml` | ML engine, training, inference | [openadapt-ml](https://github.com/OpenAdaptAI/openadapt-ml) |
| `openadapt-evals` | Benchmark evaluation | [openadapt-evals](https://github.com/OpenAdaptAI/openadapt-evals) |
| `openadapt-viewer` | HTML visualization | [openadapt-viewer](https://github.com/OpenAdaptAI/openadapt-viewer) |
| `openadapt-grounding` | UI element localization | [openadapt-grounding](https://github.com/OpenAdaptAI/openadapt-grounding) |
| `openadapt-retrieval` | Multimodal demo retrieval | [openadapt-retrieval](https://github.com/OpenAdaptAI/openadapt-retrieval) |
| `openadapt-privacy` | PII/PHI scrubbing | [openadapt-privacy](https://github.com/OpenAdaptAI/openadapt-privacy) |
| `openadapt-wright` | Dev automation | [openadapt-wright](https://github.com/OpenAdaptAI/openadapt-wright) |
| `openadapt-herald` | Social media from git history | [openadapt-herald](https://github.com/OpenAdaptAI/openadapt-herald) |
| `openadapt-crier` | Telegram approval bot | [openadapt-crier](https://github.com/OpenAdaptAI/openadapt-crier) |
| `openadapt-consilium` | Multi-model consensus | [openadapt-consilium](https://github.com/OpenAdaptAI/openadapt-consilium) |
| `openadapt-desktop` | Desktop GUI application | [openadapt-desktop](https://github.com/OpenAdaptAI/openadapt-desktop) |
| `openadapt-tray` | System tray app | [openadapt-tray](https://github.com/OpenAdaptAI/openadapt-tray) |
| `openadapt-agent` | Production execution engine | [openadapt-agent](https://github.com/OpenAdaptAI/openadapt-agent) |
| `openadapt-telemetry` | Error tracking | [openadapt-telemetry](https://github.com/OpenAdaptAI/openadapt-telemetry) |

* * *

## Installation

[Permalink: Installation](https://github.com/OpenAdaptAI/OpenAdapt#installation)

Install what you need:

```
pip install openadapt              # Minimal CLI only
pip install openadapt[capture]     # GUI capture/recording
pip install openadapt[ml]          # ML training and inference
pip install openadapt[evals]       # Benchmark evaluation
pip install openadapt[privacy]     # PII/PHI scrubbing
pip install openadapt[all]         # Everything
```

**Requirements:** Python 3.10+

* * *

## Quick Start

[Permalink: Quick Start](https://github.com/OpenAdaptAI/OpenAdapt#quick-start)

### 1\. Record a demonstration

[Permalink: 1. Record a demonstration](https://github.com/OpenAdaptAI/OpenAdapt#1-record-a-demonstration)

```
openadapt capture start --name my-task
# Perform actions in your GUI, then press Ctrl+C to stop
```

### 2\. Train a model

[Permalink: 2. Train a model](https://github.com/OpenAdaptAI/OpenAdapt#2-train-a-model)

```
openadapt train start --capture my-task --model qwen3vl-2b
```

### 3\. Evaluate

[Permalink: 3. Evaluate](https://github.com/OpenAdaptAI/OpenAdapt#3-evaluate)

```
openadapt eval run --checkpoint training_output/model.pt --benchmark waa
```

### 4\. View recordings

[Permalink: 4. View recordings](https://github.com/OpenAdaptAI/OpenAdapt#4-view-recordings)

```
openadapt capture view my-task
```

* * *

## Ecosystem

[Permalink: Ecosystem](https://github.com/OpenAdaptAI/OpenAdapt#ecosystem)

### Core Platform Components

[Permalink: Core Platform Components](https://github.com/OpenAdaptAI/OpenAdapt#core-platform-components)

| Package | Description | Repository |
| --- | --- | --- |
| `openadapt` | Meta-package with unified CLI | This repo |
| `openadapt-capture` | Event recording and storage | [openadapt-capture](https://github.com/OpenAdaptAI/openadapt-capture) |
| `openadapt-ml` | ML engine, training, inference | [openadapt-ml](https://github.com/OpenAdaptAI/openadapt-ml) |
| `openadapt-evals` | Benchmark evaluation | [openadapt-evals](https://github.com/OpenAdaptAI/openadapt-evals) |
| `openadapt-viewer` | HTML visualization | [openadapt-viewer](https://github.com/OpenAdaptAI/openadapt-viewer) |
| `openadapt-grounding` | UI element localization | [openadapt-grounding](https://github.com/OpenAdaptAI/openadapt-grounding) |
| `openadapt-retrieval` | Multimodal demo retrieval | [openadapt-retrieval](https://github.com/OpenAdaptAI/openadapt-retrieval) |
| `openadapt-privacy` | PII/PHI scrubbing | [openadapt-privacy](https://github.com/OpenAdaptAI/openadapt-privacy) |

### Applications and Tools

[Permalink: Applications and Tools](https://github.com/OpenAdaptAI/OpenAdapt#applications-and-tools)

| Package | Description | Repository |
| --- | --- | --- |
| `openadapt-desktop` | Desktop GUI application | [openadapt-desktop](https://github.com/OpenAdaptAI/openadapt-desktop) |
| `openadapt-tray` | System tray app | [openadapt-tray](https://github.com/OpenAdaptAI/openadapt-tray) |
| `openadapt-agent` | Production execution engine | [openadapt-agent](https://github.com/OpenAdaptAI/openadapt-agent) |
| `openadapt-wright` | Dev automation | [openadapt-wright](https://github.com/OpenAdaptAI/openadapt-wright) |
| `openadapt-herald` | Social media from git history | [openadapt-herald](https://github.com/OpenAdaptAI/openadapt-herald) |
| `openadapt-crier` | Telegram approval bot | [openadapt-crier](https://github.com/OpenAdaptAI/openadapt-crier) |
| `openadapt-consilium` | Multi-model consensus | [openadapt-consilium](https://github.com/OpenAdaptAI/openadapt-consilium) |
| `openadapt-telemetry` | Error tracking | [openadapt-telemetry](https://github.com/OpenAdaptAI/openadapt-telemetry) |

* * *

## CLI Reference

[Permalink: CLI Reference](https://github.com/OpenAdaptAI/OpenAdapt#cli-reference)

```
openadapt capture start --name <name>    Start recording
openadapt capture stop                    Stop recording
openadapt capture list                    List captures
openadapt capture view <name>             Open capture viewer

openadapt train start --capture <name>    Train model on capture
openadapt train status                    Check training progress
openadapt train stop                      Stop training

openadapt eval run --checkpoint <path>    Evaluate trained model
openadapt eval run --agent api-claude     Evaluate API agent
openadapt eval mock --tasks 10            Run mock evaluation

openadapt serve --port 8080               Start dashboard server
openadapt version                         Show installed versions
openadapt doctor                          Check system requirements
```

* * *

## How It Works

[Permalink: How It Works](https://github.com/OpenAdaptAI/OpenAdapt#how-it-works)

See the full [Architecture Evolution](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/docs/architecture-evolution.md) for detailed documentation.

### Three-Phase Pipeline

[Permalink: Three-Phase Pipeline](https://github.com/OpenAdaptAI/OpenAdapt#three-phase-pipeline)

OpenAdapt follows a streamlined **Demonstrate → Learn → Execute** pipeline:

**1\. DEMONSTRATE (Observation Collection)**

- **Capture**: Record user actions and screenshots with `openadapt-capture`
- **Privacy**: Scrub PII/PHI from recordings with `openadapt-privacy`
- **Store**: Build a searchable demonstration library

**2\. LEARN (Policy Acquisition)**

- **Retrieval Path**: Embed demonstrations, index them, and enable semantic search
- **Training Path**: Load demonstrations and fine-tune Vision-Language Models (VLMs)
- **Abstraction**: Progress from literal replay to template-based automation

**3\. EXECUTE (Agent Deployment)**

- **Observe**: Take screenshots and gather accessibility information
- **Policy**: Use demonstration context to decide actions via VLMs (Claude, GPT-4o, Qwen3-VL)
- **Ground**: Map intentions to specific UI coordinates with `openadapt-grounding`
- **Act**: Execute validated actions with safety gates
- **Evaluate**: Measure success with `openadapt-evals` and feed results back for improvement

### Core Approach: Trajectory-Conditioned Disambiguation

[Permalink: Core Approach: Trajectory-Conditioned Disambiguation](https://github.com/OpenAdaptAI/OpenAdapt#core-approach-trajectory-conditioned-disambiguation)

Zero-shot VLMs fail on GUI tasks not due to lack of capability, but due to **ambiguity in UI affordances**. OpenAdapt resolves this by conditioning agents on human demonstrations — "show, don't tell."

|  | No Retrieval | With Retrieval |
| --- | --- | --- |
| **No Fine-tuning** | 46.7% (zero-shot baseline) | **100%** first-action (n=45, shared entry point) |
| **Fine-tuning** | Standard SFT (baseline) | **Demo-conditioned FT** (planned) |

The bottom-right cell is OpenAdapt's unique value: training models to **use** demonstrations they haven't seen before, combining retrieval with fine-tuning for maximum accuracy. Phase 2 (retrieval-only prompting) is validated; Phase 3 (demo-conditioned fine-tuning) is in progress.

**Validated result**: On a controlled macOS benchmark (45 System Settings tasks sharing a common navigation entry point), demo-conditioned prompting improved first-action accuracy from 46.7% to 100%. A length-matched control (+11.1 pp only) confirms the benefit is semantic, not token-length. See the [research thesis](https://github.com/OpenAdaptAI/openadapt-ml/blob/main/docs/research_thesis.md) for methodology and the [publication roadmap](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/docs/publication-roadmap.md) for limitations.

**Industry validation**: [OpenCUA](https://github.com/xlang-ai/OpenCUA) (NeurIPS 2025 Spotlight, XLANG Lab) [reused OpenAdapt's macOS accessibility capture code](https://arxiv.org/html/2508.09123v3) in their AgentNetTool, but uses demos only for model training — not runtime conditioning. No open-source CUA framework currently does demo-conditioned inference, which remains OpenAdapt's architectural differentiator.

### Key Concepts

[Permalink: Key Concepts](https://github.com/OpenAdaptAI/OpenAdapt#key-concepts)

- **Policy/Grounding Separation**: The Policy decides _what_ to do; Grounding determines _where_ to do it
- **Safety Gate**: Runtime validation layer before action execution (confirm mode for high-risk actions)
- **Abstraction Ladder**: Progressive generalization from literal replay to goal-level automation
- **Evaluation-Driven Feedback**: Success traces become new training data

* * *

## Terminology

[Permalink: Terminology](https://github.com/OpenAdaptAI/OpenAdapt#terminology)

| Term | Description |
| --- | --- |
| **Observation** | What the agent perceives (screenshot, accessibility tree) |
| **Action** | What the agent does (click, type, scroll, etc.) |
| **Trajectory** | Sequence of observation-action pairs |
| **Demonstration** | Human-provided example trajectory |
| **Policy** | Decision-making component that maps observations to actions |
| **Grounding** | Mapping intent to specific UI elements (coordinates) |

* * *

## Demos

[Permalink: Demos](https://github.com/OpenAdaptAI/OpenAdapt#demos)

**Legacy Version (v0.46.0) Examples:**

- [Twitter Demo](https://twitter.com/abrichr/status/1784307190062342237) \- Early OpenAdapt demonstration
- [Loom Video](https://www.loom.com/share/9d77eb7028f34f7f87c6661fb758d1c0) \- Process automation walkthrough

_Note: These demos show the legacy monolithic version. For current v1.0+ modular architecture examples, see the [documentation](https://docs.openadapt.ai/)._

* * *

## Permissions

[Permalink: Permissions](https://github.com/OpenAdaptAI/OpenAdapt#permissions)

**macOS:** Grant Accessibility, Screen Recording, and Input Monitoring permissions to your terminal. See [permissions guide](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/legacy/permissions_in_macOS.md).

**Windows:** Run as Administrator if needed for input capture.

* * *

## Legacy Version

[Permalink: Legacy Version](https://github.com/OpenAdaptAI/OpenAdapt#legacy-version)

The monolithic OpenAdapt codebase (v0.46.0) is preserved in the `legacy/` directory.

**To use the legacy version:**

```
pip install openadapt==0.46.0
```

See [docs/LEGACY\_FREEZE.md](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/docs/LEGACY_FREEZE.md) for migration guide and details.

* * *

## Contributing

[Permalink: Contributing](https://github.com/OpenAdaptAI/OpenAdapt#contributing)

1. [Join Discord](https://discord.gg/yF527cQbDG)
2. Pick an issue from the relevant sub-package repository
3. Submit a PR

For sub-package development:

```
git clone https://github.com/OpenAdaptAI/openadapt-ml  # or other sub-package
cd openadapt-ml
pip install -e ".[dev]"
```

* * *

## Related Projects

[Permalink: Related Projects](https://github.com/OpenAdaptAI/OpenAdapt#related-projects)

- [OpenAdaptAI/SoM](https://github.com/OpenAdaptAI/SoM) \- Set-of-Mark prompting
- [OpenAdaptAI/pynput](https://github.com/OpenAdaptAI/pynput) \- Input monitoring fork
- [OpenAdaptAI/atomacos](https://github.com/OpenAdaptAI/atomacos) \- macOS accessibility

* * *

## Support

[Permalink: Support](https://github.com/OpenAdaptAI/OpenAdapt#support)

- **Discord:** [https://discord.gg/yF527cQbDG](https://discord.gg/yF527cQbDG)
- **Issues:** Use the relevant sub-package repository
- **Architecture docs:** [GitHub Wiki](https://github.com/OpenAdaptAI/OpenAdapt/wiki/OpenAdapt-Architecture-(draft))

* * *

## License

[Permalink: License](https://github.com/OpenAdaptAI/OpenAdapt#license)

MIT License - see [LICENSE](https://github.com/OpenAdaptAI/OpenAdapt/blob/main/LICENSE) for details.

## About

Record a workflow once, compile it into a deterministic, self-healing automation that runs on your own machines. Open-source demonstration compiler for desktop workflows — local-first, model-optional, audit-ready.


[www.OpenAdapt.AI](https://www.openadapt.ai/ "https://www.OpenAdapt.AI")

### Topics

[python](https://github.com/topics/python "Topic: python") [transformers](https://github.com/topics/transformers "Topic: transformers") [openai](https://github.com/topics/openai "Topic: openai") [desktop-automation](https://github.com/topics/desktop-automation "Topic: desktop-automation") [agents](https://github.com/topics/agents "Topic: agents") [workflow-automation](https://github.com/topics/workflow-automation "Topic: workflow-automation") [gui-automation](https://github.com/topics/gui-automation "Topic: gui-automation") [ai-agents](https://github.com/topics/ai-agents "Topic: ai-agents") [process-automation](https://github.com/topics/process-automation "Topic: process-automation") [local-first](https://github.com/topics/local-first "Topic: local-first") [large-language-models](https://github.com/topics/large-language-models "Topic: large-language-models") [anthropic](https://github.com/topics/anthropic "Topic: anthropic") [ai-agents-framework](https://github.com/topics/ai-agents-framework "Topic: ai-agents-framework") [large-multimodal-models](https://github.com/topics/large-multimodal-models "Topic: large-multimodal-models") [google-gemini](https://github.com/topics/google-gemini "Topic: google-gemini") [large-action-model](https://github.com/topics/large-action-model "Topic: large-action-model") [omniparser](https://github.com/topics/omniparser "Topic: omniparser") [generative-process-automation](https://github.com/topics/generative-process-automation "Topic: generative-process-automation") [computer-use](https://github.com/topics/computer-use "Topic: computer-use") [computer-use-agents](https://github.com/topics/computer-use-agents "Topic: computer-use-agents")

### Resources

[Readme](https://github.com/OpenAdaptAI/OpenAdapt#readme-ov-file)

### License

[MIT license](https://github.com/OpenAdaptAI/OpenAdapt#MIT-1-ov-file)

### Contributing

[Contributing](https://github.com/OpenAdaptAI/OpenAdapt#contributing-ov-file)

### Uh oh!

There was an error while loading. [Please reload this page](https://github.com/OpenAdaptAI/OpenAdapt).

[Activity](https://github.com/OpenAdaptAI/OpenAdapt/activity)

[Custom properties](https://github.com/OpenAdaptAI/OpenAdapt/custom-properties)

### Stars

**1.6k**
stars


### Watchers

**16**
watching


### Forks

[**257**\\
forks](https://github.com/OpenAdaptAI/OpenAdapt/forks)

[Report repository](https://github.com/contact/report-content?content_url=https%3A%2F%2Fgithub.com%2FOpenAdaptAI%2FOpenAdapt&report=OpenAdaptAI+%28user%29)

## [Releases\  117](https://github.com/OpenAdaptAI/OpenAdapt/releases)

[v1.2.5\\
Latest\\
\\
last monthJun 13, 2026](https://github.com/OpenAdaptAI/OpenAdapt/releases/tag/v1.2.5)

[\+ 116 releases](https://github.com/OpenAdaptAI/OpenAdapt/releases)

## Sponsor this project

[![@OpenAdaptAI](https://avatars.githubusercontent.com/u/132681217?s=64&v=4)](https://github.com/OpenAdaptAI)[**OpenAdaptAI** OpenAdapt.AI](https://github.com/OpenAdaptAI)

[Sponsor](https://github.com/sponsors/OpenAdaptAI)

[Learn more about GitHub Sponsors](https://github.com/sponsors)

### Uh oh!

There was an error while loading. [Please reload this page](https://github.com/OpenAdaptAI/OpenAdapt).

## [Contributors\  20](https://github.com/OpenAdaptAI/OpenAdapt/graphs/contributors)

- [![@abrichr](https://avatars.githubusercontent.com/u/774615?s=64&v=4)](https://github.com/abrichr)
- [![@Mustaballer](https://avatars.githubusercontent.com/u/43456930?s=64&v=4)](https://github.com/Mustaballer)
- [![@0dm](https://avatars.githubusercontent.com/u/57018940?s=64&v=4)](https://github.com/0dm)
- [![@dianzrong](https://avatars.githubusercontent.com/u/95876281?s=64&v=4)](https://github.com/dianzrong)
- [![@jesicasusanto](https://avatars.githubusercontent.com/u/87709055?s=64&v=4)](https://github.com/jesicasusanto)
- [![@claude](https://avatars.githubusercontent.com/u/81847?s=64&v=4)](https://github.com/claude)
- [![@KIRA009](https://avatars.githubusercontent.com/u/40872556?s=64&v=4)](https://github.com/KIRA009)
- [![@KrishPatel13](https://avatars.githubusercontent.com/u/65433817?s=64&v=4)](https://github.com/KrishPatel13)
- [![@angelala3252](https://avatars.githubusercontent.com/u/88949118?s=64&v=4)](https://github.com/angelala3252)
- [![@AvidEslami](https://avatars.githubusercontent.com/u/34798076?s=64&v=4)](https://github.com/AvidEslami)
- [![@atineoSE](https://avatars.githubusercontent.com/u/12340433?s=64&v=4)](https://github.com/atineoSE)
- [![@dependabot[bot]](https://avatars.githubusercontent.com/in/29110?s=64&v=4)](https://github.com/apps/dependabot)
- [![@Animesh404](https://avatars.githubusercontent.com/u/60042503?s=64&v=4)](https://github.com/Animesh404)
- [![@mory91](https://avatars.githubusercontent.com/u/13743297?s=64&v=4)](https://github.com/mory91)

[\+ 6 contributors](https://github.com/OpenAdaptAI/OpenAdapt/graphs/contributors)

## Languages

- [Python88.8%](https://github.com/OpenAdaptAI/OpenAdapt/search?l=python)
- [TypeScript5.8%](https://github.com/OpenAdaptAI/OpenAdapt/search?l=typescript)
- [JavaScript2.8%](https://github.com/OpenAdaptAI/OpenAdapt/search?l=javascript)
- [PowerShell1.1%](https://github.com/OpenAdaptAI/OpenAdapt/search?l=powershell)
- [Jinja0.6%](https://github.com/OpenAdaptAI/OpenAdapt/search?l=jinja)
- [Shell0.6%](https://github.com/OpenAdaptAI/OpenAdapt/search?l=shell)
- Other0.3%

You can’t perform that action at this time.