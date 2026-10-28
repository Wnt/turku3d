# t0_2_-1 playable tile

Tile bbox: E 23460000–23460250, N 6704500–6704750. The data-root playable list now includes the tile, and its per-tile store entry was published by `make tile-add T=t0_2_-1`.

## Contents and size

- 14 buildings, 75 structures, three bridge records (Tuomiokirkkosilta and two OSM pedestrian crossings), 137 road records, 233 props, 233 trees, and 22 parked cars.
- Visual layers: ground, buildings, structures, bridges, props, vegetation, traffic_static. All 16 collision cells are present.
- Visual GLB 4,790,780 bytes; Brotli visual 3,338,672 bytes; ground textures 3,342,853 bytes; total wire 6,681,525 bytes, under the 8,000,000-byte cap. 252,645 visual triangles.
- Source input area `work-3x3` was complete; no raw inputs had to be fetched. Ground uses 2025 ortho/splats. No source-data coverage blocker was found.

## Wall times

| Stage | Initial build | Repair build |
| --- | ---: | ---: |
| Input check | 0.9 s | 0.2 s |
| Input ring | 15.7 s | 4.8 s |
| Seed | 9.6 s | 0.0 s |
| Refresh | 2,068.6 s | 22.1 s |
| Fast facades | 55.2 s | 686.8 s |
| Assemble | 1,614.6 s | 280.5 s |
| Put tile | 3.2 s | 3.4 s |
| Total | 3,767.8 s | 997.8 s |

Initial refresh producer wall times: ground 410.0 s, buildings 637.4 s, structures 293.3 s, facades 20.3 s, bridges 27.7 s, roads 1.8 s, landmarks 2.0 s, buildings outer 29.1 s, props 19.6 s, vegetation 118.4 s, traffic static 22.2 s, ground textures 719.1 s, bridge textures cached. Producers overlap, so these do not add up to refresh wall time.

## Sanity checks

The assembler completed its decoded continuous-layer seam and 8 MB budget checks for this tile and built neighbours `t0_1_-1` and `t0_2_0` on both passes. Headless top-down and oblique layer renders are retained at `$TURKU3D_WORK/qa/t0_2_-1/{top,oblique}.png`. Comparison with the local 2025 ortho (`ortho2025-thumb.jpg` in the same QA directory) found the Tuomiokirkkosilta crossing, park tree groups, buildings, roads and parking area in the expected places. No obvious duplicated bridge deck, floating object cluster, water-crossing barrier or building intrusion was seen. This is a geometry review, not a drive test.

## Facade coverage

The first facade reference pass failed because `mapbox_earcut` was missing from the textures environment. Added it to `textures/pyproject.toml` and reran the pass. Reference extraction then completed for all 82 planned walls. The second pass published 82 rough maps: seven from individual reference descriptions, 39 from a sibling wall's description, and 36 from building register fallback. The final tile quality ledger reports 70 fast street facade walls, 67 untextured street walls, 51.36% rough street coverage, and validator status `unvalidated`.

The remaining 34 description calls received HTTP 429 after retries. A subsequent two-call retry still received 429; it did not change the stored tile. The content loop will revisit these when service limits clear. Facade regression reason: description calls rate-limited, fallbacks used.

Failed description IDs (from the stored second pass):

`103277360R_48`, `103277360R_50`, `103277360R_54`, `103277360R_56`, `103277360R_57`, `103277360R_58`, `103277360R_61`, `103277360R_68`, `103277360R_70`, `103277360R_73`, `103277360R_75`, `103277362T_71`, `103277362T_74`, `103277362T_79`, `103277363U_131`, `103277363U_134`, `103277363U_137`, `103371570S_0`, `103371570S_1`, `103371570S_80`, `103371570S_83`, `103431763F_11`, `103431763F_12`, `103431763F_4`, `103431763F_6`, `103431763F_95`, `103431763F_97`, `103440074K_11`, `103440074K_13`, `103440074K_7`, `103440074K_8`, `gis-103440075L_11`, `gis-103440077N_28`, `gis-f6eeee09a46dcaf329227df8be133c39e4b967eb_3`.
