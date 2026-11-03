# Handover: lidartrees (tree crowns from the LiDAR)

The game's trees take their crown volume and shape from the city LiDAR. They are drawn with the existing species
models (leaf cards, wind, LODs, impostors), not points. Working notes, probes and tools are in
`~/turku3d/loops/lidartrees/` (`STATUS.md`, `tools/`). The train readiness note is
`~/turku3d/loops/lidarwires/notes-lidartrees.md`.

## Merged (local/loop-integration)
| Branch commit | Integration | What |
|---|---|---|
| ef01673f | 1b0c2ea8 | Crown fill: each species model reshaped once at load to its genus's LiDAR crown profile (`game/src/world/nature/crowns.ts`) |
| 94c10b8c | ad9f8d51 | Per-tree crowns: `trees-lidar@1` migration (`pipeline/turku3d/migrations/trees_lidar.py`, `vegetation/crowns.py`); the game draws them (`vegetationLod.ts`) |
| fe867e8f | ef8e8219 | Canopy streamed from the LAZ blocks, with no decoded-LAZ cache on the farm disk |
| 879245c9 | d8267bb2 | Oval crown footprints: an area-preserving ellipse per tree (`shapeTree`) |
| 36f66206 | 61ecbbf4 | Per-block class-5 cell cache (~1.6 MB per LAZ block): each block is read once city-wide |

### Why
A probe over 5 tiles (723 trees, watershed crowns on a 0.5 m canopy height model) found two things.
- Tile heights and crown radii already matched the LiDAR, within about 0.2 m median.
- The models' leaf cards reached only 0.63-0.84 of the measured crown radius, where the LiDAR crown reaches about
  1.0. That is about half the crown volume, which is what Jonni saw with `?pointcloud=1`.

Two smaller findings:
- OSM-area scatter trees had random heights, 5-15 m off.
- Class 12 (the overlap flight line) adds only 1.1-1.4 % to the class-5 crown surface, so the measurement uses class 5
  alone, as the generator does.

## Crown fill and the `?crowns=` A/B (game, F5)
- Each tree model is widened per fifth of its crown height, 1.14-1.47x on average.
- One fit reshapes every LOD, the shadow proxies and the impostors alike. lodblend's fades and its alpha-coverage match
  are therefore unchanged.
- Modes (`crownMode` in `crowns.ts`):
  - `stretch`, the default on medium/high: leaf cards widen with the crown, about +30 % leaf fragments;
  - `spread`, the default on reduced/low/mobile: each card moves out at its own size, the same leaf fragments as before;
  - `asset`: the old models.
- Registered A/B: `?crowns=stretch|spread|asset`, owner lidartrees, removal within ten merges.
- **Air result (airperf): +0.1 ms on medium.** winperf's Windows (Radeon 560X) result may follow in its notes.

## The migration `trees-lidar@1` and its cost
- **Order:** in the chain after `wires@2` and before rulesmith's `furniture@1`. The generator runs the chain at the end
  of every build, so a regeneration equals base + migration.
- **Parity (t0_1_1):** the tree layer is byte-identical between the generator build and the migrated product. Other
  visual geometry differs from generator changes made since 3d1445b2, before this step.
- **Measurement:** class-5 canopy cells (highest return per 0.5 m) go to the tree nearest relative to its radius, within
  max(1.5 r, r + 2 m). The crown is the connected part around the trunk. Supported crowns (12 cells, 0.35 m relief) give:
  - height (q98) and crown radius (q90 from the trunk);
  - a lean: a shear of at most 0.08 per metre of height, beyond a dead zone;
  - an oval footprint: axis ratio under 0.85, never below 0.75.

  Bushes and unsupported trees keep their size.
- **Output:** `instance_crowns` `[h, r, shear x, shear z, stretch, angle]` in each vegetation node's extras.
  - Only the visual GLB's JSON is written; every other payload is a hard link.
  - No new manifest key and no farm upgrade.
  - Trunk collision keeps the generator's placement.
  - A tile with a LAZ block missing from the base's data root stays unchanged.
- **Cost (osgallery-dev, load 45-60, nice 19):**
  - about 30 s of CPU per LAZ block, once city-wide;
  - 0.4-1.6 s per tile once its blocks are cached;
  - about 12 CPU-hours and ~2 GB of cache (`TURKU3D_SHARED_CACHE/raw-derived/crown-canopy`) for the city.
- **Test tiles (`lidartrees-m3`, integration 61ecbbf4):**

  | Tile | Trees with a LiDAR crown | Height error p90 vs the LiDAR top |
  |---|---|---|
  | t0_0_0 | 71/73 | 4.1 -> 3.4 m |
  | t0_1_0 | 151/159 | 9.4 -> 2.1 m |
  | t0_0_-1 | 26/40 | |
  | t0_1_-1 | 43/43 | |
  | t0_-3_2 | 73/75 | 1.25 -> 0.94 m |

  `shared-tile-audit.py` passes, and output is deterministic across two farm runs on different code paths.
- **City pass:** owned by lidarwires (train). Only 264 of 4,638 base tiles have every LAZ block in the farm data today,
  so coverage follows lidarpc's LAZ downloads.

## Look check (:5221 preview, or :5220 after the pin switch; F5)
Compare `&crowns=asset` (old models) and `&pointcloud=1` (survey).
- River park (Jonni's recording pose): `?cam=23459907.05,6704298.34,5.65,45,4,60`.
- River park from above: `?cam=23459820,6704230,32,45,-22,60`.
- Avenue (register maples, t0_0_0): `?cam=23459514,6704413,6.7,55,3,60`.
- Expected:
  - crowns 1.2-1.5x wider and fuller;
  - park trees at LiDAR heights;
  - slight leans (at most 4.6 degrees) and oval crowns where the survey's crowns are oval.

## Open items (ranked)
1. **Missing trees with new colliders.** Canopy above 4 m that no tree claims is 3-20 % per tile (t0_0_0 20 %, mostly
   beside buildings). Adding trees needs new instances and trunk cylinders, an edit of the collision payloads, as a new
   migration step with generator parity.
2. **Branch skeletons per tree.** Today the branches follow the crown only through the species fit and the per-tree
   shape (size, lean, ellipse). Space-colonisation or fitted skeletons per tree are not done.
3. **Remove the losing `?crowns=` path** once winperf reports. The Air shows +0.1 ms on medium, which favours keeping
   `stretch` on medium/high.
4. **The two willow deletes from Jonni's editor session.** Two `lidar:` trees drawn as willows were deleted in the
   editor; rulesmith handed them to lidartrees (`~/turku3d/loops/rulesmith/STATUS.md`, "Not done"). Fix the rule: why
   those canopy detections became trees, and why they were drawn as willows (`natureAsset` near water). Do not fix the
   two trees by hand. Check the same pattern in 2-3 other riverside places.

## Background jobs
None left running. My farm test aliases are kept for reference and none is pinned:
- `lidartrees-m1`/`m2`/`m3`;
- `lidartrees-gen`/`gen2`/`gen3`;
- `lidartrees+par3`.

The headless harness (`~/turku3d/loops/lidartrees/harness/`) is stopped. It cannot show crowns: the SwiftShader QA page
draws only the tiles' placeholder tree posts.
