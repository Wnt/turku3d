> **Archived 2026-11-12.** The ten-hour campaign plan and its ~20-iteration schedule were replaced by
> [docs/design/visual-brief.md](../design/visual-brief.md) after the campaign review: iteration counts are no longer a
> success metric. The reference study, reconstruction notes and rendering lessons below remain useful background.

# Recognisable Turku: implementation and evidence plan

2026-11-12. Owner: realism-20261112. Target: https://city.lab.madekivi.fi/.
This plan implements the user's request for iterative photorealism and recognisability.
The latest direction is a clean next-generation game/map pipeline: **no backward
compatibility with the existing game, APIs or tile format is required**. Start in the
city centre, prove a small set of contrasting city blocks across different regions,
then run large generation only after those pilots pass. Existing imagery-derived
observations and reusable assets are valuable inputs, not schema constraints.

## Active ten-hour campaign

Started 2026-11-11 23:47 UTC; target finish 2026-11-12 09:50 UTC (11:50 Helsinki).
The goal is about twenty meaningful playable improvements across three compact
city-centre pilots, a connected showcase core, two detailed landmark frontages and
at least one contrasting held-out block proving reusable rules. Native Codex workers
run at high reasoning effort; the current session provides three worker slots plus
the coordinator. Workers own scene generation, runtime/rendering, and visual
evidence/look development. The coordinator integrates, deploys and resolves blockers.

| Iterations | Intended visible result |
|---|---|
| 1–4 | Complete new scene, coherent street/quay sections and reliable walking/driving contact |
| 5–8 | Correctly assigned furniture families, granite/paving detail and road surfaces |
| 9–12 | Landmark/commercial facade depth, entrances and passages |
| 13–16 | Vegetation, daylight contact/indirect light and river appearance |
| 17–20 | Night lighting, temporal stability, held-out transfer and consolidated walkthrough |

These are priorities, not a claim that every item will finish on a fixed clock.
Reorder work using actual evidence and dependencies. The normal city.lab game enters
the new prepared pilot by default; city.madekivi.fi preserves the previous world.
A complete approximately 50 m initial scene is acceptable, then expands with context
and adjacent blocks. Do not build a legacy/next-generation chooser or a QA-only demo.

Every claimed visual iteration needs matched before/after game screenshots: same
camera transform, FOV, clock, weather, quality and internal/output resolution. Add
3–8-second clips for motion, vegetation, water or streaming changes. Preserve source
and release identities, actual capture hardware, exact playtest location and relevant
validation. Publish game-owned evidence at **city.lab.madekivi.fi/progress/**; media
lives outside Git under the read-only `/progress-evidence/` mount. Private reference
photographs and other games' captures never enter the gallery. Planning, tooling and
asset-library-only commits do not count as visible improvements. Never relabel a
different-resolution beauty capture as a controlled before/after result.

Routine HTTP errors, host availability, game settings and broken verification tools
are work to resolve. Search existing project solutions, repair the cause, try another
permitted source or host and continue independent work. A per-slice forward-fix limit
routes remaining issues to the coordinator; it does not end this campaign. Account
access requirements remain real constraints, not permission to evade access controls.
At the time limit, report achieved and unmet targets with evidence; elapsed time is
not proof of completion or visual quality.

This supersedes the old expectation of individually generated textures for every
street-facing facade. Preserve useful authored descriptions, facade programs, approved
textures and library assets by importing their information into the new scene model;
do not preserve old loaders, migration chains or rendering paths merely for compatibility.
On-demand generation is useful during development; the user's latest instruction also
permits large generation after proof. Do not start a city-wide batch as a pilot test.

## What matters most

A familiar street is recognised by its section, silhouette, facade rhythm, materials,
trees and distinctive objects. Lighting cannot repair a missing lower quay, a wrong
lamp silhouette or a sign planted inside a building. Prioritise those errors before
adding more post-processing. Photorealism also requires motion stability and contact:
a photogenic still with shimmering windows or non-collidable furniture is insufficient.

The live client is `local/loop-integration` at baseline `470e14d4`, independently
confirmed by city.lab `/version.json`; this workspace's `main` was an older October
snapshot. Target the live branch and city share delivery path. Do not overwrite the
newer game by deploying that old main through the historical Pages workflow.

The live code already implements sky/PMREM, cascaded shadows, GTAO, TRAA, bloom,
AgX, water reflections/SSR, wet surfaces, timecycle/weather, street-light pools,
occlusion-aware ground lighting, facade relief and instanced opening depth/frames.
The task is to improve their accuracy, coverage and calibration. Sky-only PMREM still
makes city-aware daylight occlusion/bounce and local specular probes worth studying.
Do not describe already implemented facade depth, lamps or weather as new features.

## Clean next-generation target and pilot gates

Treat source processing, the semantic city model, asset compilation and runtime
streaming as separate layers. Keep EPSG:3877/N2000 georeferencing and source precision
because those describe real geography; the existing 250 m product grid, `.col`, GLB
extras and REST endpoints do not constrain the new design.

The authoritative block model contains buildings/facade programs, continuous street
and quay cross-sections, surface/material regions, vegetation and typed furniture.
Every placed object references its support surface and attachment/orientation rule.
It carries dimensions, provenance and uncertainty. Geometry, collision, draw instances,
LOD and lighting data compile from that same model. Do not reconstruct gameplay
meaning from rendered triangles or repair a stack of already-compiled products.
The sign issue in the first slice is a concrete example of why final attachment
must be resolved before emitting visual and collision products.

Use reusable meshes/material arrays, authored detail for landmarks, and compiled
instance records. Runtime should upload prepared data with bounded work, rather than
rediscover window layouts or pole origins from mesh attributes during play. Spatial
chunks are a streaming/compiler choice; a building/street entity may span chunks
without losing identity. A release pins one consistent scene/asset/compiler revision.
When the schema changes, replace producer and consumer together; no dual reader is
required. Retain content hashes and the source evidence needed to regenerate outputs.

Before implementation, define a strict scene schema with discriminated feature types,
explicit support/attachment IDs, collision policy (solid, passable, dynamic, sensor),
light-emitter records and required collections that may be empty. Reject invalid
imports before expensive compilation. A source adapter can interpret old evidence;
the runtime accepts one current schema, not accumulated legacy variants.

A canonical entity owns its geometry once. Compiler chunks reference that entity;
shared edges are derived from one common boundary, not separately fitted in adjacent
chunks. Record dependency halos for terrain fitting, casts and tree/facade occlusion,
so an edit invalidates every affected neighbour and no unrelated region. Choose chunk
sizes from measured streaming/upload costs, independently of the lidar input grid.

Bound A1's first delivery to an approximately 50 m frontage and its complete street
cross-section: one existing approved facade program, road/curb/promenade/quay,
one measured lamp family, signs and benches. Compile materials, geometry, collision
and emitter data together and load them through the new runtime. Broader bridge
geometry and additional building families follow after that complete path works.

Freeze source revisions, camera position/FOV/time, images and a short walk/drive
route before generation. Set geometric tolerances per measured source confidence
rather than claiming centimetre truth for every source. Record missed essential
features and severity per category; require no major route/contact defect and a
city-familiar review of frontage recognition. Hold those criteria fixed for the
comparison. Unregistered photos support qualitative detail assessment only.

Pilot locations are candidate areas, not assertions that their current data are complete:

| Stage | Small pilot | What it must prove |
|---|---|---|
| A1 | Office / Qwensel House / Läntinen Rantakatu frontage | Wood + masonry facades, promenade materials, trees, furniture support, quay profile |
| A2 | Auransilta–City Hall junction | Street levels, bridge approach, granite, traffic signs and walk/drive collision |
| A3 | Market Hall / Linnankatu block | Dense retail frontage, awnings, irregular openings, lane/footway boundaries |
| B1 | Port Arthur residential block | Wooden-house families, varied rooflines, cobbles and narrow streets |
| B2 | Martti apartment block | Balconies, plaster/brick families, parking and courtyard vegetation |
| B3 | Kupittaa modern block | Large glazing, modern road sections, different trees and fixture families |

Select exact footprints and fixed camera routes from source coverage before building;
include only seam/context neighbours needed to test those blocks. A1 is the first
complete vertical slice. A2/A3 test city-centre generalisation; B1–B3 are held-out
transfer checks before broad generation. Do not tune every block with unique exceptions.

Promotion gates, declared before each comparison:

1. Recognisable silhouette, frontage rhythm, surface layout and fixture families at
   matched poses; record unknowns and independent review, not just a beauty screenshot.
2. No invisible blockers, fall-throughs, unsupported furniture or disconnected required
   routes; visual and collision surfaces come from the same authoritative support data.
3. Centre street, river edge and one seam crossing pass walking and driving checks.
4. Fixed-resolution moving-route frame/GPU p50/p95, memory and upload/compile hitches
   recorded on the actual target hardware. Use the current 30+ FPS medium target as
   the initial floor, then raise quality/performance goals from measurements.
5. Cold build, cached build and a one-building/one-material edit record wall time,
   CPU time, output size and recomputed stages. Unrelated assets must remain reusable.
6. A held-out block uses the same rules and succeeds without new per-building vision
   or manually patched coordinates. Only explicitly selected landmarks get that effort.

After these pass, estimate city-centre generation cost from the measured distribution
(including difficult blocks), generate the centre, review it, and then expand by region.
Large batches are a later delivery mode, not a substitute for proving the new system.
The 30-minute playtest cadence delivers useful pilot increments and clear receipts;
it does not require completing an entire subsystem every 30 minutes.

## Reference study and its limits

Keep all reference pixels and comparisons outside Git and releases. Private evidence
for this slice is in `$TURKU3D_WORK/reports/references/`. Record date, season, camera
pose uncertainty and source; distinguish a measurement from an inferred prior.

| Reference inspected | Useful evidence | Limit |
|---|---|---|
| Live [Turku360Tour](https://turku.360tour.fi/) aerial view over Samppalinna | Dense irregular tree crowns, continuous street walls, roof silhouettes, bridges and river corridor | Aerial view, sunset exposure; cannot measure small curbs or lamp dimensions |
| Existing Google Maps 3D captures for office/river tiles t0_0_0 and t0_0_1 | Block proportions, roof colours, courtyard voids, bank width and vegetation volumes | Photogrammetric facade distortion; old capture and unregistered camera, unsuitable as exact collision truth |
| Existing central July 2026 street panorama | Cornice/sill depth, mixed shop apertures, awnings, narrow granite curbs and paving grids | Equirectangular distortion; different street from the office play capture |
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

The actual office play capture (`game-play.png`) has recognisable Qwensel-style red
wooden frontage, benches, trees, moored vessels and river reflections. Remaining
visible weaknesses include a largely uniform grey promenade material, repetitive
bench placement/form, conspicuous foliage cards and simplified frontage detail.
These are hypotheses for matched-pose review, not evidence that all corresponding
systems are missing. The capture settled 17 visual models and about 6.48 million
visual triangles with no reported game errors; a single instantaneous frame time
is not a performance benchmark.

## Scalable reconstruction

### 1. Build a common evidence graph

Use stable building, facade, street-segment and object IDs. Attach source, date,
coordinate system, uncertainty, field confidence and content hash to each observation.
Continue EPSG:3877/N2000 georeferencing and precise local coordinates, without
retaining the old product chunk size. Resolve
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

### 5. Extend the existing facade depth where it is missing

Audit existing facadeModules/facade_relief coverage before adding geometry. Near the
player, improve recessed glass, sill/lintel relief, cornice silhouettes, balcony
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
assets enter this repository. The existing November 2 Bodycam UE5.5.4 study already contains captures, configuration
and asset/shader investigation (`~/turku3d/research/bodycam/report.md`). Its Rome
frame was reviewed: masonry/cobble scale, shadowed reveals, cornice silhouettes and
warm sunlight versus cool enclosed streets are stronger transferable cues than its
camera blur or chromatic aberration. Historical GTA road captures show coherent wheel
wear, patching and curb/crossing transitions. Those images are local evidence, not
assets or a new performance measurement.

The initial Windows GTA launch was blocked by a preset parser that expected one
`ReflectionQuality` element although the XML also contained a `Presets` section.
The research harness now scopes the edit to `graphics`, preserves the original
configuration, and passes its targeted checks. A fresh bounded story-mode run
captured three lighting views and a five-second clip; the owned game process was
then closed. Those captures remain private research evidence. No new disassembly
or transfer of proprietary assets is claimed.

## Immediate repair and next-generation iteration schedule

The deployed sign-support repair is an immediate improvement to the current game,
not a requirement to extend its migration architecture. Subsequent work follows the
clean pilot sequence above. The feature priorities below guide that new implementation.

Aim for a playable delivery every 30 minutes; this is a working cadence, not a
promise to ship an unverified change at a deadline. Each slice has a concrete test
location and a receipt. Large features span multiple slices. Reprioritise using
observed defects and the user's playtest after each deployment.

| Order | Deliverable | Playtest / evidence |
|---|---|---|
| 1 | Final-surface sign and stop support after existing migrations, matching recorded collider origins | Office streets and riverside; check actual final solid support, preserved sign stacks/IDs and correction/omission counts |
| 2 | Review and correct sign facing and mapped lamp families on representative streets | Kirjastosilta approaches, river promenade and a road intersection; compare silhouette, height and orientation |
| 3 | Continuous quay/curb/stair cross-section corrections | Walk office–Auransilta–Kirjastosilta, descend lower quay; no terrain wall across path, missing collision or seam |
| 4 | Improve existing facade relief/module coverage and source accuracy | Oblique walking views; real parallax/shadows, preserved facade rhythm and authored textures |
| 5 | Street surfaces and small asset kits | Metric granite courses, paving joints, drains and curb transitions; no stretched texture or tiled photographic shadow |
| 6 | One hero building plus reusable family improvements | Compare the actual building from multiple sides; reuse generalisable rules, retain unique details |
| 7 | City-aware indirect light/reflection prototype | Shaded street versus open river, window reflections and contact; record leaks and time-of-day response |
| 8 | Temporal reconstruction and vegetation refinement | Moving route at fixed resolution; readable signs/rails, stable leaves, no ghosting or excessive GPU cost |

Use the remaining 12-hour resource window for bounded pilot slices and observation.
Large generation becomes appropriate only after the pilot gates pass. If a slice misses its slot, report the actual
blocker and next useful deliverable rather than claiming a deployment occurred.

For every slice:

1. Record a production baseline at a named route/camera, release, time/season and
   loaded generator identities. Capture both still and motion evidence where relevant.
2. Select affected tests only; all pre-merge commands, including policy stages, share
   one hard 30-second budget. Commit with the project coauthor trailer.
3. Merge the selected commits into the actual live `local/loop-integration` using
   `~/turku3d/tools/codex-loop/merge.py`, then immediately package/upload using
   `tools/share/deploy.sh` through the existing coordination wrapper. Deploy only to
   city.lab.madekivi.fi. city.madekivi.fi is frozen at 63b0b015, including its exported
   world and server; the old Pages/turku.lab release remains frozen too.
   Register generator changes against the existing base/rebuild history with
   `scripts/dev/tile-migrate`, retaining on-demand content-addressed products.
4. Curl smoke within five seconds; inspect rolling pass failures/cancellations/latency.
5. One selective post-deploy run, hard ten minutes including admission. Generator work
   updates representative real tiles on the live osgallery farm from the frozen
   deployed source and existing approved product chain; verify packed geometry,
   collision support, counts and timings. For a migration-only fix, reuse the existing
   full products rather than rebuilding unrelated terrain/facades. First-pass generation
   is not changed by this final sign migration. Client work exercises the
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
are not a live quote. Do not leave a resource running beyond its budgeted cleanup deadline.
Keep a local ledger of creation time, hourly cost, storage, deadline and deletion receipt.
