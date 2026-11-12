# Registered storeys on compound building envelopes

The approved Market Hall neighbor, municipal ID `103304667L`, retains a
photographed ribbon-window and arcade program. Its complete source shell has a
low podium and taller wings. The former width-weighted wall median was 5.1615 m;
applying the registered seven storeys to that median produced a 0.737357 m
fallback repeat. A reviewed 3.3725 m ground floor then left an implausible
0.298167 m upper repeat. This is a derived fallback error, not duplicate building
placement or a reason to discard the approved facade program.

`facade_programs.storey_layout_height` keeps valid storey fits unchanged. When a
municipal registered count cannot fit the median envelope within the existing
2.2–5.5 m storey interval, it selects the width-weighted median of actual source
wall heights which can support that count. Unknown counts and unsupported
heights abstain. For this building the supported envelope is 17.746 m, giving a
2.535143 m fallback repeat. The reviewed ground-floor height remains 3.3725 m;
its remaining six upper repeats become 2.395583 m. These are source-qualified
regularity priors, not newly surveyed floor heights.

`scene/facade_storeys.mjs` applies this result to the exact pinned approved GLB.
Only vertices carrying the known invalid fallback pitch and no explicit observed
patch change. The adapter appends replacement `_KIT_LAYOUT` and `_KIT_FLOORS`
arrays while retaining the entire original binary prefix, geometry, UVs, palette,
normal and ambient data, materials and four external image dependencies. All
explicit ribbon-window/door patches retain their original fields. The recipe is
`scene/hall_neighbour_storeys.json`; it records source hash and authority.

Reproduce the appearance input with:

```sh
node pipeline/turku3d/scene/facade_storeys.mjs \
  0d71100a5dcaec9f75aba3e6f72928810a80ccbdce210f1d18869a7deac45449.glb \
  pipeline/turku3d/scene/hall_neighbour_storeys.json /tmp/hall-neighbour.glb
```

The generated asset belongs in the canonical content-addressed store, not Git.
The prepared building placement, bounds and collision remain unchanged. Native
same-pose inspection is still required to judge the corrected fallback rhythm.
