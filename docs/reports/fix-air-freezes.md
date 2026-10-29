# Air streaming freezes: evidence and fix (2026-10-29)

Evidence: the supplied `air-play-20261029-0842` recorder, trace summary and
allocation profile. No reproduction, browser launch or validation on the Air.

The trace reports 3.2–3.9 second animation-frame microtasks, almost no new
pipelines and no significant GC. The allocation profile gives a more specific
cause than the partly evicted CPU profile: `modelTextures` calls `Node.traverse`
for each material node slot. TSL expressions form a shared directed graph, but
`Node.traverse` walks every path. Ground shader expressions therefore cause
exponential repeated traversal and temporary allocation during texture discovery,
before queued uploads and compilation can yield. The two texture-discovery
caller paths account for approximately 7.2 GB cumulative sampled self-allocation
inside `_getChildren` over the recording, in addition to traversal allocations.
This is allocation traffic, not a measurement of live retained heap.

Texture discovery now uses one visited-node set across the entire model and an
iterative worklist. It still discovers ordinary PBR maps and TSL textures, but
visits each expression once. The regression uses 16 levels of shared expressions
and checks exactly one child enumeration per node across multiple materials.
This fix was deployed first so the geometry/residency work did not delay it.

## Repeated work and residency

- Tree bark/crown merging, vertex clustering, triangle deduplication, normals and
  crossed silhouettes previously ran for every tile preparation. They now share
  derived geometry by a fingerprint of decoded prototype attributes, indices and
  baked colours. Tile UUIDs and instance placements are excluded; changed geometry
  or colours create a different entry. Reference counts protect active neighbours;
  at most 64 idle derived geometries survive eviction. Tile reloads reuse the
  current small prototype library without rebuilding its LODs.
- Instance placement still belongs to each tile. Streaming now yields between
  prototype groups and every 256 placements through the shared 2 ms preparation
  budget. Existing shadow preparation and shader preparation retain their budgets.
- The load window remains 3x3. Previously, crossing its boundary immediately
  discarded the departing column, including in-flight preparation. Already
  requested models now remain until their axis distance exceeds 325 m (the
  250 m load-window reach plus 75 m). Residency stays spatially bounded to at most
  4x4 per retained start; teleports still discard distant models even offline.
- Player-car deformation was startup-only in the current call sites, so it does
  not explain the recurring freezes. It now shares one prepared template per
  asset URL, including concurrent loads. Geometry/materials are shared and wheel
  transforms remain independent. Parked-car source meshes were already cached
  and instanced; that path retains its shared materials and reusable buffers.

The incomplete CPU node table cannot attribute every individual freeze to a
particular tile or prototype. The supplied evidence identifies pathological
texture discovery and confirms repeated tree preparation; it does not establish
that parked-car bodies were rebuilt every five seconds. Jonni's live Air retest
remains the direct confirmation of the observed freeze removal.

## Focused verification

```sh
npm --prefix game test -- tests/unit/renderScheduling.test.ts tests/unit/vegetation-lod.test.ts tests/unit/prototype-geometry.test.ts tests/unit/streaming-resources.test.ts tests/unit/coarse-tiles.test.ts
npm --prefix game run typecheck
```

Forty tests cover linear texture traversal, shared materials, shared/reloaded
merged and reduced tree geometry, changed-prototype invalidation, bounded idle
retention, paced placement, concurrent car loads, boundary jitter and teleports.
The broader post-deploy selection covers shadow batching, parked streaming and
collision streaming, without launching a browser or rebuilding tiles.
