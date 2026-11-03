# Building model and placement format

The full L0 fragment contains `buildingPlacements.version = 1`. `visual[tile]`
contains terrain and the remaining non-building layers. Collision stays on the
62.5 m grid. The structural first pass retains its inexpensive combined preview
until the full model placements replace it atomically.

`models` contains `{id, uri, bytes, tris, draws, bounds: {min, max}, occluders?}`.
`id` is the SHA-256 of the final GLB bytes. `uri` is relative to the tile manifest
(`../shared/<id>.glb`); the farm pins it to the producing revision, including on
retained fallback responses. Referenced textures and buffers are independently
content addressed in the same shared directory. Model bounds use local metres.

`placements` contains `{id, model, position: [x,y,z], viewDistance, block}`. The stable
placement ID is the building facade key; `model` references the model hash. Engine
coordinates follow COMMON: east/x, up/y, south/z. Buildings use translation only,
centred on the complete footprint's centroid, with y=0. No building triangle is
cut at a cell boundary. `buildingPlacements.bounds` expands the container to
include terrain and whole models. The existing source building cell owner remains
authoritative, including multipart and passage-split buildings.

View distance is the model diagonal projected to 24 pixels at 1080p and 60-degree
vertical field of view, clamped to 250–2000 m for the HD-only phase. The runtime
measures horizontal distance from each model AABB. These are provisional HD
cutoffs; the hierarchy phase replaces disappearance with a resident-parent swap.

Blocks (`coarse/blocks.py`): buildings whose footprints lie within 25 m form a block; blocks wider than
160 m split on a 125 m grid of building centroids. `blocks` contains
`{id, position, bounds: {min, max}, hdDistance, children: [placement ids], lod?}`; `lod` is a model id whose
descriptor has `kind: "block-lod"`. The LOD parent is one GLB of the block's LoD2 shells (roofs and walls,
no floors) in the block frame around `position`, with COLOR_0 pigment: each building's facade-program wall
colour, roofs (.20,.23,.22). Mesh `walls-glazed` holds buildings whose facade program has windows (and their
roofs); `walls` holds windowless buildings. The client batches placements per block, shows a block's HD
children within `hdDistance` (280 m, 10 m hysteresis) of its bounds and its LOD parent beyond, and a tile whose
blocks are all far draws one merged parent per material. Glazed LOD walls use the distant city's 2.6 m x 3.2 m
window grid, darker by day and lit by the shared procedural window light at night. With an LOD parent the
block swap alone decides child visibility.

Occluders are optional and independent of bounds:
`{version:1, provenance:"lod2-inset-boxes-v1", boxes:[{min:[u,y,v], max:[u,y,v], yaw}]}`.
Each box is given in a frame rotated by `yaw` (radians) about the placement origin:
world x = position.x + u·cos(yaw) − v·sin(yaw), world z = position.z + u·sin(yaw) + v·cos(yaw),
world y = position.y + y. Missing or empty boxes confer no occlusion. Bounds must never be
converted into occluders. `coarse/occluders.py` emits at most 8 boxes per model, inset 0.25 m
inside the LoD2 footprint and below every roof triangle above them (quarter-metre height bands),
following the footprint's minimum rotated rectangle like GTA V box occluders (median about 11 m).
Only models whose surfaces are all opaque (no alpha, no glass) get boxes. Walls render closed except
tunnel splits (outside the split footprint) and through facade components; a building with any
through-component keeps its 4 m street storey and the components' full height open, and surveyed
through-passages are removed as whole columns. Placement roots preserve the object as
`userData.occluders`, alongside `archetype`, `placementId`, `modelBounds` and `visibilitySlot`.

Runtime occlusion (`game/src/world/placementVisibility.ts`, `visibility.worker.ts`): placement bounds
and world box corners live in a SharedArrayBuffer (4096 slots); the main thread publishes the camera
each frame through a seqlock and reads the occluded bitmask the worker computed for the previous
camera. The worker rasterises the 192 nearest boxes of placements that are in the scene and within
their own view distance into a 128×64 conservative depth grid: a pixel takes an occluder depth only
when wholly inside its projected silhouette or face, at that shape's farthest depth; near-plane
crossings fail open. A placement is hidden when its whole dilated screen rectangle lies behind the
grid. Results only skip primary-camera beauty and normal-prepass draws (never shadows, reflections or
shader preparation), static batches only when all their placement owners are hidden, and nothing when
the camera moved more than 1.5 m or turned more than 3° since the result. Without cross-origin
isolation the system is off.

Generation partitions after authoritative wall assembly and neighbour admission.
All facade source metadata, geometry, UVs, colours and component placement data
survive the change of frame. Each building has its own `archetypes/<identity>`
package namespace, keyed by emitted geometry, its facade maps, packaging code,
material library and kit inputs. A single building edit reuses unaffected model
packages and terrain packaging. Upstream wall assembly is still tile-scoped in
this phase; this is not yet a one-building-only geometry generation path.

The runtime deduplicates concurrent model loads, limits model decode admission to
four, and shares geometry under reference-counted leases. Each tile has independent
material ownership before entering the existing material/texture pools. Prototype
resources remain live through the last placement. Collision validation and all
model preparation complete before a replacement becomes visible. Placement changes
trigger a swap even when the terrain GLB hash does not change.

The installed-game study (local `~/turku3d/research/gta-map/FINDINGS.md`,
2026-11-03) supports irregular object/relevance bounds rather than irregularizing
our scheduling grid. `streamingBounds` separately expands content bounds by each
placement's view distance. The hierarchy implementation should target the measured
HD band (p10/p50/p90 112/171/299 m), LOD around 400 m, district SLOD around
600–1500 m, and city representations beyond that, subject to Turku render evidence.
Those are tuning references, not a prescription to hide unparented HD buildings.
Only 16% of sampled drawables carry an internal medium LOD; explicit aggregate
parent entities are the primary replacement contract, rather than requiring every
GLB to embed all detail levels. Collision and navigation keep independent grids.
