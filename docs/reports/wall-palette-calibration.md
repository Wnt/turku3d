# Wall palette calibration evidence

Draft walls now draw a deterministic colour from the replaceable
`textures/turkutex/wall_palette.json` distributions. Explicit OSM paint, named
paint families and stored AI facades retain precedence. Sampler/table edits enter
the producer and building-visual content hashes; ground, collision and roof stage
producers do not depend on this table.

The original round-three sandbox was deleted during this task. Its raw measurements
were retained before deletion; recovery reached the task's five-browser-minute cap.
Offline reuse brought the recovery inventory to31 eligible references:28 scored,
three calibration abstentions, and29 incomplete references. The full57-quarter
before/after comparison remains blocked on recovering those29 references.

## Integrated corrected fit

The recovered28 scored quarters supply104 independent buildings:78 training and26
held-out register identities. A bounded ground illumination proxy corrects their
reference appearance before fitting. With the effective runtime model, including
material priors and paint precedence, held-out corrected median CIE76 dE falls from
**31.49 to26.80**, and mean dE from **31.51 to28.67**. Raw held-out median dE falls
from32.54 to23.54. These are building pigment predictions; paired scene scores are
reported separately in the sandbox after deployment.

The observed groups have sufficient support for unknown-material walls by era and
in aggregate. Classes and districts without enough measured training buildings use
continuous Lab distributions derived from the authored material palettes, retained
as explicit `priors` in the same JSON. These are labelled as authored priors, not
reported as measured classes. New independently measured rules can replace this
single file. No per-building sampled reference colours ship.

## Retained fit

Before deletion, extraction retained 555 accepted non-trim wall groups from 49 of
the 57 scored quarters. These join to 232 independent register/building identities.
The other quarters contribute no joined qualifying non-trim wall group; they must
still be included in a complete scene comparison. The extractor excludes trim bands,
deduplicates solids sharing a register ID and includes accepted groups below the
scorer's mismatch threshold.

Each building record includes material, construction year/era, use, storeys,
footprint area and cadastral district. Missing attributes remain unknown. Material
is unknown for 171/232 buildings; it is not inferred from the reference appearance.
District comes only from a recorded Finnish cadastral property ID.

SHA256 of `wall-holdout-v1:<register-id>`, first 32 bits modulo five, reserves
49 buildings (21.1%) for evaluation. No colour from these buildings enters the fit.
The JSON contains aggregate Lab means, covariance matrices and 10th/90th
percentiles. Groups back off from material/era/district to material/era, material
and the global prior. District groups require 12 training buildings; broader groups
require eight. Stable SHA256/Box-Muller draws and a fixed 0.25 spread multiplier
give reproducible colours independent of facade index, traversal order or NumPy's
random-distribution implementation. There are no per-building colour overrides.

The earlier full retained **raw appearance** candidate comparison, before illumination correction and material-prior integration, was:

| Split | Buildings | Mean dE before | Mean dE candidate | Median before | Median candidate |
| --- | ---: | ---: | ---: | ---: | ---: |
| Training | 183 | 32.86 | 23.59 | 32.71 | 20.22 |
| Holdout | 49 | 32.63 | 24.57 | 32.48 | 22.57 |

These are building-weighted pigment predictions against retained reference medians,
not shader renders or a deployed57-quarter before/after score.

## Lighting and deployment constraint

The private scorer directly compares matched base-colour RGB. Its wall method
explicitly excludes lighting; the capture owner confirmed that no light-normalised
export exists. Blue shadows and other baked illumination therefore remain in raw
reference wall colours. A lower raw dE alone does not establish a better wall albedo.

`fit_wall_palette.py --normalise` estimates a bounded per-channel linear illumination
gain from geometrically matched open ground, using the retained scorer masks and
excluding source building footprints. It requires 80 usable cells and bounds each
gain to 0.5–2. Directional wall shadows and imagery epochs remain uncertain even
after this correction. The recovered proof quarter has 5,801 supporting ground
cells, with gains 1.20/1.37/1.22. A small 29-building corrected pilot regressed on
its seven-building holdout, so it does not justify a production change.

The larger corrected fit above superseded the small pilot. Runtime integration uses
only measured groups with adequate support, with authored material priors otherwise.
Complete the reference inventory and full57-quarter scene comparison before claiming
area-wide improvement. The facade-characterization and facade-archetypes agents
received the JSON schema and precedence contract.

## Commands

All detailed RGB measurements, model candidates, reference media and rendered scores
remain private sandbox products. Source `../sandbox.env` before these commands.

```sh
# Reproduce the retained full raw fit without the deleted original media.
bash pipeline/env/run.sh python tools/refs/fit_wall_palette.py \
  --measurements "$TURKU3D_WORK/reports/wall-palette/measurements.json" \
  --out "$TURKU3D_WORK/reports/wall-palette-raw-stable"

# Recover only evidence already observed in native sessions and retained round two.
GMAPS_PLAN="$TURKU3D_WORK/gmaps-round3/synthetic/expanded-plan.json" \
  python3 scripts/dev/host_slots.py check -- bash pipeline/env/run.sh \
  python tools/refs/gmaps_reuse_capture.py

# Score eligible retained captures without launching a browser.
GMAPS_PLAN="$TURKU3D_WORK/gmaps-round3/synthetic/expanded-plan.json" \
  python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh \
  python tools/refs/gmaps_synthetic_run.py score

# Fit corrected targets after recovery and save an immutable baseline coverage file.
bash pipeline/env/run.sh python tools/refs/fit_wall_palette.py \
  --coverage "$TURKU3D_WORK/gmaps-round3/synthetic/before-coverage.json" \
  --normalise --out "$TURKU3D_WORK/reports/wall-palette-normalised"

# After finish and deploy-drafts, compare all57.
GMAPS_PLAN="$TURKU3D_WORK/gmaps-round3/synthetic/expanded-plan.json" \
  python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh \
  python tools/refs/rescore_wall_palette.py \
  --coverage "$TURKU3D_WORK/gmaps-round3/synthetic/before-coverage.json" \
  --product-root "$TURKU3D_WORK/gmaps-round3/synthetic" \
  --out "$TURKU3D_WORK/reports/wall-palette-after"
```

The paired scorer requires all 57 measured baseline quarters. It compares medians
on common reference patch IDs, rejects changed reference RGB/manifests and reports
missing support or abstentions separately. It never recaptures a different quarter
or substitutes a prediction for a scene measurement.

For the currently recovered28 quarters, use the same paired command with
`--coverage "$TURKU3D_WORK/gmaps-round3/synthetic/recovered-coverage.json"`
and `--expected-quarters 28`. This cardinality is reported
explicitly; it cannot be presented as the full57-quarter result.
