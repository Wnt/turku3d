# Coarse (draft) tiles

One 250 m L0 tile in the published tile format, built in seconds from the fast sources, for the localhost game beyond
the published tiles (`game/src/world/coarseTiles.ts`). The full pipeline replaces a draft when the tile is published.

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.coarse build t0_2_-1            # -> $TURKU3D_WORK/coarse/t0_2_-1/
bash pipeline/env/run.sh python -m turku3d.coarse build t0_2_-1 --out DIR
tools/dev/coarse-tiles                                                    # the service, port base + 5
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse.py
```

## Inputs (`inputs.py`)

Cached shared raw data first (an area such as `work-3x3` that covers the tile), the read-through fetchers otherwise:
the `maanpinta` ground key points (tile + 20 m), one 1024 px true-ortho WMS image, and the WFS layers CityGML LOD2
buildings, street areas (`GIS:Katualueet`) and water (`kanta:Vesikohde`). The first tile in a new direction downloads
its raw data inside the build (up to a few minutes); warm tiles build in 5–10 s.

## Output (`build.py`)

`<out>/manifest.json` (format `turku3d-coarse-tile` v1: `visual`, `collision`, `buildings`, `bridges`,
`water_level`, `sources`, `seconds`, and `code`, the producer hash the service compares), `vis.glb` and
`col/c_i_j.col` for all 16 collision cells; shared KTX2 textures go to `<out>/../shared/`.

- **Ground**: a 128 x 128 cell grid (~2 m) interpolated from the key points, textured with the ortho. Visual and
  collision use the same vertices, so the drawn ground is the collision ground. Street-area polygons set the
  collision material; green ortho pixels are grass, the rest paving.
- **Water**: cells inside water polygons become the water sensor at the shoreline level over a synthetic bed
  (`ground.water.add_river_bed`).
- **Bridges**: `maanpinta` has no points on a deck, so a street-area bridge (`Silta`) over water would lie at river
  level between quay-high road ends. `bridge_decks` lifts each deck patch that touches water to the line between
  its abutments (the 90th percentile of the dry ground 5–25 m beyond each end, inside the deck's corridor), raising
  the low approach cells in the corridor with it; decks over dry land stay put. A water polygon under a deck never
  turns it into the sensor. Tuomiokirkkosilta (`t0_2_-1`) now spans the Aura at 6.8–7.0 m instead of 0.2 m.
- **Buildings**: LOD2 footprints whose centroid is in the tile, extruded to the mean roof height; the collision shell
  goes to the centroid's collision tile.

No curbs, stairs, structures, props, vegetation, facade maps or LOD parents.

## Service (`service.py`)

`tools/dev/coarse-tiles` (localhost only, port `$TURKU3D_PORT_BASE + 5`, proxied at `/coarse/` by the dev and dist
servers). Routes and queueing: `tools/dev/README.md`. Built tiles are reused until the producer code changes, so a
producer fix rebuilds each draft the next time the game asks for it.
