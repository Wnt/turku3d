# lodfade handover: GTA V-grade LOD hand-overs (2026-11-04)

Jonni asked how GTA V hides its LOD changes and for ours to match, judged by a vision model on video captures.
Captures, strips, judge outputs and GTA material stay local in `~/turku3d/loops/lodfade/` (never in git).

## What GTA V does (research PC, lowest preset, 2026-11-04 plus techstudy's 2026-11-03 clips)

- **No cross-fade.** HD to LOD is a one-frame swap; LOD to HD switches geometry in 1-2 frames, then the HD
  textures stream in over about 0.3-1.6 s (blurry first). No dither is visible at 1:1.
- **Parity, not fading, hides it.** The LOD model is the same silhouette with a baked, heavily blurred copy of the
  HD textures, so the mean colour of every wall and roof survives. At the switch distance (about 170-250 m in 3D)
  a building is small; magnified, the pair still differs a lot (mean dE 15.6 at the switch size, LOD about 8.5 L
  brighter: no dark window recesses), but the colour layout is the same.
- **Hysteresis by distance only**, per object, on 3D distance (HD drawn to p50 172 m, LOD parent hands over at
  167 m; measured switches at 212-270 m). Props and palms drop at 265-515 m.
- **Occlusion and motion do the rest.** A 40 s AI drive, a 30 s sprint and a 20 m/s fly-out through downtown
  (`gta/drive15.mp4`, `walk-sprint.mp4`, `pullout-70m.mp4`) show no LOD event a motion-compensated pop scan could
  separate from parallax: streets occlude the 200 m band, and what is visible changes size slowly.

## Turku3D before (integration a350f0eb)

- Block shells (280 m swap, `buildingPlacements.ts`) dissolve over 0.6 s with a TRAA-resolved screen door.
- **The shells did not match their HD buildings.** Every shell roof was the fixed `blocks.py` ROOF pigment
  (.20, .23, .22): light, red and brown roofs turned grey-green at 280 m. This was the most visible change in the
  forced-swap clips and the judge's top artefact ("warm facade/roofs shift to cool blue-grey").
- 5 of 40 block swaps in the drive replay reversed within 10 s (10 m hysteresis band): each a second dissolve.

## What changed (client only, city-wide, no tile regeneration)

1. **Shell roofs sample their HD roof photo** (`render/sceneryMaterial.ts` `shellRoofs`, `shellPigment`). Orthophoto
   roofs use a planar projection, so a per-tile least-squares fit of the HD roof UVs over world x/z gives the
   mapping; shell roof pixels (vertex colour = ROOF) sample the same texture there. The GPU mip chain gives GTA's
   blurred-HD LOD look. Maps whose roofs are not one planar projection keep the old pigment.
2. **Shell walls mix in their facade-kit trim share** (`shellWallPigments`, `trimShare`): surrounds, courses,
   cornice and corner bands as painted by `facadeProgram.ts`. Small effect: most HD walls near the river are
   landmark or atlas facades, not kits.
3. **Block swap hysteresis 10 -> 20 m** (`SWAP_BAND`).
4. **Fade length: 0.6 s stays** (the open decision). Blind judge, forced swaps at two poses, both directions, overall
   0-10 summed over the four strips: 0 s 15, 0.15 s 14, 0.3 s 18, **0.6 s 22**, 1.0 s 21. GTA can swap in one frame
   because its LOD carries the HD colours and the swap hides behind occlusion; ours still changes window pattern
   and some landmark wall colours, and a 0.6 s dissolve reads as a soft change instead of a pop.

## Judge scores (blind `rank`, domain lod, overall 0-10, 10 = unnoticeable)

| Strip | GTA V (simulated switch size) | Turku3D before | After roofs | After roofs + walls |
| --- | --- | --- | --- | --- |
| Riverside, HD -> shell | 1 | 4 | 6 | |
| Riverside, shell -> HD | 2 | 4 | 7 | |
| Elevated riverside, HD -> shell | 1 | 5 | | 5 |
| Elevated riverside, shell -> HD | 2 | 6 | | 6 |

GTA's strips are its own 70 m focus-swap clip downscaled 3.6x to the 250 m screen size: magnified it is a hard pop
to a blurry facade; in play the same pop is rarely seen (occlusion, motion). Judged the same way, our dissolving
shells already rate above it; the remaining Turku3D artefacts are the window grid and landmark wall colours.

## Perf (Mac M1 Max, medium, `bench=static` at the elevated riverside pose, frozen builds, 5 interleaved rounds)

FPS 59.9 both (vsync). GPU p50 median delta -0.3 ms (range -1.9..+0.8), draws 382 -> 390 (+2 %: one merged far
parent per roof photo), triangles +0.4 %. CPU p50 drifted from 9 to 13 ms over the session with host load; paired
deltas +0.7..+2.7 ms with the base first and -0.6/+1.6 ms reversed, so no reliable CPU effect is resolved. The roof
fit runs once per tile install (at most 64 samples per roof mesh).

## Method (reusable)

- `~/turku3d/loops/lodfade/tools/lodswap.mjs OUT 'E,N,H,heading,pitch,fov' [query]`: the GTA "streaming focus"
  method for Turku3D. Loads `bench=static`, records the canvas (MediaRecorder), and moves every in-view block at
  150-420 m across its switch (HD -> shell at +1 s, back at +3.5 s), so the swap happens at its real screen size.
- `strips.sh NAME`, `mkstrip.py`: 12-frame strips at 100 ms; `parity.py`: dE of an HD/LOD pair at the switch size;
  `popscan.py`: motion-compensated pop finder for moving clips; `tools/rec.mjs`: canvas WebM of any bench run.
- Judge: `~/turku3d/tools/visual-judge/judge.py --domain lod rank --img A --img B ...` (new blind multi-image
  mode and LOD prompt; falls back to the Claude CLI while the Codex quota is out).

## Round 2: shell-look@1 migration and orthophoto roofs (generator parity, for train3)

- **`pipeline/turku3d/migrations/shell_look.py` (registered last in `CHAIN`).** For each HD placement the measurer
  `shell_colours.mjs` (Node, the repo's own Basis transcoder in `game/public/basis`, gltf-transform from
  `TURKU3D_GLTF_TOOLS` or `~/turku3d/tools/npm`) reads the HD model as the chain left it and returns the area-weighted
  roof texture mean, the wall mean (textured walls: texture mean; facade kits: the module atlas cell tinted by wall and
  trim, trim bands mixed in), the kit grid (bay, storey, opening width and height fractions) and the footprint. Each
  block-shell triangle takes its building's roof or wall mean; wall vertices gain `_SHELL_GRID`. The JS file's hash is
  pinned in `MEASURER_SHA256`, so editing it changes the generation id. About 4-5 s per tile.
- **Order:** it measures the HD models after every earlier step, so a facade migration (e.g. landmark windows) must
  sit **before** `shell-look` in `CHAIN`, or shells keep the old facade colours.
- **Scenery cells** copy the farm block shells' COLOR_0 (`tools/remote-drafts/scenery.mjs`), so they inherit the new
  colours once they rebuild from migrated products.
- **Survey shells** (250 m survey cells, `horizon.shells`; 1 km nodes, `horizon_nodes.survey_shells`) take each
  roof's orthophoto mean (`coarse/roof_pigment.py`, about 2 m per pixel) instead of ROOF. The orthophoto paths join
  the rows' inputs and hashes; `roof_pigment.py` joins the horizon recipe, so local-horizon rebuilds nodes on demand
  after its restart from this code (not done: that is the coordinator's switch).
- **Client:** shell roofs still sample the HD roof photo where its UVs are a planar projection, now found by face
  normal; glazed shells with `_shell_grid` draw their windows on the real bays, storeys and opening sizes
  (`buildings-lod-glazed-grid`). The client-side trim recolouring moved into the migration.
- **Test alias:** `lodfade-m1` (registered from bbe15120, base `bulkgen2-3d1445b2`), applied to t0_1_-2, t0_1_0 and
  t0_1_-1, t0_2_-1, t0_2_0. Serve it with `follow: "lodfade-m"` in a sandbox `tile-service.json`. The full chain on a
  fresh tile takes about 170 s (lidar steps); a chain that extends train2's products only runs this step.

### Round 2 results (forced swaps, Mac headless on a host lease, migrated tiles via `follow: lodfade-m`)

Blind judge, overall 0-10 (GTA V / before = integration + pin tiles / after = shell-look + client):

| Strip | GTA V | Before | After |
| --- | --- | --- | --- |
| Riverside, HD -> shell | 1 | 6 | 7 |
| Riverside, shell -> HD | 2 | 5 | 6 |
| Elevated riverside, HD -> shell | 1 | 4 | 6 |
| Elevated riverside, shell -> HD | 1 | 6 | 8 |

Measured: the mean colour of each swapping block against its HD render, pixel-weighted over 27 blocks at both
poses, went from dE 1.49 to 0.63. The judge's remaining artefacts: window rows change contrast during the fade, and
some mid-distance buildings lose contrast (shells are flat-shaded and roughness 1).

## Open, ranked

1. **Landmark and atlas facade walls** keep a shell pigment far from their HD texture (the judge's top artefact at
   the elevated riverside pose: a pale-pink facade turns dark brick red). Fix in `blocks.py` (wall colour from the
   HD wall texture mean) with a tilepatch migration, or a client top-mip readback of the HD wall maps.
2. **Window pattern:** shells use a fixed 2.6 x 3.2 m grid; HD kits know bay, storey and opening size.
3. **Scenery cells (L0) and 1 km nodes** still use the fixed ROOF pigment (`scenery.mjs`); give them the same
   roof photo sampling or a baked mean.
