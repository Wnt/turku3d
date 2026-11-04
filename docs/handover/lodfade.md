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
4. **Fade length:** see the decision below.

## Method (reusable)

- `~/turku3d/loops/lodfade/tools/lodswap.mjs OUT 'E,N,H,heading,pitch,fov' [query]`: the GTA "streaming focus"
  method for Turku3D. Loads `bench=static`, records the canvas (MediaRecorder), and moves every in-view block at
  150-420 m across its switch (HD -> shell at +1 s, back at +3.5 s), so the swap happens at its real screen size.
- `strips.sh NAME`, `mkstrip.py`: 12-frame strips at 100 ms; `parity.py`: dE of an HD/LOD pair at the switch size;
  `popscan.py`: motion-compensated pop finder for moving clips; `tools/rec.mjs`: canvas WebM of any bench run.
- Judge: `~/turku3d/tools/visual-judge/judge.py --domain lod rank --img A --img B ...` (new blind multi-image
  mode and LOD prompt; falls back to the Claude CLI while the Codex quota is out).

## Open, ranked

1. **Landmark and atlas facade walls** keep a shell pigment far from their HD texture (the judge's top artefact at
   the elevated riverside pose: a pale-pink facade turns dark brick red). Fix in `blocks.py` (wall colour from the
   HD wall texture mean) with a tilepatch migration, or a client top-mip readback of the HD wall maps.
2. **Window pattern:** shells use a fixed 2.6 x 3.2 m grid; HD kits know bay, storey and opening size.
3. **Scenery cells (L0) and 1 km nodes** still use the fixed ROOF pigment (`scenery.mjs`); give them the same
   roof photo sampling or a baked mean.
