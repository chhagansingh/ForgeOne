# ForgeOne — IDE Agent First Prompt v2 | FORGE-001

> **Note:** This is the sanitized public copy. Owner-local absolute paths have
> been replaced with the `$FORGEONE_HOME` placeholder and machine-specific
> identifiers removed. The unredacted original is kept locally, untracked.

You are the implementation agent acting as a senior AI systems engineer. Read `ForgeOne_Project_Blueprint_v2_2026-09-24.md` in full first. This prompt supersedes the earlier FORGE-001 prompt's placeholder repository and `~/Developer/ForgeOne` paths. This is a controlled foundation milestone, not a one-shot full-stack install.

## Approved owner-provided configuration

- Remote Git repository: `https://github.com/chhagansingh/ForgeOne.git`
- Intended existing local workspace: `$FORGEONE_HOME`
- IDE workspace: open that real folder; Git checkout must be at the workspace root, not inside a nested `repo/` unless the owner explicitly changes the plan.
- Storage for models, caches, run outputs and worktrees: `$FORGEONE_HOME/storage/`, excluded from Git.
- Hardware baseline: Apple Silicon Mac, 24GB unified RAM, ~700GB SSD; inspect actual specifications and remaining free space.
- Owner authorizes **scoped, validated source commits and pushes to this ForgeOne remote**; no implicit production deployments, protected-branch bypass, external/customer-project changes, or secret/model-weight publication.

IMPORTANT PATH SAFETY: The owner-supplied path may include a trailing invisible non-breaking space (U+00A0). Inspect actual directory names under the owner's Documents folder (e.g., `ls -lb` plus a Unicode-aware inspection if needed), resolve the exact existing directory, and record `pwd -P`. Do not auto-correct its name, create a duplicate lookalike folder, or assume a literal trailing U+00A0 is intentional. Store this verified path as FORGEONE_ROOT and quote it in every command. If ambiguous, stop with exact findings before writing.

## Stage A — factual inspection (no mutations)

1. Inspect the existing local folder, `.git` status, remotes, branch, tracked/untracked files, actual filesystem path and iCloud/sync/storage implications.
2. Inspect remote non-destructively (`git ls-remote`, repo metadata and default branch if authorized). Verify `gh auth status` or equivalent without printing tokens. Report repo visibility; do NOT assume it is private or empty.
3. Inventory OS, Apple chip, free space, Git, Python, Node, Xcode/Android tooling, installed AI IDE, possible agent runtimes and dependencies. Do not enter or alter customer repositories.
4. Compare Hermes and OpenHands based on evidence, record provisional ADR and M1 bake-off criteria. Do not install either in M0.
5. Identify any conflicts between actual state and the blueprint. Report blocking conflict rather than overwriting anything.

## Stage B — safe M0 bootstrap (only if Stage A proves the path/repository unambiguous)

1. Make the actual project root the Git checkout. If it is empty, clone the authorized remote into the existing directory. If initialized already, reuse its history. If nonempty and not Git (possibly because the blueprint and this prompt were copied into the folder), preserve seed files and reconcile safely; stop if overwriting/merging risk exists. Prefer the owner attaching these documents in IDE chat rather than copying them into an uninitialized root. No second project checkout and no force pushes.
2. Preserve existing README/licence/default branch. Create only minimal approved scaffold: `.gitignore`, `AGENTS.md`, `README.md` updates if needed, `docs/architecture.md`, `docs/decisions/ADR-0001-agent-runtime-evaluation.md`, `specs/000-foundation/`, `.github/ISSUE_TEMPLATE/`, and a sanitized M0 validation report. Keep optional empty directories via `.gitkeep` only where useful.
3. Create `storage/` if appropriate, but ignore `storage/` entirely in Git. Ensure `.env`, credential files, checkpoints, caches, virtualenvs, build outputs and generated artifacts are excluded. Never stage them.
4. Keep agent tool ownership explicit: existing IDE agent owns this bootstrap; do not start another nested autonomous editor. Never claim the future `forge-auto` endpoint exists.
5. Perform actual checks: correct remote, root, `git status`, `.gitignore` tests (`git check-ignore`), no suspicious staged files, Markdown/link structure sanity, and review the full staged diff. Record checks honestly with PASS/FAIL/NOT_RUN. No invented test execution.

## Stage C — Git delivery (only after validation)

- If remote is truly empty, establish its initial `main` bootstrap in the usual non-destructive way; otherwise create `feat/forge-001-foundation` from the existing default branch and push that branch for PR review. Do not modify/rewrite remote history or force-push.
- Use a focused commit message such as `chore: bootstrap ForgeOne foundation`.
- Push only validated, non-sensitive ForgeOne-owned source/docs. Confirm remote branch/commit exists after push. Never auto-merge a feature PR.
- If authentication is absent or remote/branch permissions block pushing, retain the local commit and report PUSH BLOCKED with the exact safe next step. Do not ask for passwords or print credentials.

## Forbidden in FORGE-001

No heavyweight model downloads, frameworks installed without a follow-up scoped task, global Homebrew/Python/system setting changes, `sudo`, arbitrary downloaded shell scripts, deleting/replacing existing files or histories, access to existing client work, public-port exposure, CI secrets setup, destructive commands, or claims that ForgeOne is already operational.

## Required final report

1. Canonical exact local path (including any Unicode ambiguity), free space/sync risk.
2. Remote URL, visibility, actual default branch, authentication/permissions status (redacted).
3. Existing vs newly created files and key architecture decisions.
4. Commands/checks executed, results and evidence.
5. Git branch, commit SHA, successful remote push/PR URL if available; otherwise explicit PUSH BLOCKED.
6. Identified limitations, dependency decisions, and next milestone `FORGE-002: Hermes vs OpenHands bake-off`.

Stop after FORGE-001. Do not proceed to M1 downloads/installs without the next task instruction. Never hide failures or report unexecuted checks as passed.
