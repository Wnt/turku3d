# Environment assets: how GTA V builds them, and what Turku3D ships

Turku3D's trees were one faceted Kenney prototype in an ochre autumn colour, there were no shrubs or grass, and
the sky was a cloudless gradient. Branch `local/assets-nature` adds an asset library
(`game/public/assets/nature/`, [README](../../game/public/assets/nature/README.md)) and draws the tile vegetation
and the sky with it. This note records the generic findings from GTA V that set the budgets and techniques. It
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
| Near broadleaf tree | ~5,000 tris, ~3 draws, ~270 cluster cards of ~1.6 m | 2,100 to 7,300 tris (rowan to willow), 2 draws, 320 to 640 bent cards of 1.0 to 1.35 m |
| Near conifer | ~7,750 tris | Scots pine 2,400, Norway spruce 9,600 |
| Mid level | 0.3 × near, same cards | 0.14 to 0.25 × near (spruce 0.21), the same canopy as flat cards |
| Shadow caster | proxy at 0.4 to 0.5 × visible | LOD 1 on the shadow-only layer (0.14 to 0.25 ×) |
| Far | 2 to 4 crossed quads from 280 m, a shared billboard mesh | 3 crossed planes + 1 top plane (8 tris) from 180 m, angle-faded and dithered |
| LOD switches (medium) | 60 / 160 / 280 / 400 m | 50 / 180 / 650 m (`quality.ts`) |
| Leaf texture | 512² to 1024² per species | one 2048² atlas, 512² per species card, shared by all |
| Bark | 512×1024 + normal per species | one 4096×512 column atlas + 2048×256 normal, 8 barks |
| Bush | ~400 tris | lilac 990, rose 660, juniper 1,620 (LOD 1: 120 to 510) |
| Grass clump | ~86 tris | 6 tris (3 crossed cards), rosette 2 tris |
| Fence | ~300 tris per 4 m module | 100 to 310 tris per 2 m module, LOD 1: 20 to 80 |
| Sky | analytic dome + noise clouds + textured cloud hats | analytic SkyMesh + two noise cloud layers (cumulus, cirrus) from one 256² tileable texture |
| Wind | vertex weights + global wind | `_WIND` vertex weights (sway, flutter, phase) + global strength/direction uniforms |

All trees, shrubs, grass and the hedge share **three materials**: bark, leaves (alpha-tested, double-sided,
crown normals, back-light term) and impostor. The fence kit adds wood, metal and granite. All instances in a tile
pack into one draw per asset part per LOD, so pipeline count does not grow with instance or species count. The
library is a 6 MB GLB with KTX2 and meshopt.

Departures from GTA, and why:
- **Mid level.** Our cards are cheap (2 to 4 triangles), so the mid level keeps every card flat and drops the
  twigs. The silhouette does not change at the switch.
- **Far level.** The impostor goes further (650 m) instead of a batched parent billboard. Tiles are 250 m and a
  far tree costs 8 triangles.
- **Sky.** The clouds are a cheap two-layer screen-space noise in the sky shader rather than meshes, because the
  sky is the only consumer and there is no weather system yet.

## Evidence

<!-- evidence:begin -->
<!-- evidence:end -->
