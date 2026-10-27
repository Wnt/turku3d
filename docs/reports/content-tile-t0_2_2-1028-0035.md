# Playable tile t0_2_2

Tile `t0_2_2` covers E 23460000–23460250, N 6703750–6704000. `make tile-add T=t0_2_2` built and stored it, and `python -m turku3d.region list-tile t0_2_2` added it to `$TURKU3D_DATA/playable-tiles.json`. Its `work-3x3` inputs were complete (6,028,610,119 registered bytes); no raw data was fetched. The target touches the existing playable list at a corner, so the region planner now permits corner adjacency. The road graph keeps the largest routable network in each edge-connected group of listed tiles; the target has 132 graph nodes and 153 edges instead of being dropped by the former global-largest filter.

## Content and budget

The assembled L0 visual has 216,670 triangles and includes ground, 43 building shells, structures, 118 props, 145 vegetation records, and 40 parked-car records. The roads producer clipped 107 ways in the tile. The visual GLB is 5,431,112 bytes (4,119,048 Brotli bytes); shipped ground maps add 3,306,957 bytes. **Wire total: 7,426,005 bytes**, below the 8,000,000-byte cap. The six visual layers are `ground`, `buildings`, `structures`, `props`, `vegetation`, and `traffic_static`; roads ship as a graph asset.

The first assembly failed because existing tile `t0_0_1` reached 8,066,374 wire bytes after its facades had reached the minimum package density. The packager now resamples the shipped ortho from the preserved 4 cm source to a 5,600-pixel tile core (about 4.46 cm); this is still finer than the 2022/2025 source imagery. The successful build measures `t0_0_1` at 7,581,207 bytes. A separate tile-store fix includes road and address asset hashes in per-tile keys, so a road-only repair publishes a new entry rather than retaining the old graph.

## Wall times

The first build's refresh took 1,748.4 s. Its measured producer times were ground 770.331 s, buildings 837.237 s, structures 136.210 s, bridges 15.868 s, roads 1.362 s, props 28.491 s, vegetation 70.301 s, parked cars 26.498 s, and ground textures 773.727 s. Producers overlap, so these do not add to the refresh time. The first fast facade pass took 357.3 s (119 walls; 97 successful description calls), after which assembly hit the existing tile's wire cap.

| Successful retry stage | Wall time |
| --- | ---: |
| Inputs | 0.185 s |
| Massing ring | 6.717 s |
| Refresh (cached layers) | 30.049 s |
| Fast facades | 55.847 s |
| Assembly, including shipped ortho variants | 625.971 s |
| Tile-store put | 1.623 s |
| **Total** | **720.398 s** |

The road-only repair then took 68.163 s: refresh 9.690 s, cached facades 11.142 s, assembly 45.127 s, and tile-store put 0.255 s. Its new entry contains the corrected road graph.

## Checks and coverage

- `node tools/validator/src/cli.ts --checks format,seams` on `collision/t0_2_2`, `collision/t0_2_1`, and `collision/t0_1_2`: **format PASS, seams PASS**, 96 shared edges. Report: `$TURKU3D_WORK/reports/tile-add/t0_2_2-seams.json`. The latter two directories hold the built ground collision ring next to the target.
- The road graph has 9 west, 9 east, 7 north, and 7 south boundary nodes for the target. No *listed* tile shares an edge with it, so a two-sided published road seam cannot yet be measured; the existing `t0_1_1` touches only its corner.
- `kanta_Sahkoverkonosa.parquet` remains a restricted optional prop source. The structures producer omitted two meshes, vegetation omitted six candidates, and seven parked-car candidates failed ground placement. Required ground, buildings, and roads were present.
- The tile build bound 119 fast facade maps. `list-tile` published none to the data root's facade approvals because all 119 walls were absent from the approved inventory. The per-tile store entry has the maps; a later data-lane rebuild needs the shared inventory refreshed to retain them.

No screenshots or drive test were run; Jonni will test the tile in the game.
