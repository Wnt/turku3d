# W1-03 buildings layer: measured acceptance report

Date: 2026-10-25. Result: **BLOCKED**. No acceptance threshold was relaxed.

## Implemented

CityGML cache ingestion and native roofer LoD2.2; earcut polygons and holes; footprint snapping; closed/manifold shell validation; robust post-snap class-6 residuals; collision tiles with canonical shared cuts; facade UVs and metadata; ortho roof colour classes; OSM semantics; manifests and a Rapier layer validator.

## Inventory and artifacts

The inventory conservatively includes 71 kantakartta building footprints (66000) and 10 building-part footprints (66001). All 81 intersect the four visual tiles plus 50 m margin. Parts remain explicit rather than disappearing when reconstruction fails. Complete footprints extend beyond the margin.

- Shells exported: **62/81**. Residual acceptance: **57/81**.
- 78 collision NPZ tiles, 10 visual layer GLBs, 5817 planar wall polygons (600 street-facing).
- All 119 neighbouring collision-tile boundary comparisons are bitwise identical.
- All 6888 visual surface meshes have finite positions, normals and UVs and material metadata.
- Maximum selected shell: 1,444 triangles before grid clipping. All exported shells passed closure, edge/vertex manifold, winding, positive-volume and ≤0.3 m footprint checks.
- Every selected model is roofer. Only one city candidate passed geometry validation; the per-candidate measurements and rejection reasons are preserved.

Outputs: `$TURKU3D_WORK/build/layers/buildings/`. [Detailed metrics](buildings-metrics.json), [artifact checks](buildings-checks.json), [facade contract](../specs/facades.md). The manifest records the source-content hash as well as the base Git revision; the build was run from the working tree before its commit.

## Acceptance blockers

12 footprints have candidates rejected by the closed-manifold checks. Seven have no usable roofer LoD2.2: roofer reports success but emits only LoD0 with `rf_pointcloud_unusable=true`. They include six small building parts and building `103969493L`. This is not reported as successful shell coverage.

Missing shells:

- `103277348B`
- `103277387L`
- `103304661D`
- `103304671R`
- `103304673T`
- `103304676W`
- `103304678Y`
- `1033046801`
- `1033046812`
- `103447030Y`
- `1034470343`
- `103762753K`
- `103969493L`
- `kanta-0ae1f2a9771e780d415e9d72284ea849065cb86a`
- `kanta-152298a5d487e0c2ec3636a20d201a703d60196a`
- `kanta-1b56ec2b707d69d7d171b5b32f1d9d3e93c55179`
- `kanta-52df0535c3b740b4a292d1e55730a21fbe2f568a`
- `kanta-84488cf9ec6912ef917b364173f7ec4e5f91ad27`
- `kanta-c03581d0e7a8357e46ce3c8b92ac4cb38d9c629f`

Five exported candidates miss the fixed 0.10 m median / 0.25 m p75 limits:

| Footprint | Median absolute residual m | p75 m |
|---|---:|---:|
| `103277355K` | 0.1100 | 0.3064 |
| `1032959060` | 0.0489 | 1.1049 |
| `kanta-97e1d0fd0320a859d69b69fbb8293fcfc83fbfdd` | 0.0854 | 2.2201 |
| `kanta-ace494b85c84b8d3f10bdf05e3026e9ed096286c` | 1.8966 | 6.0241 |
| `kanta-f4a8db243ddae25e0cfde199c3707460892a1cf4` | 0.0817 | 3.6523 |

No guessed heights or trimmed lidar outliers were used to manufacture a passing result. Building-part association and source-epoch differences need investigation alongside the remaining topology defects.

## Verification

Run from this worktree:

```sh
source ../sandbox.env
make -j2 buildings-test
bash pipeline/env/run.sh python -m turku3d.buildings \
  --roofer-output "$TURKU3D_WORK/cache/buildings/roofer"
make buildings-validate
```

The initial reconstruction was actually executed with native roofer 1.0.0, four jobs, `--lod22 --no-clip-terrain --srs EPSG:3877 --id-attribute building_id`, the nine-LAZ input directory and the sandbox footprint GPKG. It took 211.3 s and emitted 81 records, of which 74 contained LoD2.2. `make buildings-build` runs the complete reconstruction workflow.

## Gate and command output

`make -j2 buildings-test`: **PASS**, 18 Python tests, TypeScript typecheck, and one Rapier positive/negative regression test; **5.63 s wall time**. Python emitted 17 Shapely/Affine deprecation warnings; none were suppressed. No Wave 0 `scripts/dev/gate` exists in this checkout.

The real build exits 1 (74.12 s, reusing the reconstruction):

```json
{"pass": false, "shells": 62, "buildings": 81, "elapsed_s": 74.12}
```

The real walker check exits 1 (111.632 s):

```json
{"pass": false, "walkers": 2000, "entered": 0, "endedInside": 0, "untestedBuildings": ["kanta-f4a8db243ddae25e0cfde199c3707460892a1cf4"]}
```

All 2,000 ten-second trials had zero entries and zero end positions inside a tested footprint. The check still fails because 19 shells are missing and one nested building-part footprint has no accepted outside start. The horizontal support pad isolates wall blocking; this is not an integrated terrain or vehicle acceptance claim.

## Required follow-ups

1. Repair or replace the 12 rejected roof/shell topologies without relaxing manifold checks; regenerate and measure the candidates.
2. Resolve the seven unusable point-cloud cases, including authoritative building-part association and the source epoch for `103969493L`.
3. Resolve the five residual failures and the nested part without a valid walker start. Rerun both real-data commands until they pass.
4. Rebase onto Wave 0 tooling/quality-gate changes when available, then run the applicable gate. W1-04 must use complete object bounds for cross-tile visual ownership and bind the material keys; integrated ground/vehicle validation follows assembly.
