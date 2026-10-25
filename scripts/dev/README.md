

## Quality gate (W0-B)

Run from the repository root. The gate never installs packages, fetches Git refs, downloads
assets or calls paid/model services. Python 3.12+, Node 24+, shfmt and ShellCheck must be installed.
Initial dependency setup (network allowed here only):

```sh
make pipeline-bootstrap       # native GDAL/osmium CLI tools; setup only
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e 'pipeline[dev,fetch,lidar]' -e 'textures[post,dev]' -r scripts/dev/requirements.txt
npm ci --no-audit --no-fund
(cd game && npm ci --no-audit --no-fund)
(cd tools/validator && npm ci --no-audit --no-fund)
(cd qa/render && npm ci --no-audit --no-fund)
(cd textures/node && npm ci --no-audit --no-fund)
# QA renderer uses the locally installed Chrome; game e2e also uses Chrome.
scripts/dev/install-hooks
```

```sh
scripts/dev/gate                         # merge-base(main, HEAD) + index + working tree + new files
scripts/dev/gate --base HEAD             # only local changes
scripts/dev/gate --base main --list      # selection and explicit reasons, without executing
scripts/dev/gate --all                   # every stage, including both browser backends
scripts/dev/gate --json                  # JSON report, including command output and durations
```

Exit codes: **0 green, 1 red, 2 usage/invalid base**. Deleted paths still select their owning
stages. Ignored untracked files do not count. JSON goes to stdout with no progress chatter;
text mode prints each completed stage and all skip reasons, then total wall time.

`scripts/dev/gate.toml` is the single routing table. A stage has a name, kind, path triggers,
and either commands or a language runner. `always` stages check budgets, binaries, flag registry
and generated materials on every run. Changes to the gate routing/execution select the whole suite.
The two lint `configs` tables expand lint scope when its configuration changes. Python package
maps select dependent test files; an unmapped module conservatively selects its whole package.
Vitest uses `related --run`; deleted sources and dependency/config changes use the entire unit suite.
Validator Node tests remain Node tests, preserving their cross-language round-trip coverage.

Playwright paths are explicitly tagged in the table. Specs use `@walk`, `@drive`, `@render`,
`@qa`, `@input` and `@pipeline`; the table's `related.tags` selects the affected subsets. A new/unmapped
browser trigger selects all specs. Both WebGPU and WebGL2 run. The pipeline browser fixture is
built from synthetic points and a deterministic RGB texture, without reading raw data.
The long browser stage is deliberately excluded from ordinary pure-core/unit changes.

All caches live in this checkout: `.cache/ruff`, `.cache/eslint`, each TS package's
`.cache/typecheck.tsbuildinfo`, and package-local pytest caches. Temporary files and browser
profiles live under `.cache/tmp`. `TURKU3D_PORT_BASE` selects e2e ports `base` and `base+1`;
QA HTTP tests use `base+2`, dev-server tests use `base+3`, and the two Python fetch
test workers use `base+4` / `base+5`. The default base is 5190; parallel
sandboxes must set their allocated base. The gate bounds pytest `-n auto` to two workers per
package and runs up to six stages concurrently, with numeric-library threads limited to one.
`GATE_PYTHON` can select a prepared Python environment instead of `.venv/bin/python`.
The table's `tool_dirs` adds the prepared native pipeline CLI directories to PATH, preserving
the caller's existing tools first. Missing native dependencies fail; the gate never installs them.

### Git hooks

`install-hooks` sets **worktree-local** `core.hooksPath=.githooks` using Git's worktree config;
sibling worktrees keep their own hook settings. Pre-commit runs only staged-file lint/format
(Python Ruff, JS/TS ESLint, shell shfmt/ShellCheck) in a temporary index snapshot. Partially
staged files are read from the index, never from the working copy. It does not run tests.
Pre-push reads Git's ref update list, checks each pushed commit snapshot against the remote
ancestor (new branches against main), and ignores deletion-only pushes. Unknown remote objects
fail with a diagnostic: fetch them explicitly before retrying. Hooks never fetch automatically.
Unrelated unstaged/untracked files cannot block either hook. Dependencies are linked into the
snapshot; caches and generated outputs stay isolated and are removed afterward.

### A/B flag contract

Default policy: no feature flags. A real temporary A/B experiment must use exactly one literal
name through `featureFlag('name')` (`game/src/core/flags.ts`) or `feature_flag("name")`
(`pipeline/turku3d/flags.py`). Do not alias these functions or construct names dynamically.
Only these adapters may read the reserved settings: browser `?ab.name=1`, Python
`TURKU3D_AB_NAME=1`. Other values are false. Backend selection, CLI options and material bitmasks
are capabilities/data, not experiments, and do not belong in this registry.

Add `[[flags]]` in `docs/flags.toml` with:

```toml
[[flags]]
name = "curb_solver"
owner = "name/team"
reason = "Compare measured curb impulse error between two implementations; remove the loser."
added_at_merge = 42
remove_after_merges = 3
```

Use `git rev-list --first-parent --count main` for `added_at_merge`. It must not be in the future.
The gate expires the flag when that count reaches `added_at_merge + remove_after_merges`;
the duration must be 1–10, and there can be at most five entries. Every entry must have a
production-code call and every call must have an entry. Tests do not keep flags alive.
Remove the losing implementation, flag call and registry entry together.

### Repository budgets and generated files

`budgets.lines` caps source lines by extension. Oversized sources must be split, or listed in
`budgets.exceptions` with a concrete reason (for example a vendored runtime). The binary cap is
512 KiB outside the explicitly allowed runtime asset directories. New untracked files are checked
too, so a new 2 MiB PNG cannot slip through. Research evidence is external, under
`~/turku3d/research/w0-gate/`; [the archive ledger](../../docs/research/ARCHIVED-ASSETS.md)
lists removed paths, byte sizes and historical Git blobs for the final squash.

Generated materials are compared byte-for-byte against the generator without writing files.
Regenerate intentionally with `PYTHONPATH=pipeline .venv/bin/python -m turku3d.col.materials --write`.
Update the generator when removing a dead generated export, then regenerate its outputs.

### Gate tests

```sh
.venv/bin/python -m unittest discover -s scripts/dev/tests -v
```

Disposable Git repos exercise selection, deletion, hook snapshots, usage errors and all six
required planted failures: unused export, unregistered/expired flag, 2 MiB PNG, Ruff and ESLint.
They use the real installed linters and knip; no mock-success lint commands are involved.
