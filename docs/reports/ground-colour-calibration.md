# Ground colour calibration

## Recovery status

The durable round-three copy contained 60 quarter folders but only 31 eligible
captures (28 previously scored and three calibration abstentions). An offline
union of retained native session and capture catalogues independently re-audited
all quarters. All 29 incomplete quarters still lacked required metadata or terminal
payloads; none became eligible through relocation alone.

A native, screenshot-free recovery pass restored 22 quarters in 289.5 browser
seconds. After the browser allocation was raised to ten minutes, a second pass
restored the seven remaining quarters. The portable `reference-inventory.json`
now records all 60 as eligible; OCI independently verified every referenced
response body. Reference data remain private at
`~/turku3d/private/gmaps-data/round3/` and
`/srv/turku3d/private/gmaps-data/round3/` on OCI. Existing report and decoded-manifest
bytes are preserved when relocating inventory pointers.

The pre-calibration baseline scored 50 quarters before the final seven captures
were restored. The deployed pilot correction was then scored on the full set:
57 scored quarters and the same three calibration abstentions.

| Metric | Pilot quarter support | Pilot median |
| --- | ---: | ---: |
| Ground colour CIE76 ΔE | 57 | 11.52 |
| Roof colour CIE76 ΔE | 50 | 14.85 |
| Exposed-ground height RMSE | 57 | 0.08 m |
| Road height MAE | 57 | 0.06 m |
| Roof height MAE | 52 | 0.21 m |
| Wall colour CIE76 ΔE | 1 | 23.07; facade-kit v4 is unsupported by the private sampler |

Across the 50 quarters shared by the pre-calibration baseline and pilot, matched
reference hashes and ground sample counts were identical. Their rendered ground
median fell from 14.96 to 10.98 ΔE; 42 of 50 quarters improved.

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
gravel and grass. A class needs five independent training tiles. Source-mosaic
residuals need three training tiles, are shrunk towards the surface correction and
are enabled only by training cross-validation. Mosaic identifiers are available
input VRT names, not independently identified original aerial-flight sheets.

Corrections exclude buildings, water and bright aerial paint. Unsupported classes
retain their input. The deployed pilot fit of the original 50 held out 11
tile-disjoint quarters (22%). Its model-predicted holdout median fell from 11.21
to 6.45 ΔE. The full-set residual fit pins those 11 quarters and adds one newly
captured quarter, so all 12 final holdouts (21.1%) remain blind to the pilot.
Training-only validation selected 0.25 residual strength and no mosaic-specific
correction. The 12-quarter holdout material-sample median is predicted to fall
from 6.80 to 6.66 ΔE. The actual paired rendered re-score of the final model is
still outstanding; that prediction is not a rendered-world result.

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
  --label pilot-full-20261029

bash pipeline/env/run.sh python tools/refs/fit_ground_colour.py \
  --coverage "$GMAPS_ROOT/calibration/pilot-full-20261029/coverage.json" \
  --base-model pipeline/turku3d/coarse/ground_colour.json \
  --holdout-policy "$GMAPS_ROOT/calibration/final-ground-20261029/holdout-policy.json" \
  --out "$TURKU3D_WORK/reports/final-ground-fit"

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 bash pipeline/env/run.sh python -m pytest -q \
  tools/refs/test_gmaps_inventory.py tools/refs/test_gmaps_ground_score.py \
  tools/refs/test_fit_ground_colour.py pipeline/tests/test_ground_colour.py
```
