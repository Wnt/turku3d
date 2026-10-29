# Generated facade material consistency

Kristiinankatu 1 (`103295899S`) had a stone building decision but several later
paint paths bypassed it. Unreviewed roof/wall fragments still bound stored facade
maps, including partial-reading remainders. String courses and historicist
pilasters/balustrades explicitly selected atlas module 42 (plaster). Partial
section material text also overrode its parent without material-field confidence.
The previous deployed probe checked wall material labels and therefore missed
these emitted zone materials.

All coarse wall surfaces now use generated paint. Stored maps, descriptions,
characterization forms and reference images remain untouched. The retained maps
are still available to other consumers, but are no longer a coarse-render fallback.
No building-ID condition selects materials in the renderer.

Material precedence is explicit building evidence, unopposed supported reviewed
wall readings, retained generated descriptions, then register/style priors. A
supported contrary wall, floor band, base or trim reading remains local. Partial
sections need their own material support >= 0.6 to contradict the family; an
opening-count confidence does not authorize a different material. Unknown sections
inherit the resolved wall. Layout, counts and observed colours remain wall-local.

Stone uses the existing mineral palette for unobserved pigment and lighter stone
for trim. A plaster/donor/era colour cannot tint that stone yellow. Supported
same-wall colour readings remain available. The atlas material used by courses,
surrounds, pilasters and ornament now inherits the resolved trim material.

## Inventory

A read-only replay over the **341 buildings** with retained surveyed map segments
found **49** with mixed generated cladding materials across walls/zones before the
change. **37** of those change, including Kristiinankatu 1: **36 other buildings**.
**33** become one cladding family (**32 other buildings**); four retain supported
local contrasts while other zones change. Sixteen buildings still have mixed
materials; their supported wall/floor contrasts are retained. Forty buildings in
total change material sets or pigment. These are generated-instruction counts,
not image classification of stored maps or historical served-pixel telemetry.

Kristiinankatu's 48 surveyed segments change from stone plus plaster decoration
to stone throughout. Examples elsewhere are `103295900T` and `1032959104`
(stone plus plaster to stone), `103277362T` (concrete plus plaster to concrete),
and `103258136L` (brick plus plaster to brick).

Reproduce in a pre-change and current checkout using the same inventory script:

```sh
source ../sandbox.env
bash pipeline/env/run.sh python scripts/dev/inventory_facade_materials.py \
  --output "$TURKU3D_WORK/reports/materials-before.json"
# After applying the change:
bash pipeline/env/run.sh python scripts/dev/inventory_facade_materials.py \
  --baseline "$TURKU3D_WORK/reports/materials-before.json" \
  --output "$TURKU3D_WORK/reports/materials-after.json"
```

## Verification

Selected quick checks cover field precedence, contrary material support, stored
asset preservation, generated binding, partial sections, wall paint partitioning,
learned layouts and first-pass collision hash isolation:

```sh
PYTHONPATH=pipeline:textures bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_facade_inference.py pipeline/tests/test_facade_assets.py \
  pipeline/tests/test_facade_programs.py pipeline/tests/test_facade_learning.py \
  pipeline/tests/test_coarse_sources.py
```

The owned deployed probe builds `t0_0_1` and `t0_0_0`, first and full passes,
compares building/collision counts with source, then examines actual emitted
GLB paint ranges as well as diagnostic wall decisions. It checks Kristiinankatu 1,
`103295904X` (concrete), and the two other stone buildings above. Stored facade
materials must be absent. Per Jonni's speed-first coordination update, there are
no scripted browser runs; he checks the visuals in the game:

```sh
bash pipeline/env/run.sh python scripts/dev/probe_facade_materials.py \
  --commit "$(git rev-parse HEAD)"
```

Delivery timings, rolling-health smoke and deployed outcomes are recorded in the
owning task's result/check logs. No new screenshots are generated.
