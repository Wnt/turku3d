# Recognisable Turku: implementation and evidence plan

2026-11-12. Owner: realism-20261112. Target: https://city.lab.madekivi.fi/.
This plan implements the user's request for iterative photorealism and recognisability.
It supersedes the old architecture's expectation of individually generated textures
for every street-facing facade. Preserve all existing authored descriptions, facade
programs, approved textures and library assets. Generate the world on demand.

## What matters most

A familiar street is recognised by its section, silhouette, facade rhythm, materials,
trees and distinctive objects. Lighting cannot repair a missing lower quay, a wrong
lamp silhouette or a sign planted inside a building. Prioritise those errors before
adding more post-processing. Photorealism also requires motion stability and contact:
a photogenic still with shimmering windows or non-collidable furniture is insufficient.

The code already implements sky/PMREM lighting, cascaded shadows, GTAO, TRAA, bloom,
AgX, planar water reflection, PBR texture preservation and adaptive resolution.
Do not reimplement those features under new names. Current facade shader programs
paint much of the surround/cornice/balcony detail on flat wall surfaces; they need
real depth at walking distance. Sky-only environment lighting lacks nearby city
occlusion, bounce and reflected buildings outside the water reflection path.

## Reference study and its limits

Keep all reference pixels and comparisons outside Git and releases. Private evidence
for this slice is in `$TURKU3D_WORK/reports/references/`. Record date, season, camera
pose uncertainty and source; distinguish a measurement from an inferred prior.

| Reference inspected | Useful evidence | Limit |
|---|---|---|
| Live [Turku360Tour](https://turku.360tour.fi/) aerial view over Samppalinna | Dense irregular tree crowns, continuous street walls, roof silhouettes, bridges and river corridor | Aerial view, sunset exposure; cannot measure small curbs or lamp dimensions |
| Existing Google Maps 3D captures for office/river tiles t0_0_0 and t0_0_1 | Block proportions, roof colours, courtyard voids, bank width and vegetation volumes | Photogrammetric facade distortion; old capture and unregistered camera, unsuitable as exact collision truth |
| Existing Google reference street render | Shows why aerial mesh is insufficient at walking distance: smeared openings and large distorted triangles | Rendering/coverage defects also contaminate it; do not score the game against this as clean ground truth |
| Vastavalo 509952, Kirjastosilta | Heritage lantern profile, paving slab scale, stair/handrail relationship and cool bridge strip versus warm lamps | Blue-hour photograph, clipped lamp exposure, 2014 filename; exact present fixture inventory unverified |
| Vastavalo 846451, low quay | Large masonry courses, coping, narrow lower path, upper tree line and dark water with broken reflections | Night exposure; bank attribution and dimensions need registration |

The requested [Vastavalo search](https://www.vastavalo.net/haku-Aurajoki.html)
returned HTTP 403 in isolated Chrome during this study; the existing local previews
were inspected instead. The ordinary browser-control host was unavailable; project
Chrome-for-Testing was used with an isolated profile. Neither limitation establishes
that the source is generally unavailable.

The first `?cam=` capture entered the separate QA scene and showed plain proxy
geometry. It is **not evidence of the normal game's visual quality**. Use the actual
play session, record its release and loaded tile generations, and wait for full
geometry before judging materials. A comparison with different pose/time/season is
qualitative only. Do not advertise a numerical similarity gain from it.

## Scalable reconstruction

### 1. Build a common evidence graph

Use stable building, facade, street-segment and object IDs. Attach source, date,
coordinate system, uncertainty, field confidence and content hash to each observation.
Continue EPSG:3877/N2000 and the existing tile-local coordinate contract. Resolve
source disagreement per field, not by selecting one universal source:

- City vectors: footprints, curb/stair/wall breaklines and street/furniture locations.
- Lidar: ground profiles, canopy extent, roof planes and supported heights; distinguish
  sampling gaps from empty space. Use class 2 plus near-ground class 12 as documented.
- Register: floor count, construction year and use/material priors, including unknowns.
- OSM: names, entrances, lane/traffic semantics and semantic gap filling. City survey
  coordinates take precedence where the same object is known in both datasets.
- Orthophotos: surface boundaries, roof appearance and canopy extent. Remove cars,
  shadows and obsolete markings before interpreting them as permanent material.
- Photos/panoramas: visible architectural rhythm, fixture family and material examples.
  Align to existing geometry, mask occluders and reject ambiguous facade identity.

### 2. Reconstruct street cross-sections before decoration

Fit cross-sections perpendicular to mapped edges: roadway, curb crest/face, footway,
retaining wall, lower promenade and water. Estimate each side from local lidar
profiles and neighbouring observations; keep bridges and tunnels on separate levels.
Emit the visual face and collision surface from the same profile. Carry exact shared
edge constraints across tiles. Preserve ramp/dropped-curb access instead of extruding
every line to a constant height. Water is not an interpolation surface for dry land.

Place an object only on its final semantic support surface. Check its entire base,
clearance to buildings, navigable width, water and bridge level. Orient signs from
mapped facing/traffic approach, not an arbitrary nearest-road tangent at intersections.
Deduplicate city and OSM observations. Never hide a systematic coordinate mismatch
by snapping every object to whichever surface happens to be nearby.

### 3. Use reusable measured families

Create a small photographed and dimensioned library for Turku heritage lanterns,
road lamps, modern promenade lamps, traffic poles, bollards, bins, benches, railings,
bus stops, drains and curb stones. Store dimensions, attachment points, materials,
collision envelope and LODs together. Choose family from explicit municipal data
first, then reviewed street-segment rules, then a conservative generic fallback.
An unknown lamp stays unknown; river proximity alone does not prove heritage style.

Instance geometry and share trim sheets. Reuse stable object IDs for deterministic
wear and variation. Leaves, posters and surface wear must follow physical context;
uniform random dirt makes the city less plausible. Preserve critical walkway width.

### 4. Characterise buildings economically

Three tiers replace a city-wide multimodal/image-generation pass:

1. **All buildings:** geometry, register and cheap deterministic descriptors. Accurate
   roof envelope, floor bands, entrance positions and material/era priors.
2. **Reusable facade families:** local batched CV on good registered crops, producing
   openings, wall colour and bay spacing with per-field confidence. Fit distributions
   by use/era/material/geometry; stable-ID sampling fills unobserved fields. An inferred
   facade never inherits a donor's coordinates, exact openings or identity.
3. **Important buildings:** multimodal agents review several views and sources, then
   author a precise facade program and special geometry. Start with City Hall,
   Qwensel House/Pharmacy Museum, Cathedral, Library and the office frontage. Prioritise
   recognisability and route visibility; extend only after direct before/after review.

Existing experiments in [facade-characterization](../research/facade-characterization.md)
and [the Linnankatu pilot](../research/facade-linnankatu-pilot.md) already support this
direction. Their window detection confused retail/ribbon/balcony glazing: the small
blind audit had zero exact standalone-window counts out of nine scorable walls.
Do not scale those predictions as truth. Improve semantic classes, facade identity,
occlusion rejection and multi-view agreement; evaluate on held-out streets before
publishing automatic changes. Expensive review targets uncertain, prominent cases.

Generate generic plaster, stone, brick, wood and metal materials once per family.
Use neutral albedo and coherent normal/roughness/occlusion maps at metric scale.
Keep real lighting out of albedo where possible. Texture generation is not a geometry
measurement. Existing authored building assets win over statistical defaults.

### 5. Give facade programs actual depth

Near the player, emit recessed glass, sill/lintel relief, cornice silhouettes, balcony
slabs/railings, door reveals and shop entrances. Derive them from the same program
that controls distant shading. Instance repeated modules, limit geometric detail by
screen-space size, and retain a stable distant representation without popping.
Structural/protruding surfaces receive suitable collision; small ornament does not
need full triangle collision. Never close a mapped passage with a decorative facade.

## Rendering lessons from current engines

| Technique | Transfer to this game | Decision |
|---|---|---|
| [Nanite](https://dev.epicgames.com/documentation/en-us/unreal-engine/nanite-virtualized-geometry-in-unreal-engine) | Hierarchical detail, culling, streaming and screen-space work budgets | Improve current LOD/assets; a Nanite rewrite is not the first step |
| [Lumen](https://dev.epicgames.com/documentation/en-us/unreal-engine/lumen-performance-guide-for-unreal-engine) | World-space lighting representation, cached radiance and temporal reuse | Begin with generated sky visibility/bent normals and sparse local probes |
| [DDGI](https://research.nvidia.com/publication/2019-05_dynamic-diffuse-global-illumination-ray-traced-irradiance-fields) | Visibility-aware irradiance prevents leaking through walls | Prototype offline visibility on one changed tile before broader integration |
| [TSR](https://dev.epicgames.com/documentation/en-us/unreal-engine/temporal-super-resolution-in-unreal-engine) / [three TAAU](https://threejs.org/docs/pages/TAAUNode.html) | Reconstruct full output from lower-resolution colour/depth/velocity | Installed TAAU is an experiment candidate after stable moving baselines |
| [three SSGI](https://threejs.org/docs/pages/SSGINode.html) | Screen-space indirect detail plus temporal denoising | Measure cost and offscreen failures; not a substitute for world-space lighting |
| [Production rendering talks](https://advances.realtimerendering.com/s2021/index.html) | Large-world lighting, radiance caches, atmosphere and vegetation | Reproduce one bounded technique at a time |

Epic's cited Lumen guide budgets about 4 ms/8 ms at 1080p internal resolution for
its 60/30-fps tiers. These are engine-specific design budgets, not evidence that the
same feature will fit our browser. Profile actual city scenes on M1 Max and Windows.
Current synthetic renderer timings do not establish a real-city frame budget.

Windows study: inspect installed titles using repeatable daylight/overcast/night
routes and available frame-debugging/source material. Record pass structure, LOD,
material scale, shadow softness, vegetation and streaming behaviour. Investigate
shipped shaders/binaries where accessible to understand implementation; no proprietary
assets enter this repository. No game has been run or disassembled in this slice yet.

## Iteration schedule and acceptance

Aim for a playable delivery every 30 minutes; this is a working cadence, not a
promise to ship an unverified change at a deadline. Each slice has a concrete test
location and a receipt. Large features span multiple slices. Reprioritise using
observed defects and the user's playtest after each deployment.

| Order | Deliverable | Playtest / evidence |
|---|---|---|
| 1 | Final-surface sign and stop support, source exclusion and matching collider origins | Office streets and riverside; no floating/buried bases, unsupported water or wall placements; first/full builds and retained pole counts |
| 2 | Review and correct sign facing and mapped lamp families on representative streets | Kirjastosilta approaches, river promenade and a road intersection; compare silhouette, height and orientation |
| 3 | Continuous quay/curb/stair cross-section corrections | Walk office–Auransilta–Kirjastosilta, descend lower quay; no terrain wall across path, missing collision or seam |
| 4 | Shared facade geometry for recesses/sills/cornices | Oblique walking views; real parallax/shadows, preserved facade rhythm and authored textures |
| 5 | Street surfaces and small asset kits | Metric granite courses, paving joints, drains and curb transitions; no stretched texture or tiled photographic shadow |
| 6 | One hero building plus reusable family improvements | Compare the actual building from multiple sides; reuse generalisable rules, retain unique details |
| 7 | City-aware indirect light/reflection prototype | Shaded street versus open river, window reflections and contact; record leaks and time-of-day response |
| 8 | Temporal reconstruction and vegetation refinement | Moving route at fixed resolution; readable signs/rails, stable leaves, no ghosting or excessive GPU cost |

Use the remaining 12-hour resource window for repeated bounded slices and observation,
not an automatic whole-city rebuild. If a slice misses its slot, report the actual
blocker and next useful deliverable rather than claiming a deployment occurred.

For every slice:

1. Record a production baseline at a named route/camera, release, time/season and
   loaded generator identities. Capture both still and motion evidence where relevant.
2. Select affected tests only; all pre-merge commands, including policy stages, share
   one hard 30-second budget. Commit with the project coauthor trailer.
3. Run `codex-task finish`: merge, immediately package/upload Pages, publish OCI source.
4. Curl smoke within five seconds; inspect rolling pass failures/cancellations/latency.
5. One selective post-deploy run, hard ten minutes including admission. Generator work
   builds representative real tiles on frozen published OCI source, first and full;
   verify source counts, supported geometry and timings. Client work exercises the
   changed feature on the deployed game. No generic suite substitutes for this.
6. At most one forward fix. Record remaining defects and next playtest; no rollback.

Review sheet: landmark/roof silhouette; facade rhythm and entrances; street section;
prop type/orientation/support; walk/drive collision; material scale; lighting; motion
stability. Record p50/p95 frame/GPU time, draw calls, triangles and resident texture
memory with the same route/resolution. Keep quality and performance scores separate.

## Capacity and spending

The user's current instruction authorises osgallery-dev despite older retirement
notes. Read-only checks found 16 CPUs, about 33 GiB available memory and load around
6; no competing processes needed pausing. Use an isolated workspace for selected
CV, mesh/profile experiments and asset processing, with cached raw inputs read-only.
Mac owns visual review, development and browser checks. Windows is reachable and has
a Radeon Pro 560X 4 GiB; treat it as a useful lower-end desktop comparison, not a
modern high-end ray-tracing reference.

UpCloud is authorised up to **€20 total over the next 12 hours**, including disk,
network and tax where applicable. No resources have been purchased. Prefer existing
capacity until a measured bottleneck justifies offload. Before provisioning, read
current prices, choose a bounded instance duration and record maximum total exposure,
then arrange shutdown/deletion and independently verify it. Historical runbook prices
are not a live quote. Do not provision a recurring resource without enforceable cleanup.
Keep a local ledger of creation time, hourly cost, storage, deadline and deletion receipt.
