# FORGE-001 — M0 Foundation validation report

- **Milestone:** M0 Foundation
- **Issue:** `FORGE-001: Foundation inspection and safe bootstrap`
- **Date:** 2026-09-24
- **Branch:** `feat/forge-001-foundation`
- **Base commit:** `d286049af64c7dad54f1062101795ec904da6cdd` (`main`, "Initial commit")
- **Report status:** Sanitized for publication. No secrets, tokens, credentials
  or owner-local absolute paths are included.

> **Honesty statement.** Every status below is `PASS`, `FAIL`, `NOT_RUN` or
> `NOT_APPLICABLE` based on a command that was actually executed and an output
> that was actually observed. Nothing is reported as passing because it was
> expected to pass. No test framework was executed because none is installed at
> M0 and none was in scope.

## 1. Environment as observed

### 1.1 Canonical path

| Item | Observed value |
|---|---|
| Canonical path (`pwd -P`) | `$FORGEONE_HOME` (owner-local; redacted) |
| Symlink | No — `os.path.islink()` returned `False`; `realpath` equals the path itself |
| Trailing `U+00A0` / invisible characters | **None.** Byte-level inspection (`xxd`) of the directory name shows ASCII only, terminating `0x41 0x49` ("AI"). A Unicode scan of every entry under the owner's Documents folder found **no** non-ASCII directory names. |
| Directory renamed or duplicated | **No.** The existing directory was used as-is. |

**Result: PASS** (REQ-000-01). The owner's written path was already correct; the
suspected trailing non-breaking space does not exist on this filesystem.

### 1.2 Sync and storage risk

| Item | Observed value |
|---|---|
| iCloud Drive enabled | **No** — `FXICloudDriveEnabled = 0`, `FXICloudDriveDesktop = 0`, `FXICloudDriveDocuments = 0`, `FXICloudLoggedIn = 0` |
| `~/Library/Mobile Documents` | Does not exist |
| `~/Documents` symlinked | No |
| Sync risk for `storage/` | **None.** The project root is purely local. |

**Result: PASS** — no iCloud sync implications for model data.

### 1.3 Hardware and OS

| Item | Observed value |
|---|---|
| OS | macOS 27.0 (build 26A428) |
| Chip | Apple M5 |
| Cores | 10 (4 performance, 6 efficiency) |
| Unified memory | 24 GB |
| Internal SSD | 926 Gi total, **725 Gi available**, 20% used |

**Blueprint comparison:** memory matches. The blueprint's "~700GB SSD" figure is
conservative — the volume is 926 Gi with 725 Gi free, comfortably above the
100–150 GB planning guardrail. No blocking conflict.

### 1.4 Toolchain inventory

| Tool | Status |
|---|---|
| Git | 2.54.0 (Apple Git-157) |
| `git-lfs` | **Not installed** |
| Xcode | 27.0 (27A266a) at `/Applications/Xcode.app/Contents/Developer` |
| Apple clang | 21.0.0, target `arm64-apple-darwin27.0.0` |
| iOS simulators | iOS 27.0 — iPhone 18 Pro, 18 Pro Max, 17e, Air, 17, iPad Pro 13-inch (M5) |
| Java | OpenJDK 21.0.11 (JetBrains Runtime) |
| Android SDK | `~/Library/Android/sdk` (build-tools, emulator, platform-tools, platforms, sources, system-images, licenses) |
| Gradle cache | `~/.gradle` present |
| Python | 3.9.6 only (`/usr/bin/python3`, Xcode-bundled pip 21.2.4) |
| Node / npm | **Not installed** |
| Homebrew | **Not installed** (`/opt/homebrew`, `/usr/local` both absent) |
| Docker | **Not installed** |
| `gh` CLI | **Not installed** |
| Local model runtimes | **None** — no Ollama, llama.cpp or MLX |
| IDEs present | Devin (this agent), Visual Studio Code, Android Studio, Xcode, Sourcetree |
| Agent CLI configs present | `~/.codex`, `~/.gemini`, `~/.copilot`, `~/.codeium`, `~/.devin`, `~/.config/devin`, `~/.config/kilo` |

**Result: PASS** — environment recorded (REQ-000-08 context, blueprint §13).

### 1.5 Unrelated repositories

No customer or unrelated repository was entered, cloned or modified.
`~` is not itself a Git repository, and `~/Documents` is not inside one.

**Result: PASS** — forbidden-action boundary respected.

## 2. Remote repository inspection (non-destructive)

| Item | Observed value |
|---|---|
| URL | `https://github.com/chhagansingh/ForgeOne.git` |
| **Visibility** | **PUBLIC** (not private) |
| Default branch | `main` |
| Default branch commit | `d286049af64c7dad54f1062101795ec904da6cdd` |
| Existing content | `LICENSE` (MIT, Copyright (c) 2026 Chhagan singh), `README.md` (`# ForgeOne`) |
| Repository size | 0 KB |
| Created | 2026-09-24T06:46:56Z |
| Issues enabled | Yes |
| Fork / archived | No / No |

**The remote is NOT empty.** `git ls-remote` returned a real `refs/heads/main`.
The M0 bootstrap therefore follows the "existing default branch" path: work on
`feat/forge-001-foundation` and push that branch. No `main` re-initialisation, no
history rewrite, no force-push.

**Visibility is a publication risk and is flagged to the owner.** Every push to
this remote is a public publication. This bootstrap therefore contains only
ForgeOne-owned documentation and configuration. Planning documents were
sanitized before publication (owner-local absolute paths replaced with
`$FORGEONE_HOME`; the unredacted originals remain local and untracked).

**Result: PASS** (REQ-000-03).

## 3. Authentication and permission status (redacted)

| Method | Result |
|---|---|
| `gh auth status` | **NOT_RUN** — the GitHub CLI is not installed on this host |
| SSH (`ssh -T git@github.com`) | **FAIL** — `Permission denied (publickey)`. The existing `~/.ssh/id_rsa` is not authorized for GitHub; `~/.ssh/config` only defines a host entry for an unrelated internal GitLab host, which was **not** used. |
| macOS Keychain lookup | **No entry found** for `github.com` |
| `credential.helper` | `osxkeychain` (from Apple's system Git config) |
| HTTPS token authentication | **PASS** — an owner-supplied scoped token returned HTTP 200 on the repository API with `permissions: {admin, maintain, push, triage, pull}`. The token value is never written to disk, never embedded in the Git remote URL, and is redacted from all output. |

**Result: PASS** for the purpose of this milestone, with a security action
required by the owner — see §7.

## 4. Checkout reconciliation (non-destructive)

The project root already contained two seed files and was **not** a Git
repository. Reconciliation procedure actually performed:

1. Cloned the authorized remote into a temporary directory (`mktemp -d /tmp/...`).
2. Verified the clone: `main` @ `d286049`, working tree containing `LICENSE` and
   `README.md`, remote `origin` correct.
3. Verified the destination contained **no** `.git`, `LICENSE` or `README.md` —
   therefore no overwrite was possible.
4. Moved `.git/`, `LICENSE` and `README.md` into the project root.
5. Removed only the now-empty temporary directory created in step 1.

| Item | Result |
|---|---|
| Seed files preserved unmodified | **PASS** — both originals byte-identical, timestamps unchanged |
| Existing remote history preserved | **PASS** — `git log` shows `d286049 Initial commit` |
| Existing remote README/LICENSE preserved | **PASS** — unmodified, tracked on `main` |
| `origin` remote | **PASS** — `https://github.com/chhagansingh/ForgeOne.git` (fetch and push) |
| Second checkout / nested `repo/` created | **No** — `PASS` |
| `rm -rf` / force-push / history rewrite | **Not used** — `PASS` |

**Result: PASS** (REQ-000-02, REQ-000-04).

## 5. Scaffold and ignore verification

### 5.1 `git check-ignore` matrix

**Paths that MUST be ignored — 20/20 PASS:**

`storage/`, `storage/models/llm/model.gguf`,
`storage/models/llm/weights.safetensors`, `storage/cache/x.bin`,
`storage/runs/abc/out.json`, `storage/secrets/token.txt`, `.env`, `.env.local`,
`credentials.json`, `id_rsa.pem`, `__pycache__/x.pyc`, `.venv/lib/x`,
`node_modules/x.js`, `DerivedData/x`, `.DS_Store`, `build/out.jar`,
`app-debug.apk`, `out.mp4`, `logs/run.log`, `.pytest_cache/x`.

**Paths that MUST NOT be ignored — 10/10 PASS:**

`README.md`, `AGENTS.md`, `.gitignore`, `docs/architecture.md`,
`docs/decisions/ADR-0001-agent-runtime-evaluation.md`,
`specs/000-foundation/spec.md`, `.github/ISSUE_TEMPLATE/bug_report.md`,
`docs/planning/blueprint-v2-2026-09-24.md`,
`docs/reports/FORGE-001-M0-validation-report.md`, `.env.example`.

**Result: PASS** (REQ-000-05, REQ-000-06).

### 5.2 `storage/` created and excluded

`storage/` exists on disk with the full blueprint subtree:
`models/{llm,image,video,vision,adapters}/`, `cache/`, `runs/`, `artifacts/`,
`worktrees/`, `logs/`, `benchmarks/`, `secrets/`. `storage/secrets/` is `chmod 700`.
No `.gitkeep` was added because the entire tree is Git-ignored — a tracked
placeholder inside an ignored tree would be meaningless.

**Result: PASS** (REQ-000-05).

### 5.3 Sensitive-content scan

A recursive scan of every publishable file for the owner's account name, personal
email addresses, token prefixes, hardware serial, absolute home-directory paths
and private-key headers returned **no matches**.

**Result: PASS** (REQ-000-09).

### 5.4 Markdown structure and link check

All relative Markdown links across the published `.md` files were resolved
against the filesystem. Zero broken links after the validation report was added.

**Result: PASS.**

## 6. Checks that were NOT run (and why)

| Check | Status | Reason |
|---|---|---|
| Automated test suite (`pytest`, unit/integration) | **NOT_RUN** | No test framework is installed and no application code exists at M0. The blueprint schedules `pytest`/format/schema/smoke CI from M2–M3 onward. Claiming a test pass here would be fabricated. |
| `gh auth status` | **NOT_RUN** | `gh` is not installed. Equivalent authentication was verified through the repository API instead. |
| Model download / inference smoke test | **NOT_APPLICABLE** | Explicitly forbidden in FORGE-001. |
| Hermes / OpenHands installation or comparison | **NOT_APPLICABLE** | Deferred to M1 by design; see ADR-0001. |
| iOS / Android build verification | **NOT_APPLICABLE** | No platform code exists yet (M6/M7). |
| CI pipeline execution | **NOT_APPLICABLE** | No workflow files and no CI secrets are configured at M0. |

## 7. Security actions required from the owner

1. **Rotate the access token.** The token used for this push was transmitted
   through a chat session. It should be revoked and re-issued after this
   milestone, whether or not the push succeeds.
2. **Consider making the repository private.** The remote is public. Publishing
   the scaffold is harmless, but all later milestones involve model manifests,
   client-shaped fixtures and potentially sensitive evaluation tasks. Public is
   an intentional choice only if the owner wants the work visible.
3. **Remove the token from any local shell history** if it was typed into a
   terminal manually.

## 8. Delivery

- **Branch:** `feat/forge-001-foundation`
- **Base:** `d286049` on `main`
- **Commit:** the M0 bootstrap commit on this branch (`chore: bootstrap ForgeOne
  foundation`). The commit SHA cannot be embedded in the file it commits; the
  SHA and the push verification result are recorded in the FORGE-001 delivery
  summary and in the branch's pull request.
- **Merge policy:** never auto-merged. `main` remains untouched and protected by
  owner review.

## 9. Identified limitations and dependency decisions

| # | Limitation / decision |
|---|---|
| 1 | **No `gh` CLI.** Pull requests cannot be created from this host. The push is accompanied by a GitHub compare URL for the owner to open the PR manually. Installing `gh` is a follow-up decision, not an M0 action. |
| 2 | **No Homebrew, no Node, no Docker, no Python 3.11+.** Any M1/M2 dependency strategy must either install a package manager (a scoped, owner-approved task) or use per-project virtual environments that require no global changes. This is a real constraint on the Hermes vs OpenHands bake-off, since OpenHands commonly expects Docker. |
| 3 | **Python 3.9.6 only.** System Python is EOL-adjacent for modern tooling. A project-local interpreter is recommended before M2's model gateway. |
| 4 | **No `git-lfs`.** Model manifests and upstream references are used instead of committing checkpoints, so LFS is not required at M0. Revisit only if a small binary artifact must be versioned. |
| 5 | **Repository is public.** All future pushes must pass the same sensitive-content review. |
| 6 | **Hardware is M5 / 24 GB / 725 Gi free** — verified as adequate for the plan, but one heavy model resident at a time remains the rule. |
| 7 | **No test engine yet.** From M2/M3, no milestone may be reported as `PASS` without automated evidence. |

## 10. Requirement trace

| REQ-ID | Status |
|---|---|
| REQ-000-01 Canonical path verified, no duplicate created | PASS |
| REQ-000-02 Checkout at project root, no nested repo | PASS |
| REQ-000-03 Remote inspected non-destructively | PASS |
| REQ-000-04 Seed files preserved | PASS |
| REQ-000-05 `storage/` created and Git-ignored | PASS |
| REQ-000-06 Credentials/artifacts/caches excluded | PASS |
| REQ-000-07 Minimal approved scaffold only | PASS |
| REQ-000-08 Provisional ADR-0001, nothing installed | PASS |
| REQ-000-09 Staged diff reviewed for sensitive content | PASS |
| REQ-000-10 Scoped branch, no direct push, no auto-merge | PASS |
| REQ-000-11 Honest PASS/FAIL/NOT_RUN reporting | PASS |

## 11. Next milestone

`FORGE-002: Hermes vs OpenHands bake-off` (M1). M1 begins only under a separate
scoped instruction. No downloads or installs were performed in FORGE-001, and
none may be inferred from this report.
