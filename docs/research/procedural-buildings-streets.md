# Procedural buildings and streets: Unreal reference tools vs Turku3D

Topic: `procedural-buildings-streets` · Date: 2026-11-12 · Question: can the Yazan Hanna Building Generator and Road
Generator (Unreal Engine 5, PCG) teach us how to generate detail-rich buildings and streets, and how do they compare
with what our facade programs and generator already do?

Labels: **SEEN** read from video frames, **SAID** in narration or description, **INFERRED** our reading, **CODE**
checked in this repository.

## 1. Verdict

- **The quality bar is not theirs.** Both tools make plausible *generic* buildings and roads from hand-drawn splines.
  Every floor of a building is identical. The ground floor differs only by one door, and there are no shopfronts,
  signage, wear or irregularity (SEEN). Our bar is that someone who knows Turku recognises each building as the real
  one ([visual brief](../design/visual-brief.md#acceptance-and-stop-rules)). A generic building in the right place
  fails that bar.
- **Our building description is already far richer.** Our facade programs hold per-wall, per-floor and per-bay
  observations with provenance and support (§4). Their buildings carry one module table and a height.
- **Their strength is real 3D module geometry**: arched windows with brick arches, dentil cornices, pediments, sills,
  and clay roof tiles with ridge and hip covers (SEEN). Our observed detail is mostly compiled into painted and shaded
  wall programs on the surveyed shell. Only window frames and sills become instanced geometry (CODE).
- **What to take:**
  - the structure: an explicit module table, a one-dimensional edge solver and stable edge numbering;
  - side lines as anchors for street furniture;
  - live per-unit regeneration;
  - the visual payoff of real module geometry.
- **What not to take:**
  - grammar as the source of identity;
  - the tile-loop roof;
  - per-tile instancing at city scale;
  - per-building manual offsets;
  - anything tied to Unreal, Fab or Megascans licences.

Studio and research practice is surveyed separately in §7.

## 2. Sources and method

Six videos by Yazan Hanna (Unreal Engine), downloaded with yt-dlp on 2026-11-12. The user supplied the first two
videos and both Fab listings. The other four videos are linked from the Building Generator listing and the video
descriptions.

| Video | Published | Length | Content |
| --- | --- | --- | --- |
| [rFIL71BHZWU](https://www.youtube.com/watch?v=rFIL71BHZWU) The Most Powerful Procedural Building Tool in UE5 | 2026-10-28 | 2:44 | product demo, 2026 build |
| [DQhydHoNuzE](https://www.youtube.com/watch?v=DQhydHoNuzE) Road Generator Demo | 2026-05-13 | 5:05 | product demo, OSM import |
| [irKxiy-xxWQ](https://www.youtube.com/watch?v=irKxiy-xxWQ) UE 5.5 Building Generator | 2025-02-04 | 11:57 | product walkthrough |
| [Mlitgm1jAk8](https://www.youtube.com/watch?v=Mlitgm1jAk8) Build Any City Block in 10 Minutes | 2025-11-09 | 21:33 | product, music only (frames) |
| [n9PHsmRlrRk](https://www.youtube.com/watch?v=n9PHsmRlrRk) Procedural Building Generator with PCG Grammar | 2024-11-26 | 29:50 | free tutorial: wall grammar |
| [uzB1h3s4rzM](https://www.youtube.com/watch?v=uzB1h3s4rzM) Advanced PCG Grammar: Tile-by-Tile Roof | 2024-12-17 | 22:59 | free tutorial: roof |

Fab listings:
[Building Generator](https://www.fab.com/listings/73404bfc-b1e4-4d41-8a6c-0b9c581f2c69),
[Road Generator](https://www.fab.com/listings/f205fd21-1428-4a4b-b067-a46d1a2fe86f).

Method:
- **Transcripts:** whisper.cpp `large-v3-turbo` on an M1 Max, 11–62 s per video. YouTube's own captions were
  rate-limited.
- **Frames:** 409 frames at 1280 px, taken at every scene change and at least every 15 s, plus 4-column contact sheets.
- **Analysis:** one reading per video group, from transcript and frames.

The material is third-party and stays local (about 760 MB), at
`~/turku3d/data/research/procedural-tools-2026-11/` on the project owner's Mac; its `README.md` lists the layout. This document
cites frames by `<video>/<tMMmSSs-NNNN>.jpg`; no frame, transcript or video goes into Git.

## 3. Findings

### 3.1 Building Generator (product: rFIL71BHZWU, irKxiy-xxWQ, Mlitgm1jAk8)

**Workflow.** Each building is an actor holding a closed spline and a PCG graph. The user:
1. Drags the actor into the level and Alt-drags spline points to shape the footprint. The narration says changes
   "appear in real time" (SAID).
2. Assigns meshes from a downloaded Megascans kit to the module slots.
3. Nudges offsets until the joints close.
4. Chooses the roof type, door side and gable sides.

Settings can be copied from another building as a preset (SAID). There is no OSM building footprint import in these
videos. The 2026 demo generates buildings from PCG points, but the city-block video places them by hand (SEEN).

**Data model** (SEEN in the details panel):
- **Module slots.** One array of slots: Wall, Window, Door, Corner, Trim, TrimOuterCorner. Each slot has ten fields:
  `Symbol`, `MeshCollection`, `ScaleHorizontalSize` (the length the module claims along the wall, separate from its
  visual width), `ScaleVerticalSize`, `bScalable`, `VerticalScalable`, `DebugColor`, `MeshPlacementAdjustment`,
  `Rotate` and `MeshScale`.
  - The first seven are Unreal's PCG grammar module fields; the last three are the plugin's per-slot fitting layer.
  - Corners claim 0.001 of length, so they sit on footprint vertices and overlap the walls.
- **Global settings.** Building height (the floor count is however many module heights fit), roof height and offsets,
  and a door side given as an integer edge index. A "show side numbers" button labels the footprint edges in the
  viewport (`Mlitgm1jAk8/t13m21s-0056.jpg`).
- **Ground floor.** There is no separate ground-floor slot: the door module is scaled 1.5–1.6 and raised 110–120 cm
  (`Mlitgm1jAk8/t04m09s-0018.jpg`).

**Roofs** (SEEN, SAID). Geometry Script builds a roof mesh inside PCG. That mesh is either given a material with
world-scaled UVs or sampled to place instanced tile meshes, with Corner and CornerTop modules as hip and ridge covers
(`Mlitgm1jAk8/t08m24s-0035.jpg`). The hip lines on an irregular footprint look like a straight skeleton
(`rFIL71BHZWU/t01m31s-0008.jpg`; INFERRED). Gables are chosen per footprint edge index ("5,6,7"). The gable walls are
flat planes with a material and no modules (`Mlitgm1jAk8/t13m36s-0057.jpg`).

**Detail richness and defects.** The richness comes from the purchased kit (`irKxiy-xxWQ/t10m58s-0047.jpg`).

What is missing (SEEN):
- every floor and window is identical;
- the walls are perfectly flat;
- windows read as dark;
- there are no decals or wear beyond the textures;
- LOD is not discussed.

Visible assembly faults (SEEN):

| Fault | Frame |
| --- | --- |
| seams between modules | `irKxiy-xxWQ/t04m52s` |
| sky showing through a corner gap | `irKxiy-xxWQ/t07m52s-0033.jpg` |
| misfit trim corner | `Mlitgm1jAk8/t06m09s` |
| light leaking between gable and fascia | `Mlitgm1jAk8/t13m36s` |
| roof facet outside an L-shaped footprint | `Mlitgm1jAk8/t12m21s` |

**Speed.** Rebuilds are live, but each one shows an "Executing PCG Tasks (210/210)" progress toast. One building
took about ten minutes of video, including downloading the kit (SEEN). No timings are shown. The editor runs at about
70–80 FPS while editing and 6 FPS in the city overview (SEEN; hardware unknown).

### 3.2 Wall grammar tutorial (n9PHsmRlrRk)

**How it works.** Two passes of Unreal's shape grammar over a closed four-point footprint (SEEN):

1. *Vertical pass.* `Duplicate Cross-Sections` stacks copies of the footprint with the grammar
   `FirstFloor, UpperFloors*`. Floor modules are 450 cm tall. A 1438.9 cm extrusion gives three floors; the leftover
   88 cm is dropped.
2. *Horizontal pass.*
   - `Spline To Segment` turns each floor ring into wall segments and records each segment's index, length and the
     turn angles at both ends.
   - `Select Grammar` picks a segment grammar from its floor symbol, index and length. For example, with walls of 600
     cm or more:
     `[Corner][wall]*[Window][[Wall]4[Window]]*[wall]*`
     The door goes centred on segment 0 of the first floor.
   - `Subdivide Segment` cuts the segment into modules, with "accept incomplete subdivision" on
     (`n9PHsmRlrRk/t19m11s-0080.jpg`).

**Grammar syntax** (UE documentation shown at `n9PHsmRlrRk/t06m21s-0028.jpg`):

| Notation | Meaning |
| --- | --- |
| `A, B` | sequence |
| `*` | as many as fit |
| `+` | at least one |
| `[A,B]` | group |
| `[A,B]2` | group repeated twice |
| `{[A]:2,[B]:1}` | weighted random choice |
| `<A,B,C>` | priority fallback: A if it fits, else B, else C |

Two equal `wall*` fillers get equal counts, which centres the window run and the door (SAID).

**Module sizes and placement.**
- Module sizes are read from mesh bounds (Wall 50, Window 300, Door 400, Corner 172).
- Points are shifted so each mesh's front face lands on the footprint line.
- The pivot must be at bottom-left, and each mesh needs a manual placement vector
  (`n9PHsmRlrRk/t22m11s-0092.jpg`, `n9PHsmRlrRk/t28m26s-0117.jpg`).
- Trims are sampled every 50 cm along each floor ring.

**Limitations** (SEEN, INFERRED):
- every storey is the same height;
- modules cannot scale, so gaps under 50 cm remain;
- the turn angles are extracted but unused, so convex and concave corners get the same module;
- every wall shares one grammar;
- the hip roof is "not really working correctly" (SAID).

### 3.3 Roof tutorial (uzB1h3s4rzM)

**How it works.** The roof is a stack of tile rows.
- The top outline is copied once per row and raised by `sin(angle) × 40` cm.
- A PCG loop with a feedback pin moves each corner inward by one tile width, (±d, −d) in the corner's frame. The sign
  comes from the corner's turn angle.
- Rows are split into tile, edge and corner symbols and spawned as instanced meshes, 275 points per row and 26 rows in
  the example (`uzB1h3s4rzM/t10m38s-0046.jpg`, `uzB1h3s4rzM/t19m23s-0082.jpg`).

**Manual repair.** Where the shrinking outline collapses, the artist picks a row level, flags points to delete and
adds position fixes per building. Level 15 leaves a hole, 20 overlaps and 17 is right
(`uzB1h3s4rzM/t16m08s-0069.jpg` vs `t16m23s-0070.jpg`). An automatic version "did not work that well" (SAID).

**Other details** (SEEN):
- tile pitch comes from a curve the author fitted in Python;
- hip caps go at outer corners only, and valleys get no piece;
- there is no ridge module.

**Our reading:** this is a discretised straight skeleton with hand-made event handling. It works only for single-pitch
hipped roofs on right-angled footprints.

### 3.4 Road Generator (DQhydHoNuzE)

**How it works.**
- **Roads** are landscape splines with a width per control point and falloff bands for cutting and filling terrain
  (`DQhydHoNuzE/t00m53s-0005.jpg`). The asphalt appears to be a landscape paint layer rather than a mesh (INFERRED).
- **Side lines.** A PCG graph (standard or PCGEx) places along offset side lines:
  - kerbs, from a kerb grammar with a scalable last piece (`ScaleHorizontalSize` 1.13);
  - sidewalks, as grooved concrete or 25 cm tiles, 500 cm wide;
  - instanced line markings;
  - decals every 100 cm (sample distance);
  - lamps.
  (`DQhydHoNuzE/t02m23s-0011.jpg`, `DQhydHoNuzE/t02m38s-0012.jpg`.)
- **OSM import.** One paint layer per OSM highway class drives both the material and the PCG rules
  (`DQhydHoNuzE/t03m23s-0015.jpg`). The import takes a centre latitude and longitude plus a size in kilometres. Lanes
  are fixed at 300 cm. Buildings arrive as flat grey footprints (`DQhydHoNuzE/t04m34s-0031.jpg`).
- **Speed.** PCGEx is used for large networks because standard PCG slows down there (SAID). The narration says a
  network generates "within just a couple of seconds", but the video is cut and the taskbar clock skips 13 minutes
  (SEEN).

**Weak points** (SEEN, `DQhydHoNuzE/t00m15s-0002.jpg`):
- junctions are flat asphalt polygons without stop lines, arrows or kerb ramps;
- zebra crossings sit on every arm;
- sidewalk ends are cut diagonally, and median noses overlap the lanes;
- the patch decals look pasted on;
- the only furniture is one lamp model at even spacing.

## 4. Comparison with Turku3D today (CODE, 2026-11-12)

### Our facade data

`textures/library/facade-programs.json` holds 650 photographed buildings with 1,061 walls. Each field carries
`value`, `source` and `support`; an unmeasured field stays `null` and never erases a supported prior. Field coverage:

| Field | Walls |
| --- | --- |
| per-floor rows: role base/middle/top, bay count, per-bay kind (window, shop, door, unknown), head shape, surround, confidence | 301 |
| ornament level | 310 |
| wall material | 301 |
| repeated bay layout | 294 |
| measured window rectangles | 262 |
| cornice | 224 |
| string courses | 122 |
| photographed door candidates | 69 |

There are also height bands of wall material, OSM entrances, metric modernist storey profiles, classical orders,
through-arcades and blank-wall monoliths, plus register year, era, storeys, material and height.

### How it reaches the renderer

- **Pipeline.** `pipeline/turku3d/facade_programs.py`, `facade_sections.py`, `facade_modernist.py`,
  `facade_repetition.py` and `facade_program_geometry.py` compile the floor rows, sections and profiles into metric
  opening rectangles, head shapes, surrounds, bands and ornament strips per wall.
- **Shader.** The facade shader takes two storey heights per wall, ground and upper (`_KIT_FLOORS`), and paints the
  openings and relief on the surveyed LoD2 shell.
- **Instanced geometry.** `facadeModules.ts` adds only frames and sills: 50 triangles per window, none on the ground
  floor, hidden beyond 110 m ([spec](../specs/facade-modules.md)).

### Side by side

| Aspect | Building Generator | Turku3D today |
| --- | --- | --- |
| Identity source | a kit and a height | register attributes plus observations per wall, floor and bay |
| Floors | identical; leftover height dropped | base, middle and top rows with their own bays, heads and surrounds; two storey heights in the shader; metric storey levels in modernist profiles |
| Ground floor | one door, scaled up | shops, doors, arcades and entrances where observed |
| Openings | uniform grammar run | measured rectangles and counts; the grammar fills only what was not observed |
| Ornament | whatever the kit contains | observed ornament level, cornice, string courses and surrounds |
| Geometry depth | real modules: recessed openings, arches, cornices, 3D tiles | mostly painted on the shell, plus frames and sills |
| Roofs | skeleton-like mesh, gables per edge, instanced tiles near | surveyed LoD2 shell roofs snapped to lidar ([facades spec](../specs/facades.md)); colour class from the orthophoto |
| Provenance | none | per field: source and support |
| Iteration | live per actor, seconds | about 2 s live appearance patch; geometry needs a 208 s scene compile |

In short, we know far more about each building than this tool can express, but we show it more flatly than the kit
geometry it uses. The gap to close is **observation → geometry**, not observation itself.

## 5. Lessons for the world-pipeline rewrite

1. **Compile the facade program into geometry.** The Gen 2 "facade program v2 as geometry" step. Recessed openings
   with reveals, real frames and sills, arches and lintels, pilasters, and cornices and string courses become meshes
   chosen and placed by the observations. This applies to the ground floor too: shopfronts, doors and plinths. The
   videos show what real modules add at street level; our data says which ones go where.
2. **A module table as data.** Each module records:
   - a symbol and a glTF mesh;
   - its claimed length, kept separate from its visual size;
   - whether it scales;
   - its front plane and anchor;
   - its LOD variants.

   Measure module sizes from the glTF bounds and normalise pivots once, when the kit is imported. Their per-mesh
   placement vectors and the gaps they leave are the failure to avoid.
3. **A one-dimensional edge solver, fed by observations.** A compact grammar string per wall edge (`[]`, `N`, `*`,
   `+`, `{}:w`, `<>`) hashes and diffs well.
   - Observed counts and positions (`centers_m`, rectangles, floor rows) are fixed constraints.
   - The solver fills only unobserved stretches.
   - Fillers are scalable, so every edge closes exactly. Balanced fillers keep symmetric facades symmetric.
4. **Real storey heights.** Take each storey's height from its floor row or metric profile, not two heights per wall.
   A remainder goes to the ground floor or cornice and is never dropped.
5. **Corners and trims for Turku's angles.** Sweep cornice, string-course and plinth profiles along the footprint ring
   with mitred joints, and pick corner pieces by turn angle: convex, concave or obtuse. Zero-length overlapping corner
   meshes cause their sky gaps and light leaks.
6. **Roofs from a weighted straight skeleton,** fitted to lidar:
   - Edges with weight 0 become gables. Pitch comes from a plane fit per face, and ridge height from the highest
     returns.
   - Eave overhang and roof offset are fields in the building data.
   - Tile and standing-seam materials use course-aligned UVs anchored at the eave, so courses meet at the hips.
   - Ridge and hip caps and valley flashings are real geometry. Dormers are a separate step.
   - Instanced tiles at most for the nearest roofs, never city-wide.
7. **Streets on measured side lines.**
   - Store each street's measured cross-section as named offset lines: carriageway edge, kerb face, sidewalk edge and
     lane lines. Rendering, collision and multiplayer share these lines.
   - Fill kerb stones and markings along the lines as modules, with a scalable last piece.
   - Flat paving and asphalt go into the ground material, with real geometry only for kerbs and edges.
   - Junctions are where their demo is weakest. Build each junction from the offset-line intersections with kerb
     radii, put crossings only at OSM crossing nodes, and add kerb ramps and stop lines.
   - Use the OSM highway class only as a fallback when a street has no measured cross-section.
8. **Iteration like theirs, but in a workbench.** Rebuild one building or one street segment live, keyed by content
   hashes of its inputs, targeting under one second. Show edge indices, a debug colour per module symbol and a checker
   material on roofs. This is the workbench the project owner asked for (2026-11-12): a single building, a
   stretch of quay or a street piece, without the full game.
9. **Copyable styles as labelled priors.** "Copy style from building X" is useful for walls nobody photographed. The
   copy is recorded as a prior with its source and never overrides an observation of the same wall.

## 6. What not to adopt

- **A generic kit or grammar as the building's identity**, identical floors, a door-only ground floor, or random
  variation without a seed derived from the building ID.
- **Per-building manual repairs:** roof deletion levels, placement vectors, and fitted rotation curves.
- **The tile-loop roof.** It handles only right-angled, single-pitch hipped roofs.
- **City-wide per-tile instancing.** One roof in the tutorial is about 7,000 instances.
- **Unreal-only tooling.** PCG, PCGEx and Geometry Script are not available to us. The products are paid Fab assets.
- **Megascans and Fab content.** It is not CC0 or CC-BY and cannot enter the game
  ([licence policy](../../AGENTS.md#hard-limits)).

## 7. Beyond these tools: studio and research practice

A web survey on 2026-11-12. **[V]** means read in the cited source; **[U]** means a secondary source or unconfirmed.
No public source shows a studio getting recognisable real buildings at city scale from procedural generation alone.
Every pipeline that reaches that bar starts from reference for each building, chooses a kit per building and builds
landmarks by hand. The only fully automatic one, Microsoft Flight Simulator autogen, is about 20 % wrong by its
supplier's own estimate.

| Who | What they do | What it gives us |
| --- | --- | --- |
| Massive, *The Division* (Manhattan) [V [12]] | Per street, an artist checks Street View and blocks out the buildings' width, floors and distance to the street. In the Snowdrop tool each building gets a footprint and a kit; the artist picks the piece for each wall tile and sets window shapes, wall colours, and entrance positions and door types (shop or residential). | The closest analogue. Every one of these parameters can come from our register data and facade observations instead of an artist. |
| Epic, *Matrix Awakens* / City Sample [V [13]–[16]] | A Houdini graph for about 16 km² and 7,000 buildings. Each building is a volume styled by a shape grammar, one rule set per style, with separate bottom and top styles and hundreds of kit instances. 24 kits, more than 2,000 meshes. A city run takes "several minutes". | A good runtime model: volume, style grammar, plinth and upper split, kit instances. Their style is chosen by zone, which only works because the city is fictional. |
| Insomniac, *Spider-Man 1–2* [V [1]–[5]] | Houdini systems for ground, buildings, props and impostors. Research was "a lot of living in Google Maps", plus field photos and archives. Generic towers were replaced with researched buildings. Regeneration keeps valid hand edits and flags what needs attention. | Regeneration that preserves overrides and monitors what changed. Landmarks and legally excluded buildings need a track of their own. |
| Ubisoft, *AC Unity*, *Syndicate*, *Watch Dogs: Legion* [V [7]–[11]] | Ordinary buildings scaled to half or a third and built from repeating modules; Notre-Dame "closer to one to one", built from blueprints and photos. Syndicate had a road, building and sidewalk toolset. Legion placed specific buildings by hand. | Fidelity in tiers by salience. |
| Rockstar, *GTA V* [V [17]] | "Over 250,000 images and hours of video" of reference. The pipeline itself is not public. | The density of reference needed. |
| Microsoft Flight Simulator [V [19], [20]] | Blackshark autogen produced 1.5 billion buildings from imagery and learned priors, about 20 % "off", with no way to fix them one by one. Photogrammetry cities and hand-made points of interest are layered on top. | Statistical generation fails at street level. Landmark identity comes from hand-made points of interest. |
| CD Projekt Red, *Cyberpunk 2077* [V [21]] | Night City is almost entirely hand-built. Procedural tools were tried and rejected on quality. | Confirms the quality ceiling of generic generation. |
| Disney, *Big Hero 6* and *Zootopia* in CityEngine [V [23], [24]] | 83,000 buildings from the San Francisco assessor's data for every building, restyled. Same-day iteration. | Register data as the base for massing. |

Research [V unless marked]:

- **Fitting a facade program to a photo (inverse procedural modelling):**
  - Müller et al. 2007 [25]: a grammar subdivision from one facade image.
  - Wu et al. 2014 [26]: the shortest split grammar that describes a given layout.
  - FaçAID 2024 [27]: a transformer turns a segmented facade into a split-grammar program.
  - Pro-DG 2026 [28]: edits such as duplicating floors from a recovered layout.
  - Visual-to-Executable 2026 [29], [41]: a fine-tuned Qwen-VL-7B turns one street photo into an executable CGA
    program.

  These are the methods for turning our observations into exact edge programs (§5, item 3).
- **Massing:** roofer and 3DBAG produce LoD2.2 for all ~10 million Dutch buildings from footprints and lidar [31],
  [32]. They already ran on Turku tiles ([prior art](prior-art.md)).
- **LoD3 openings:**
  - Yarroudh et al. 2024 [34]: Grounding DINO on mobile-mapping images.
  - Si & Yao 2026 (Hong Kong) [35]: SAM2 segments facade components; each one is matched to a component library
    with DINOv2 (81.2 % precision@1) or generated, then merged with CSG.
  - SVI2LoD3 2026 [36]: street-view images and vision-language agents give CityGML-valid openings.

  Retrieval from a kit, rather than generating every part, is the pattern that fits our module table.
- **Gaussian splats:** CityGaussian cannot train a 1.5 km² scene on a 40 GB A100 and assumes a static scene [37].
  Lighting is baked in, collision needs proxies and the result is hard to edit [U]. Useful as reference or for a
  distant backdrop, not as game assets.
- **What makes a place recognisable:**
  - Doersch et al. 2012 [38]: the elements that set a city apart are frequent and geographically distinctive, such as
    "windows, balconies, and street signs".
  - Landmark salience [39]: a unique shape or colour, and visibility from a distance.

**Synthesis for Turku3D.** Studio practice confirms the direction of §5:

1. **Massing from data first.** Footprints, lidar and register storeys give the silhouette and roofline, the strongest
   cues at distance.
2. **A facade program per building,** fitted to evidence, drawing on **Turku-specific kits** through retrieval. This
   is what The Division does by hand and Matrix Awakens does by zone.
3. **Fidelity in tiers by salience.** Landmarks such as Rettigin palatsi, the Market Hall and the cathedral are built
   individually from the start. Ordinary buildings come from fitted programs.
4. **Signature street-level elements first:** windows, balconies, entrances, shopfronts and their signage.
5. **Regeneration that keeps manual and vision-derived overrides** and reports what changed.
6. **Fast iteration for a single building,** with a same-pose comparison against reference photos.

### 7.1 Methods, availability and licences

Checked on 2026-11-12 by reading each repository, model card, paper and LICENSE file; **[V]** unless marked **[U]**.

**Methods.** Only two of the works use a general vision-language model:
- SVI2LoD3 prompts one without further training;
- Visual-to-Executable fine-tunes Qwen-VL-7B.

The rest use:
- classic vision or optimisation: Müller, Wu, Doersch, roofer;
- networks trained for the task: FaçAID, Scan2LoD3;
- open-vocabulary detectors and segmenters: Grounding DINO, SAM 2;
- generative image or 3D models: Pro-DG's ControlNet, Si & Yao's TripoSR;
- Gaussian splats: CityGaussian, Proc-GS.

**Released work.** Most facade-specific results are unreleased, so for us they are ideas. The components they are
built from are available under permissive licences.

| Work | Method | General VLM | Released | Licence | Use for us |
| --- | --- | --- | --- | --- | --- |
| Müller 2007 [25] | Repetition detection, split grammar | No | No code, no open re-implementation | — | Implement from the paper |
| Wu 2014 [26] | Shortest split grammar for a layout | No | No code | — | Implement from the paper |
| FaçAID 2024 [27] | Transformer: segmented facade → grammar program | No | Nothing [40] | — | Idea |
| Pro-DG 2026 [28] | Layout recovery, ControlNet diffusion | No | Nothing; the paper promises code | — | Rebuild from the components below |
| Visual-to-Executable 2026 [41] | Fine-tuned Qwen-VL-7B: photo → CGA | Yes | The paper states that code, checkpoints and data are not released | — | Idea |
| SVI2LoD3 2026 [36] | SAM3 and vision-language agents | Yes, gpt-5.1 through the paid OpenAI API by default | Scripts, no weights [42] | No LICENSE file, so all rights reserved; SAM3 under Meta's own SAM License | Idea |
| Si & Yao 2026 [35] | SAM2, DINOv2 retrieval, TripoSR, CSG | No | Nothing | — | Rebuild from the components below |
| Scan2LoD3 [43] | Mask R-CNN, Bayesian network, ray casting on laser scans | No | Partial code, no weights | No LICENSE; needs commercial FME | No |
| Proc-GS [44] | Procedural code plus Gaussian splats | No | README only | — | No |
| CityGaussian [37], [45] | Gaussian splat optimisation | No | Code and V2 weights | CC BY-NC-SA 4.0, plus Inria's non-commercial rasteriser; NVIDIA CUDA only | No |
| Doersch 2012 [38] | HOG and SVM patch mining | No | MATLAB release (MIT-style) [46]; Python port [47] | MIT | MIT port (MATLAB is paid) |

**Components:**

| Model | Use | Code / weights licence | Apple Silicon |
| --- | --- | --- | --- |
| SAM 2 [48] | Prompted segmentation | Apache-2.0 / Apache-2.0 | MPS is "preliminary" in the official notebook |
| DINOv2 [49] | Retrieval embeddings | Apache-2.0 / Apache-2.0. The same repository's XRay-DINO and Cell-DINO weights are FAIR Noncommercial; avoid them | PyTorch; MPS [U] |
| Grounding DINO [50] | Text-prompted detection | Apache-2.0 / Apache-2.0 | Runs here on MPS ([facade-cv-models](facade-cv-models.md)) |
| TripoSR [51] | Image → 3D mesh | MIT / MIT | CPU fallback; no MPS path |
| ControlNet [52] | Layout-conditioned diffusion | Apache-2.0 (v1.0) / OpenRAIL (v1.1); SDXL ControlNet OpenRAIL++ | MPS through diffusers |
| Stable Diffusion 1.5, SDXL [53], [54] | Base diffusion models | CreativeML OpenRAIL-M, OpenRAIL++-M: royalty-free with use restrictions, not OSI-open | MPS documented for SD 1.5; SDXL [U] |
| FLUX.1 [schnell] [55] | Base diffusion model | Apache-2.0 / Apache-2.0 (click-through gate on Hugging Face) | MPS [U] |
| FLUX.1 [dev] | — | **Non-commercial only: do not use** | — |

**Weighted straight skeletons (gable roofs):**

| Library | Licence | Weighted | State |
| --- | --- | --- | --- |
| CGAL `Straight_skeleton_2` [56] | GPL-3.0-or-later or commercial | Yes, since CGAL 5.6 (positive weights) | Active. C++ only: the official SWIG bindings lack it |
| compas_cgal [57] | An LGPL wrapper over GPL CGAL, so GPL in practice | Weighted extrusion (`weights`, `angles`). Gable ends at 90° need `maximum_height`; whether they come out as gables is [U] | PyPI 0.10.0 with macOS arm64 wheels |
| scikit-geometry [58] | LGPL over GPL CGAL | No | Last commit 2023-12; no osx-arm64 conda build |
| bpypolyskel [59] | GPL-3.0 | No (hipped roofs only) | Not on PyPI; needs `mathutils` |
| polyskel [60] | LGPL-3.0 | No | Unmaintained since 2020 |

There is no permissive weighted skeleton. A gable roof needs either CGAL or compas_cgal running offline in the
generator, or our own implementation. Distributing a GPL tool carries GPL obligations [U, not a legal reading].

**Facade-parsing checkpoints:**
- **CMP Facade DB [61]:** CC BY-SA, 606 images, 12 classes. A Hugging Face mirror tagged MIT contradicts the source.
- **`Marco333/segformer-b0-facade-cmp` [62]:** used by our `cv-models` engine. It is tagged MIT, but its base is
  NVIDIA's SegFormer, whose licence allows research or evaluation only. The ADE20K SegFormer that we use beside it is
  NVIDIA's own checkpoint.
- **beyond-segmentation (Eurographics 2026) [63]:** YOLOv8 with a row and column alignment loss. It has no licence,
  is built on AGPL-3.0 Ultralytics and is trained on CMP.

**What we take:**
1. **Kit retrieval:** rebuild the Si & Yao pipeline from Grounding DINO, SAM 2 and DINOv2 (Apache-2.0), with
   TripoSR (MIT) only for parts the kit lacks. All of it runs on the M1 Max.
2. **Grammar fitting:** we write it ourselves from Müller, Wu and FaçAID. It turns approximate vision-model
   observations ("about 9 openings, arched") into an exact facade program (§5, item 3).
3. **Pro-DG-style facade images:** ControlNet with FLUX.1 [schnell] or Stable Diffusion, never FLUX.1 [dev].
4. **Before any commercial use,** replace the research-only SegFormer weights, for example with SAM 2 prompted from
   DINO boxes.

Sources:

- [1] https://gdcvault.com/play/1025765
- [2] https://www.sidefx.com/community/houdinihive_gdc2019/
- [3] https://history.siggraph.org/?p=82780
- [4] https://80.lv/articles/spider-man-a-deep-dive-into-the-look-creation-of-manhattan
- [5] https://artdaily.com/news/162146/Spider-Man-2-s-New-York-is-a-web-of-skyscrapers-and-brownstones
- [7] https://www.pinaultcollection.com/en/node/56
- [9] https://blog.siggraph.org/?p=5940
- [10] https://gdcvault.com/play/1023305/-Assassin-s-Creed-Syndicate
- [11] https://ubisoftsingapore.com/post/how-realistic-in-game-cities-are-made
- [12] https://80.lv/articles/division-pt-1
- [13] https://forums.unrealengine.com/t/the-matrix-awakens-an-unreal-engine-5-experience-download-now/265329
- [14] https://dev.epicgames.com/documentation/unreal-engine/city-sample-project-unreal-engine-demonstration
- [15] https://dev.epicgames.com/documentation/unreal-engine/city-sample-quick-start-for-generating-a-city-and-freeway-using-houdini
- [16] https://www.cgchannel.com/?p=135314
- [17] https://laist.com/news/kpcc-archive/grand-theft-auto-v-show-us-your-la-screen-shots
- [19] https://techcrunch.com/?p=2032098
- [20] https://www.flightsimulator.com/world-update-20/
- [21] https://80.lv/articles/interview-how-cyberpunk-2077-s-night-city-was-built-almost-entirely-by-hand/
- [23] https://www.ajc.com/entertainment/movies/san-fransokyo-architects-built-new-world-for-disney-big-hero/OlWwIhuTTPOl9aTV3gvfYL/
- [24] https://www.gpsworld.com/esri-uc-how-cityengine-powered-disneys-zootopia/
- [25] https://history.siggraph.org/?p=115394
- [26] https://arxiv.org/abs/1308.0419
- [27] https://arxiv.org/abs/2406.01829
- [28] https://arxiv.org/abs/2504.01571
- [29] https://neurips.cc/virtual/2026/poster/155329
- [31] https://github.com/3DBAG/roofer
- [32] https://www.tudelft.nl/en/2021/bk/all-10-million-buildings-in-the-netherlands-available-as-3d-models
- [34] https://isprs-archives.copernicus.org/articles/XLVIII-2-W8-2024/471/2024/
- [35] https://isprs-archives.copernicus.org/articles/XLIX-B2-2026/1267/2026/
- [36] https://arxiv.org/abs/2608.29992
- [37] https://arxiv.org/html/2404.01133v3
- [38] https://publications.ri.cmu.edu/what-makes-paris-look-like-paris
- [39] https://www.frontiersin.org/articles/10.3389/fcomp.2022.1044151
- [40] https://facaid.github.io/
- [41] https://arxiv.org/abs/2611.03143
- [42] https://github.com/hcu-cml/citydb-SVI2LoD3-ai
- [43] https://github.com/OloOcki/scan2lod3
- [44] https://github.com/city-super/ProcGS
- [45] https://github.com/Linketic/CityGaussian
- [46] https://graphics.cs.cmu.edu/projects/whatMakesParis/
- [47] https://github.com/Timalph/Patch-Doersch
- [48] https://github.com/facebookresearch/sam2
- [49] https://github.com/facebookresearch/dinov2
- [50] https://github.com/IDEA-Research/GroundingDINO
- [51] https://github.com/VAST-AI-Research/TripoSR
- [52] https://huggingface.co/lllyasviel/ControlNet-v1-1
- [53] https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5
- [54] https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0
- [55] https://huggingface.co/black-forest-labs/FLUX.1-schnell
- [56] https://doc.cgal.org/latest/Straight_skeleton_2/
- [57] https://github.com/compas-dev/compas_cgal
- [58] https://github.com/scikit-geometry/scikit-geometry
- [59] https://github.com/prochitecture/bpypolyskel
- [60] https://github.com/Botffy/polyskel
- [61] https://cmp.felk.cvut.cz/~tylecr1/facade/
- [62] https://huggingface.co/Marco333/segformer-b0-facade-cmp
- [63] https://github.com/Musialski-Research-Group/beyond-segmentation

## 8. Next experiment

One building, end to end in a workbench:

- **Building.** Linnankatu 16 (`103295896N`, 1887, three storeys, rendered, 19th-century classicism). Its walls are
  fully described in the facade programs, and it lies in the first corridor.
- **Pipeline.** Facade program → edge solver → real module geometry (openings with reveals, frames, sills, surrounds,
  string courses, cornice) → skeleton roof fitted to lidar → glTF.
- **Measurements:**
  - rebuild time per building;
  - triangles and draw calls;
  - a same-pose comparison with the current shell in the office views.
- **Acceptance:**
  - a judgement against local reference photos by someone who knows the street;
  - a `scripts/dev/scene-judge.mjs` gain larger than the repeat spread. The riverside-office baseline on 2026-11-12
    was 45.7 ± 2.5 of 100 (city.lab `d3e2021d`). Its weakest categories were materials (3.7) and street (3.7).

Repeat with a Humalistonkatu apartment block (shopfronts) and a Portsa wooden house (cladding, gables, porch) before
any city-wide generation.
