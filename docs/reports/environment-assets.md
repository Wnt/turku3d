# Environment assets: how GTA V builds them, and what Turku3D ships

Turku3D's trees were one faceted Kenney prototype in an ochre autumn colour, and there were no shrubs or grass.
Branch `local/assets-nature` adds an asset library (`game/public/assets/nature/`,
[README](../../game/public/assets/nature/README.md)) and draws the tile vegetation, grass and barriers with it. A
two-layer cloud deck was prototyped here (commits `ebf142df`, `d01847ce`) and then handed to the sky loop, which
owns sky and clouds; the branch no longer changes the sky.

This note records the generic findings from GTA V that set the budgets and techniques. It
contains counts, sizes and techniques only. No GTA mesh, texture, name or per-asset table was copied.

## Method (GTA side)

The research PC dumped GTA V Enhanced (story-mode data, latest DLC level) offline with an extended CodeWalker.Core
tool. The game was not started. Samples:

- every placed broadleaf (39) and conifer (15) tree archetype;
- 50 bushes, 10 hedges, 60 grass archetypes, 60 fences, 40 railings and 17 low walls;
- the LOD chains of all 29,704 placed non-palm trees;
- all 56,693 grass instance batches;
- the sky dome, the cloud-hat meshes and textures, and the cloud and timecycle files.

Raw dumps and keyword rules stay on the research host. The longer local note is
`~/turku3d/data/research/gta/environment-assets.md`.

## Findings that set our design

**Trees**
- **LOD chain.** Full mesh to about 60 m, then a reduced mesh to about 160 m, and the tree is drawn to
  about 280 to 300 m. A shared parent billboard (2 to 4 crossed quads per tree, batched for about 5 trees) takes
  over from 280 to 400 m. Beyond that a city tree is usually not drawn.
- **Budget.** A near broadleaf has about 5,000 visible triangles in about 3 draws; a conifer about 7,750. The
  reduced level is about 0.3 × the full level.
- **Canopy.** It is made of about 270 alpha-tested *cluster* cards per broadleaf tree. Each is about 1.6 m across
  and slightly bent (about 3 triangles), so a card shows a whole branch tip. Conifers use about 155 bent strips.
  The reduced level keeps the same cards and saves on trunk and branches.
- **Leaf shading.** A canopy-volume normal replaces the card normal, so the crown shades as one volume.
- **Shadows.** Newer trees cast their shadow with a separate proxy of about 0.4 to 0.5 × the visible triangles.
- **Textures.** Leaf atlases are 512² to 1024² with alpha, bark 512×1024; about 2.6 MB per species including mips.
  Billboards are 64² to 256².
- **Wind.** Vertex weights carry it, all in the vertex shader: sway amplitude rising with height, a random
  per-card phase and a secondary bend weight, plus AO. Global wind and per-material micro-movement of about 2 to
  4 cm drive them.

**Bushes, hedges, grass**
- **Bushes.** About 400 triangles of about 90 cards, switching at 60 m and drawn to 100 m.
- **Hedges.** An opaque leafy core wrapped in cards.
- **Grass.** Clumps of about 86 triangles, one 512² texture per clump. They are instanced in batches of about
  500 over about 100 m squares, start fading at about 18 m and are gone by 100 m. Clumps are about 1.2 m apart.
  Urban streets mostly have none: lawns are ground texture.

**Fences and walls**
- **Fences.** About 4 m modules of about 300 triangles, with alpha-tested mesh panels near and whole-fence cards
  at distance. They switch at 50 m and are drawn to 100 m.
- **Railings and low walls.** Railings are real geometry at every distance. Low walls are mostly part of the map
  meshes.

**Sky and clouds**
- **Sky.** An analytic sky shader on a low-poly dome, driven by about 90 parameters per weather and 13 keys a
  day, includes a procedural noise cloud layer.
- **Cloud hats.** Usually 5 layered dome meshes of about 500 triangles each, with 1024² to 2048×512 density and
  normal maps and 3 scrolling UV layers.
- **Cloud lighting.** Silver-lining and forward-scattering terms and separate sun, ambient and east/west colours,
  keyed 15 times a day.

## What Turku3D ships

| | GTA V (median) | Turku3D library |
|---|---|---|
| Near broadleaf tree | ~5,000 tris, ~3 draws, ~270 cluster cards of ~1.6 m | 2,400 to 7,300 tris (rowan to willow), 2 draws, 380 to 700 bent cards of 1.35 to 1.7 m; two seeded variants per species |
| Near conifer | ~7,750 tris | Scots pine 2,400, Norway spruce 9,100 to 9,600 |
| Mid level | 0.3 × near, same cards | 0.24 to 0.42 × near (median about 0.3), the same canopy as flat cards (spruce keeps 60 % of its cards, larger) |
| Shadow caster | proxy at 0.4 to 0.5 × visible | LOD 1 on the shadow-only layer (0.24 to 0.42 ×) |
| Far | 2 to 4 crossed quads from 280 m, a shared billboard mesh | 3 crossed planes + 1 top plane (8 tris) from 180 m, angle-faded and dithered; 128×256 texel views |
| LOD switches (medium) | 60 / 160 / 280 / 400 m | 50 / 180 / 650 m (`quality.ts`) |
| Leaf texture | 512² to 1024² per species | one 2048² atlas, 512² per species card, shared by all |
| Bark | 512×1024 + normal per species | one 4096×512 column atlas + 2048×256 normal, 8 barks |
| Bush | ~400 tris | lilac 960 to 990, rose 660 to 690, juniper 1,450 to 1,620 (LOD 1: 330 to 810) |
| Grass clump | ~86 tris, ~1.2 m apart, drawn to 100 m | 6 tris (3 crossed cards), rosette 2 tris; patch-noise placement at 2.5 candidates/m² on tile lawns, dissolving at 34 m (medium) |
| Fence | ~300 tris per 4 m module | 70 to 310 tris per 2 m module (picket, board, railing, park, chain-link, granite), LOD 1: 20 to 80; chain-link as an alpha-tested panel |
| Sky | analytic dome + noise clouds + textured cloud hats | not on this branch (prototype: two noise cloud layers from one 256² tileable texture, handed to the sky loop) |
| Wind | vertex weights + global wind | `_WIND` vertex weights (sway, flutter, phase) + global strength/direction uniforms |

All trees, shrubs, grass and the hedge share **three materials**: bark, leaves (alpha-tested, double-sided,
crown normals, back-light term) and impostor. The fence kit adds wood, metal and granite. All instances in a tile
pack into one draw per asset part per LOD, so pipeline count does not grow with instance or species count. The
library is a 6.6 MB GLB (33 assets) with KTX2, quantized positions/normals and meshopt.

Departures from GTA, and why:
- **Mid level.** Our cards are cheap (2 to 4 triangles), so the mid level keeps every card flat and drops the
  twigs. The silhouette does not change at the switch.
- **Far level.** The impostor goes further (650 m) instead of a batched parent billboard. Tiles are 250 m and a
  far tree costs 8 triangles.

## Evidence

<!-- evidence:begin -->
Street bench (`game/scripts/street-bench.mjs`), this M1 Max, headless Chrome 156, WebGPU, medium quality, built
bundles of `local/street-richness` (before) and this branch at `00b0b7b7` (after: species trees, LOD1 shadow
proxies, and the cloud prototype that has since moved to the sky loop; no grass or fence kit yet). GPU ms per
frame, p50 / p95, 240 frames after a settle:

| Camera | 13:00 before | 13:00 after | 22:00 before | 22:00 after |
|---|---|---|---|---|
| riverside | 9.4 / 11.0 | 11.5 / 12.8 | 8.8 / 10.7 | 10.4 / 11.7 |
| shopping | 10.6 / 12.7 | 9.0 / 9.8 | 7.6 / 7.8 | 9.1 / 10.3 |
| residential | 7.2 / 8.3 | 7.7 / 8.9 | 5.5 / 5.6 | 7.8 / 8.8 |
| park-edge | 8.6 / 9.4 | 10.3 / 11.3 | 7.4 / 8.2 | 9.2 / 10.3 |
| kupittaa (dense forest) | 8.5 / 9.8 | 14.2 / 16.1 | 7.5 / 8.9 | 11.8 / 13.7 |

Pipelines rose by 1 to 3 per camera (63 to 71). The worst case, Kupittaa's 768-tree forest tiles, costs about
+6 ms GPU and stays at about 70 fps, well inside the 30 fps target. Caveat: the host ran several other agents
(load average 15 to 240 during the runs), so single-camera differences of about 1 ms are noise. The shopping
camera came out faster after, which shows the size of that noise.

Before/after shots at the `turku3d-compare` poses are kept locally under
`~/turku3d/work/assets-nature/compare-after/`, `~/turku3d/work/assets-nature/compare-grass/` and the review
gallery `~/turku3d/loops/assets-gallery/index.html`. They are not published, per the repository's screenshot rule.
<!-- evidence:end -->
