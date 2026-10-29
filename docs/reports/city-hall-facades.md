# City Hall facade precedence

The 22.54 m south-east wall of register building `1032958849` (Aurakatu 2)
was bound to stored generated map `1032958849_71`, reprojected from `_65`.
Its retained generation QA reports **zero storeys and zero openings**, and describes
“uniform grey aggregate”. The acceptance record explicitly says the reference did
not show identifiable storeys/window rows. This explains the large grey block:
the current binding is a later generated map, not the original `aurakatu2_se` pilot.
No City Hall identity branch or landmark replacement exists in the current draft
producer. Removing a nonexistent building exception would not fix the wall.

## Source trace

- The pilot's original artwork and label remain archived unchanged. Its label in
  `docs/pilot/textures/pilot.json` describes seven bays, two storeys, salmon render,
  arches and ornament. Neither pilot label was readable by the current parameter
  loader. Both retained pilot labels are now normalized as ordinary records under
  `textures/library/facade-params/`; newer shared/sandbox descriptions win.
- OCI has 15 approved generated/derived map records for City Hall. All remain
  unchanged and use the normal stable-building-id/collinear-wall binding. Map 71
  covers the south-east wall; maps 47/48 cover the three-bay Aurakatu face;
  maps 38, 49/54 and 92 cover other returns, with derived narrow-strip records.
- There was no current native AI/kit description for City Hall. Its fallback
  archetype was `empire` from the registered 1811 date, with two register storeys.
  OSM's `monumental` tag caused the legacy register palette to select `#998f82`.
  Restoring the existing description supplies `#B98B7E` through normal precedence.
  Structure-only reviews now preserve that description colour instead of resetting
  it to the register palette. Calibrated reviewed albedo can still override it.
- Learned fallback rules do not train/predict a building from its own readings;
  retained descriptions and reviewed walls remain higher authority.
- Three wall readings already exist in the characterization store and published
  program. The 11.08 m rear/return reading has three upper windows and an occluded
  lower row. The 12.94 m Aurakatu reading has two windows and a blind niche upstairs.
  The 22.54 m south-east reading has seven bays per row: five windows and two blind
  niches. The latter two remained deferred because mixed bay kinds were unsupported.
- The fourth requested 12.78 m wall (`58105d45c2`) was already attempted twice and
  rejected: no cached panorama passes whole-wall visibility/view-angle checks.
  Its existing treatment remains; no new reading or intermediate rewrite is justified.
- City Hall is one LoD2 building (`Building_908843`), owned by `t0_0_0`, with 33 roof
  parts and 160 exposed wall quads in the retained source snapshot. Those parts
  retain the same facade identity. All their walls pass through the same assembler
  and precedence; neither the legacy landmark allowlist nor the original pilot
  path replaces this draft building.

## Generic changes and scope

Reviewed **sections** now outrank stored maps just like full reviewed walls.
Unobserved/excluded regions retain their map. Material-only readings retain mapped
openings, while supplying material to an unmapped fallback. Geometry-only readings
keep their existing fallback and the newly landed component cuts.

The section adapter accepts explicitly counted, ordered mixed bay lists and their
per-bay heads/surrounds. Blind niches are opaque wall-coloured arch patches with
surrounds, not glass, doors or a completely blank row. Original readings are not
edited. No building IDs occur in the implementation.

A read-only audit of OCI's cached diagnostics found **52 reviewed walls across
36 buildings** bypassed by the old partial-review condition. **33 walls across
27 buildings** have actionable layout sections (including City Hall's Aurakatu
face); the remaining material-only sections preserve their maps. The newly supported
south-east mixed reading additionally replaces City Hall's grey map. Section
normalization changes 16 retained readings across 14 buildings, including more
explicit head/surround names; these populations overlap and must not be added.
Two archived pilot descriptions are recovered through the normal parameter lookup.
These are counts of retained/cached evidence, not a claim of complete city coverage.

The representative City Hall repaint has three reviewed walls, 142 description
fallback quads, 15 map-owned quads, and 15 explicit window patches. There are 17
stored-map mesh pieces, including unobserved portions of reviewed walls. The
3,584 emitted faces pass the existing wall overlap validator with zero violations.
The full per-quad trace is private at the task's `work/reports/city-hall-after.json`.

Input hashing already includes each building's effective description and reviewed
program. Parameter changes therefore affect their own tiles; generic painter code
invalidates the building-visual stage of requested tiles. Ground/collision inputs
and stored texture bytes are reused. No area rebuild, screenshots or drive checks.

## Reproduction

```sh
source ../sandbox.env
PYTHONPATH=pipeline:textures bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_facade_assets.py pipeline/tests/test_facade_programs.py \
  pipeline/tests/test_facade_learning.py pipeline/tests/test_facade_structure.py \
  pipeline/tests/test_facade_components.py
PYTHONPATH=pipeline:textures bash pipeline/env/run.sh python \
  tools/research/facade_characterization/audit_precedence.py \
  --snapshot "$TURKU3D_WORK/reports/city-hall-source.json" \
  --out "$TURKU3D_WORK/reports/city-hall-after.json"
```

Deployment and the selective OCI first/full build receipt are recorded in the task
result and `codex-task checks`; the selected real tile is City Hall's `t0_0_0`.
