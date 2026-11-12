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
snapshot. Independent OSM outline matches have IoU 0.888–0.991. Four supply explicit
wood cladding cues, one plaster, and nine have unknown cladding. Existing grammar
assignment gives four timber, two 1960s slab, four historicist, three Jugend and one
postwar plaster prior; the seven historic-period assignments do **not** establish
masonry cladding or actual ornament.

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
