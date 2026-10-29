# Ground colour calibration

## Recovery status

The durable round-three copy contained 60 quarter folders but only 31 eligible
captures (28 previously scored and three calibration abstentions). An offline
union of retained native session and capture catalogues independently re-audited
all quarters. All 29 incomplete quarters still lacked required metadata or terminal
payloads; none became eligible through relocation alone.

A native, screenshot-free recovery pass restored 22 quarters in 289.5 browser
seconds. The default five-minute browser allocation stopped the pass with seven
quarters still incomplete. The portable `reference-inventory.json` records 53
eligible and seven incomplete quarters. Reference data remain private at
`~/turku3d/private/gmaps-data/round3/` and
`/srv/turku3d/private/gmaps-data/round3/` on OCI. Existing report and decoded-manifest
bytes are preserved when relocating inventory pointers.

Current-generator scoring uses a separate resumable batch index and immutable,
hash-verified OCI snapshots. Materialization into the invoking sandbox includes
sibling shared textures; only these own-generator inputs enter the scorer's own
build reader. Original capture inventories and raw response bodies remain durable.
Full mesh/UV resolution audits are separate from scoring; metadata completeness
is checked before decoding and independently by the scorer.

## Calibration method

The fitter requires all 57 scored baseline quarters. A deterministic tile-disjoint
split reserves 12 quarters (21.1%, the rounded-up 20% holdout) and trains on 45.
No tile occurs in both splits. Training-only leave-one-tile-out validation selects
the strength of bounded additive D65 Lab corrections for asphalt, paving,
gravel/earth and grass. A class needs five independent training tiles. Source-mosaic
residuals need three training tiles, are shrunk towards the surface correction and
are enabled only by training cross-validation. Mosaic identifiers are available
input VRT names, not independently identified original aerial-flight sheets.

Corrections exclude buildings, water and bright aerial paint. Unsupported classes
retain their input. The production generator does not yet call this correction:
the complete baseline, holdout result and paired scene re-score remain outstanding.
No before/after improvement is claimed from the partial inventory.

The installed private facade sampler supports facade-kit v1-v3. Current v4 batches
abstain from wall colour measurement; unavailable wall support must be reported
separately from measured ground, roof and height metrics.

## Commands

```sh
source ../sandbox.env
export GMAPS_ROOT="$HOME/turku3d/private/gmaps-data/round3"
export GMAPS_PLAN="$GMAPS_ROOT/synthetic/expanded-plan.json"

python3 tools/refs/gmaps_inventory.py --root "$GMAPS_ROOT"

scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- \
  bash pipeline/env/run.sh python tools/refs/gmaps_ground_score.py \
  --label baseline-full-20261029

# Requires the completed 57-quarter current baseline.
bash pipeline/env/run.sh python tools/refs/fit_ground_colour.py \
  --coverage "$GMAPS_ROOT/calibration/baseline-full-20261029/coverage.json" \
  --out "$TURKU3D_WORK/reports/ground-colour-fit"

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 bash pipeline/env/run.sh python -m pytest -q \
  tools/refs/test_gmaps_inventory.py tools/refs/test_gmaps_ground_score.py \
  tools/refs/test_fit_ground_colour.py pipeline/tests/test_ground_colour.py
```
