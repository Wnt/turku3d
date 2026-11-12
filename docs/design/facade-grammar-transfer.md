# Reusable facade grammar and held-out block

The office and Qwensel are the two detailed reference studies. Further blocks reuse
existing facade programs, material parameters, texture families and the established
`turkutex.facade_archetypes` rules. They do not receive copied hero ornament or a
new per-building vision pass.

## Source authority

`scene.grammar.footprint_match` associates independent outlines only when IoU is
at least 0.75 and coverage in both directions is at least 0.8. Competing identities
within 0.15 IoU abstain. Repeated rows for the same source identity are deduplicated;
conflicting material/roof/use cues for that identity abstain. These thresholds are
matching policy, not confidence probabilities.

`qualify_facade` fills missing source attributes and reports the existing grammar
family. Register cladding and storeys remain authoritative. Matched OSM cladding,
roof and use cues are explicit support-0.4 priors. Fractional levels such as 1.5
remain a cue rather than becoming a fabricated integer floor count. The existing
shell-height resolver remains responsible for visual floor layout. Named paint is
an uncalibrated family cue, not measured pigment. Missing material stays unknown;
construction date alone never becomes evidence of timber cladding.

Approved programs and existing AI descriptions are retained unchanged, with hashes
in the adapter result. The adapter does not generate geometry, replace a program,
or promote an inferred period family into an observed opening count. Consumers use
its derived attributes only for unobserved facade zones, following the existing
`facade_inference` field-authority rules.

`scene.facade_transfer.qualified_buildings(buildings, inventory)` is the bounded
producer adapter. It returns copied buildings and authority receipts, fills only
missing attributes, reports conflicting known fields, and preserves footprint,
base, top and roof-part geometry. The ordinary `coarse.stages.building_visual`
and facade-program resolver then create appearances from those qualified inputs.
Unknown cladding stays unknown even when the existing grammar samples a visual
material prior. No new description or reviewed program is written.

The external `scene.facade_appearances` CLI accepts the pinned WFS and ground
pickles, private inventory, tile identity, output directory and existing texture
library. KTX tools must already be on `PATH`. It includes selected crossing
buildings from `wall_context`, resolves exposed walls against neighbouring source
shells, and uses the normal facade kit, roof-material fallback, ambient visibility
and immutable per-building packaging. No orthophoto or street-reference pixels
enter this transfer. `assets.json` and `provenance.json` remain external products;
the latter records input hashes, unknowns, conflicts and retained-description
counts. Consumers must pair these appearances with the same source shell geometry.

## Three central pilot areas

The first office block and Auransilta extension already preserve their approved
building assets. The third pilot uses the existing Market Hall asset and Linnankatu
fronts. The bundled inventory contains 650 building programs and 1,061 walls; the
cached Linnankatu study includes 12 photographed buildings and 13 selected walls.
These counts describe source coverage, not proof that every wall is observed.
Market Hall has a separate approved landmark asset and description; absence from
the generic program list does not authorize replacing it with a generic facade.

## Independent Port Arthur selection

The held-out eastern Port Arthur block uses engine bounds
`[-1000, 250, -875, 375]`, around Puutarhakatu 23–32 and Sairashuoneenkatu 18b.
It retains mixed-era context rather than selecting only attractive timber fronts.
Fourteen municipal building footprints intersect the bounds. None occurs in the
650 reviewed facade programs or the cached 2,299 facade-asset / 2,034 approval
snapshot. Independent OSM outline matches have IoU 0.888–0.991. The initial cached
register snapshot lacked cladding, so OSM alone supplied four timber and one plaster
cue, leaving nine unknown.

A bounded read-only municipal `kanta:Rakennus` and `GIS:Rakennukset` metadata query
on 2026-11-12 recovered the stable-ID records without building the area or collecting
new photographs. Qualified inputs now contain eight explicit timber claddings, one
brick and one concrete; OSM supplies one additional plaster cue. Three remain
unknown. Duplicate `muu materiaali` (other material) records do not erase explicit
known cladding. Years and storeys agree with the existing LoD2 inputs for all fourteen.
The unchanged grammar consequently assigns eight timber, two 1960s slab, two Jugend,
one historicist and one postwar plaster prior. Period-family assignments still do
not establish actual ornament or opening counts.
The cached municipal LoD2 and OSM extracts cover the block, as does the private
23458500_6704500 LAZ tile. Raw extracts, coordinates, photos and derived inspection
manifests stay outside Git. The private inventory records each exact identity,
outline-match metrics, source tags, derived attributes and qualified grammar.
No area build is authorized by this inventory alone: finish the third pilot proof
before preparing the held-out block. Its visual acceptance must assess transferable
rules against independent evidence, with no bespoke facade edits disguised as
successful transfer.

## Validation

Run the affected selection:

```sh
source ../sandbox.env
PYTHONPATH=pipeline ~/turku3d/envs/pipeline/bin/python -m unittest discover \
  -s pipeline/tests -p test_scene_grammar.py -v
```

The tests exercise ambiguous outline rejection, duplicate identity handling,
conflicting source records, source-authority preservation, fractional floor cues,
unknown material and transfer into the established timber family. A source adapter
passing these checks is not itself a completed visible iteration.

## Held-out clipping-sliver correction

The first Port Arthur appearance batch exposed a specific source-processing defect
on103329716M and103329720S: both qualified one-storey timber programs had a0.10m
layout height and5–6cm-high windows. Their visible source walls are several metres
high. Exposed-wall subtraction retained micrometre-wide quads with one upper
endpoint at the building base. The whole-ring minimum admitted that degenerate
endpoint and collapsed the eave measurement for the valid surrounding facade.

`stored_facades.ring_eaves` excludes spans shorter than one millimetre only from
the eave measurement. It retains every input geometry point and index. Real short
returns still participate. The normal registered-storey plausibility resolver
continues to choose an actual supported wall envelope; the roof peak is not used
as a replacement facade height, and the register floor count is not changed.
Existing approved facade fields keep their ordinary resolver precedence.

Only the two affected appearance assets are regenerated for the bounded repair;
the other12 qualified assets, source shell geometry and prepared collision remain
unchanged. This fixes a reusable layout defect, not evidence of each actual window
position or paint colour. The previous Port cross-street camera is the relevant
visual before. Selected regressions exercise a sliver touching the base, a real
short lower return, an ordinary pitched roof and the retained registered-storey
plausibility rule:

```sh
PYTHONPATH=pipeline ~/turku3d/envs/pipeline/bin/python -m unittest discover -s pipeline/tests -p 'test_facade_eave_slivers.py' -v
```

The bounded live repair uses `scene.facade_layout_rebind`: each old instruction
must match exactly one corrected instruction by every nonvertical style/material
field. Only storey, ground, upper and extent-height values change; vertex ranges
and the original visual binary remain exact. The two resolved one-storey grids
are4.8501m and4.6549m, derived from supported lower wall envelopes. The normal
producer's regenerated trim was deliberately excluded from this repair. Packed
triangle POSITION/NORMAL/UV/COLOR0/COLOR1 signatures match exactly, as do all KTX
bindings. Immutable input/output records are in `port_arthur_eave_repairs.json`;
all referenced GLBs remain in the external prepared-asset store.
