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

`placements` contains `{id, model, position: [x,y,z], viewDistance}`. The stable
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

Occluders are optional and independent of bounds:
`{version:1, provenance:"lod2-inset-boxes-v1", boxes:[{min:[x,y,z],max:[x,y,z]}]}`.
They use the same model frame. Missing or empty boxes confer no occlusion. Bounds
must never be converted into occluders. Placement roots preserve the object as
`userData.occluders`, alongside `archetype`, `placementId`, and `modelBounds`.

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
