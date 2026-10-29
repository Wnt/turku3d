# Generated facade field precedence

Jonni's revised direction on 2026-10-29 is to improve generated facades, not restore
hand-made/AI maps. Reviewed walls still use the procedural painter. Existing maps,
descriptions, characterization programs and reference crops remain untouched.

## Changes

Reviewed values need support >= 0.6 to override a prior. Explicit supported false,
zero and empty values remain authoritative; unknown and weak values do not erase
material, colour, surrounds, cornices or ornament. Retained structure forms are
recompiled in memory so their old rectangle arrays cannot bypass row confidence.
Low-confidence rows remain unobserved; supported other rows keep their openings.
Opening heads/surrounds have independent confidence from bay counts.

The resolver keeps material and colour on the matched wall. Register cladding
outranks sampled donor material. Explicit building-level evidence is separate
from photographed wall evidence: a side wall is never a building-wide material
source. Historicist style priors add bounded pilaster, pediment/surround, cornice,
inset balustrade and shop-fascia treatment around supported opening layouts.
These details are labelled priors, not claimed measurements or invented signage.

Existing supported same-wall photo colours also remain usable as explicitly
uncalibrated appearance priors instead of being discarded for an era colour.
They cannot overwrite a supported calibrated description, and are never copied
to another wall. This covers Linnankatu's shorter previously read segments too.

Two new wall-local pigment descriptors are medians of manually reviewed patches
from existing characterization crops. Only descriptors, normalized patch boxes
and crop checksums are committed; images remain private. They are explicitly
uncalibrated photo-appearance priors, not measured albedo. The reusable sampler is
`scripts/dev/sample_facade_appearance.py`. There are no building-ID branches in
the resolver or painter.

Stone already had a distinct atlas material and block relief. Granite and
rusticated-stone readings now normalize to that material through compilation and
material-band painting. Kristiinankatu's register says **other material**, not
stone; a separate attributed building-cladding descriptor records Jonni's stone
identification. It does not rewrite the register or spread one wall's reading.

## Matched-wall comparison

These are resolver/paint instructions for the surveyed wall, not a game screenshot.

| Building | Before | After | Preserved structure |
| --- | --- | --- | --- |
| Linnankatu 16, 103295896N | Ochre register palette `#c9b188`, pale trim `#d0cdbb`; full-wall procedural replacement | Same-wall panel pigment `#ac9686`, trim `#cabeb5`; generated pilasters, inset balustrades and shop fascia with retained reviewed pediments/cornice | 9 bays, 24 observed openings, 3 registered storeys |
| Kristiinankatu 1, 103295899S | Plaster material, ochre palette `#c6ad8e` | Stone module, same-wall grey `#7e96a4`, pale trim `#b9bebd`; unobserved walls receive the building stone prior independently | 2 bays, 8 observed openings, 4 registered storeys |

Colour estimates retain the source crops' illumination; no de-lighting claim is
made. The Kristiinankatu crop shows a pale rendered upper band as well as stone;
the building-level stone prior addresses the reported wrong cladding without
inventing a measured floor boundary. Source forms remain available for a later
supported material-band reading.

## Deployed finding and forward fix

The first OCI run built `t0_0_0` first/full in **1.799 / 33.640 seconds**,
with **46 buildings / 16 collision cells** matching source. Its assertion then
caught enriched OSM `osm_use=retail` selecting `shopping` before the registered
1887 era could select historicist details. The one forward fix retains explicit
pre-1930 period style for retail buildings while keeping their shop ground
floors. The acceptance assertion is unchanged.

## Displacement inventory

Read-only evaluation of the previous reviewed-wall eligibility against retained
surveyed facade segments found **104 of 341 buildings**, **227 stored map
segments**, of which **100 buildings / 208 map segments** matched structure-only
readings. Counts describe potential replacement under the old precedence, not
historical served-pixel telemetry. The original assets remain available.

Reproduce with:

```sh
source ../sandbox.env
bash pipeline/env/run.sh python scripts/dev/inventory_facade_precedence.py
PYTHONPATH=pipeline:textures bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_facade_inference.py pipeline/tests/test_facade_programs.py \
  pipeline/tests/test_facade_assets.py pipeline/tests/test_facade_structure.py \
  pipeline/tests/test_facade_learning.py
```

The selected post-deploy command is:

```sh
python3 scripts/dev/probe_facade_inference.py --commit "$(git rev-parse HEAD)"
```

It uses frozen published OCI source, builds first/full passes for `t0_0_0` and
`t0_0_1`, compares building/collision counts against source, checks full-pass GLB
textures and checks matched-wall generated paint, colours, bays and stone
material. Its remote wall budget is 540 seconds; the owning async job includes
admission waits in the repository's ten-minute limit. Actual deployment smoke
and async outcomes are recorded in the task result/check logs.
