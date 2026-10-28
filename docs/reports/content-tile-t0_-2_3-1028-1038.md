# Playable tile t0_-2_3

## Result: DONE

`t0_-2_3` covers E 23459000–23459250, N 6703500–6703750. `make tile-add T=t0_-2_3` built and put it in the per-tile store; `list-tile` added it to the shared playable list. The tile is 7,589,019 bytes on the wire, below the 8,000,000-byte cap (5,310,664-byte GLB, 4,171,209-byte Brotli geometry, 3,417,810 bytes of ground textures).

## What changed

The tile has ground, buildings, structures, a Betanianpolku crossing, props, vegetation and parked cars. It owns 21 building shells, 82 road records, one crossing deck, 115 props, 155 trees and 22 parked cars. The structure layer records 17 stair flights, 52 fences and 16 walls. The crossing source and output counts are both one. The 2022 true ortho shows the stadium, roads, residential blocks, trees and the pedestrian crossing in the same broad locations as the headless top-down and oblique geometry renders. I also rendered neighbour `t0_-2_2` in both views and compared it with its source ortho: two bridge decks match the two visible crossings. No obvious duplicate decks, floating buildings or water crossings appeared. Source and QA images for both tiles remain under `$TURKU3D_WORK/qa/`.

The resumed build exposed two missing-data cases outside this tile. A building with no valid 2022 true-ortho roof pixels now gets an explicit gray roof fallback instead of stopping all tiles. A building whose LOD2 roof parts lie below its recorded base now gets a flat shell at its recorded top height instead of an empty mesh. Both cases have synthetic regression fixtures. No raw files were removed.

The successful cached retry spent 0.126 s checking inputs, 4.086 s checking ring inputs, 0 s seeding, 98.584 s refreshing layers, 198.941 s on fast facades, 799.668 s assembling and 0.956 s putting the tile in the store: 1,102.365 s total. Within refresh, outer buildings took 31.702 s and props 59.322 s; the other producers reused cached outputs. Earlier cold work was interrupted by a Mac reboot during assembly, so these timings describe the successful retry.

Fast facades published rough maps for all 109 planned walls: 28 reference-derived, 39 building-derived and 42 register-derived. The model service returned HTTP 429 for 47 of 85 description jobs; 24 walls had no usable reference. The tile still has the published fallback maps. Three target-tile structure objects failed their real-data Rapier traversal and were omitted by the existing producer.

## How to verify

```sh
source ../sandbox.env
cat "$TURKU3D_WORK/reports/tile-add/t0_-2_3.json"
node tools/validator/src/cli.ts --checks format,seams --workers 2 --out "$TURKU3D_WORK/reports/validate-t0_-2_3.json" "$TURKU3D_WORK/dist/tiles/collision/t0_-2_2" "$TURKU3D_WORK/dist/tiles/collision/t0_-2_3"
bash pipeline/env/run.sh python -m turku3d.region quality
```

The focused validator passed format and all shared collision seams, including the edge with built neighbour `t0_-2_2`. Road records were generated for both tiles and the rendered road surface meets at that edge. The QA render files are `top-layers.png`, `oblique-layers.png` and `source-ortho.jpg` in each tile's sandbox QA directory. Google camera comparison was not run while the shared browser slot remained occupied; the local source ortho comparisons do not claim pose alignment.

## Gate output

`scripts/dev/gate --fast --base main`: GREEN in 1.746 s (env-sync and policy passed; generated and python-lint cached PASS; deep and browser stages skipped by the fast gate).

## Follow-ups

Retry the 47 rate-limited facade descriptions when model service capacity returns. Jonni's playtest can identify any remaining driving or traversal problems; the three omitted structures are recorded in the structures metrics for this tile. Run `make gmaps-compare` when the browser slot is free, treating its N2000 camera height hypothesis as uncalibrated.
