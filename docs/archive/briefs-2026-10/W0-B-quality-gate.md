# W0-B: Quality gate MVP, fast and selective, plus dead-code cleanup

Read `docs/briefs/COMMON.md` first. You run in your own git worktree.

## Why

Many agents will land changes in parallel. The codebase must not decay into workarounds, dead code and stale flags.
One fast gate decides "green before done", and the same gate runs locally (hooks), in `codex-task land`, and later in
CI. For ideas, read (read-only) `ssh osgallery-dev` `~/kernel-hive/docs/lab/AGENT-CI-EXIT-RULE.md`,
`~/kernel-hive/.claude/hooks/pre-push-gate.sh`, `~/kernel-hive/{ruff.toml,eslint.config.mjs,.shellcheckrc}`,
`~/kernel-hive/spa/{eslint.config.js,knip.json}` and `~/gta6/docs/QUALITY-GATE.md`. **Take ideas only; do not copy
1:1.** Note: gta6 accumulated far too many feature flags. Our flag policy is much stricter (see below).

## Deliverables

1. `scripts/dev/gate [--base REF] [--all] [--list] [--json]`:
   - It works out the changed paths (base default `main`, plus staged, unstaged and untracked-not-ignored files in a
     worktree) and maps them to stages through one declarative table, for example `scripts/dev/gate.toml`.
   - It runs only the relevant stages, **in parallel**, and prints a per-stage timing table plus the total.
   - Every stage it skips is listed with the reason.
   - Exit codes: 0 = green, 1 = red, 2 = usage.
2. Stages:
   - **TS** (game/, tools/validator/, qa/render, textures node code): ESLint flat config + typescript-eslint with
     `--max-warnings=0`, `tsc -b --noEmit` (incremental), **knip** (unused files, exports and dependencies),
     vitest **related/changed** tests only.
   - **Python** (pipeline/, qa/, textures/): `ruff check` with a sensible rule set (pyflakes, bugbear, unused code,
     import order, pyupgrade), `ruff format --check`, pytest with `-n auto` (pytest-xdist), selecting tests by the
     changed modules (an import graph, or a per-package mapping in gate.toml).
   - **Shell**: shfmt -d + shellcheck (`.shellcheckrc` with documented exceptions only).
   - **Cross-cutting, always run**:
     - a file-size budget (per-extension limits; growth over the limit fails unless the path is listed with a reason)
     - generated-file drift (for example `materials_v1.json` and the TS materials vs `docs/specs/materials.md`)
     - the flag registry check (below)
     - a "no committed binaries over N kB outside allowed dirs" check
   - **Playwright e2e** runs only when a path tagged for it changes. Tag the existing specs by subsystem.
3. **Flag registry** `docs/flags.toml`: each entry has a name, owner, reason, `added_at_merge` (count of first-parent
   commits on main) and `remove_after_merges` (at most 10). The gate fails if:
   - a flag in code is not in the registry
   - a registry entry has no use in code
   - a flag has expired
   - there are more than 5 flags in total

   Define what counts as a flag in code: one module per language that reads flags, so the gate can grep reliably.
4. **Hooks**: a pre-commit hook (staged-file scope, very fast lint/format only) and a pre-push hook (the full gate over
   the pushed range). Install them via `scripts/dev/install-hooks` (core.hooksPath). Never block on unrelated
   untracked files.
5. **Speed**: measure, then make it fast. Targets: 5–20 s for a typical single-area change; a docs-only change in
   under 2 s; a full `--all` run reported, not necessarily fast. Use caches (ruff, eslint `--cache`, tsbuildinfo),
   parallelism across the 10 cores, and no network.
6. **Bring the repo to green under the new gate**, fixing the real issues rather than suppressing them. **Delete dead
   and probe code:**
   - `tools/engine-probe/` and `tools/reconstruction/verify/`: keep only what a current make target or test uses;
     the research docs keep their findings. Move anything still useful into the proper package with tests.
   - Unused exports and files reported by knip and ruff.
   - Large binaries committed under `docs/research/*-assets` and `tools/engine-probe/public/assets`: move them to
     `~/turku3d/research/` and reference them from the docs by path. History rewrite is out of scope: list the blobs
     in your result for a later squash.

## Coordination

W0-A (tooling) and W0-C (Mac pipeline) run at the same time. They change mk files, configs and pipeline code, so
expect to rebase on `main` after they land. Do your lint fixes last, after rebasing, so the fixes cover their code too.
`codex-task land` (from W0-A) will call `scripts/dev/gate`. Keep its CLI exactly as described above.

## Acceptance

- `scripts/dev/gate --all` is green on the repo.
- Typical single-file changes in game/ and pipeline/ each finish in ≤ 20 s (print the measured timings in your result).
- Planting an unused export, an unregistered flag, an expired flag, a 2 MB PNG and a `ruff`/`eslint` error each turns
  the gate red. Add tests that do exactly this on a temp git repo.
- The hooks are installed and documented in `scripts/dev/README.md` (shared with W0-A; add your own section).
