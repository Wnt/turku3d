# Reading-driven facade geometry

2026-10-29. The full on-demand pass now adds bounded parametric facade components
without rewriting characterization inputs, existing maps, descriptions or facade-kit
results. No screenshots, drive checks, image generation or area rebuilds were used.

## Coverage

The frozen `ring42/programs.json` source has SHA256
`bd5bb74b4e469a30e797d76875f2d44ea89b3679433d9599db5d63b6f978af95`.
The same painter adapter reproduces the previous **349-wall** denominator.
The component audit emits geometry on **42 of those 349 walls**; **307 remain
unresolved**. Another **24 already partially painted walls** gain components, for
**66 characterized walls with new geometry** in total. These are bounded-section
coverage counts, not fully reconstructed walls or a claim that live tiles have all
been rebuilt. On-demand production may further abstain when a roof/passage fragment
cannot contain a described component.

| New support among the 349 | Walls |
| --- | ---: |
| Open balconies | 11 |
| Glazed balconies | 11 |
| Loggias | 5 |
| Colonnades/arcades | 7 |
| Entry/frontage recesses | 7 |
| Verandas | 1 |

Projecting bays/risalits also render on already partially painted walls. The library
supports external stairs with explicit rise/run and located artwork slots, but the
remaining readings do not provide enough information to claim those walls rendered.
Through-passages remain owned by the existing mapped tunnel geometry. Reading slots
bind to its explicit passage provenance and carved apertures; an unlocated vehicle
passage description never punches an invented tunnel through a building.

## Geometry and preservation

Named left/centre/right and floor-band extents and default depths are **layout
priors**, labelled in the component descriptors. Explicit bounds, depth, rows,
stacks and ordered bays override those priors. Unknown counts, lower bounds,
clipped/hidden fragments and uncertain wall associations abstain. Existing register
heights/storeys are unchanged. No component sampling is added for unphotographed
buildings; the learned painter remains in place.

Projecting and recessed planes replace their original wall patch, with returns and
the original UVs and paint on the displaced face. Loggias have a recessed back,
slabs and rails; balconies have decks and rails or tinted glazing; verandas have
framed glazing. Arcades have back walls, columns and optional faceted arch heads.
External stairs use bounded stepped boxes. Artwork is an inert slot with a known
rectangle, awaiting an appropriate asset. Glass uses a shared opaque tint to avoid
alpha sorting costs.

Projecting hit surfaces receive simple box collision meshes. Only walkable arcade
recesses open wall collision; upper loggias do not create access through the building
shell. Existing mapped passages are preserved before decoration. The initial basic
pass stays unchanged; the full visual/collision pair carries the components.

Expanded source geometry goes through the existing wall-surface check. After wall
assembly, intact repeated boxes become `EXT_mesh_gpu_instancing` batches sharing
materials. Clipped boxes remain clipped meshes. Producer sources and per-building
program parameters enter the existing tile/stage content hashes; ground, roof and
other unaffected cached stages remain reusable. Full collision freshness also checks
facade parameters so a changed reading cannot retain an old full collision pair.

## Evidence and verification

The private read-only inventory and generated representative GLB are in
`$TURKU3D_WORK/reports/facade-components/`. The audit covers all 735 deferred readings
and validates emitted meshes in export precision, with **zero overlap failures**.
Synthetic production tests cover paint preservation, rotated UVs, collision cuts,
passage provenance, artwork slots and instancing round trips. Jonni performs game
visual testing; this report makes no screenshot-based fidelity claim.

```sh
source ../sandbox.env
PYTHONPATH=pipeline:textures bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_facade_components.py pipeline/tests/test_facade_programs.py \
  pipeline/tests/test_facade_structure.py pipeline/tests/test_facade_wall_overlap.py
node --test pipeline/turku3d/package/facade_components.test.mjs
PYTHONPATH=pipeline:textures bash pipeline/env/run.sh python \
  tools/research/facade_characterization/audit_components.py
```

## Remaining work

The 307 unresolved walls include incomplete non-geometric layouts, balcony counts
or locations obscured by crops, separate/uncertain planes, stairs without rise/run,
passages without mapped paths, and clocks/signs/murals without located asset bounds.
Those need better bounded readings or existing mapped geometry, not invented
complete facade grids. Geometry defaults can be refined as measured dimensions
become available. No photographic or city source data is added to git.
