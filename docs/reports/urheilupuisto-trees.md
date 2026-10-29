# Urheilupuisto vegetation recovery

The park-wide loss came from `a149acd7`'s open-surface classification, rather than
pond classification or a failed object pass. Urheilupuisto has both
`leisure=park` and `landuse=recreation_ground`: the latter turned its entire
186,163 m² polygon into a buffered exclusion, covering the separately mapped forests.
The reported tile retained trees outside that park footprint.

Vegetated area tags now take precedence over broad recreation land use on the
same feature. Explicit pitches, tracks, stadiums and playgrounds remain excluded.
Foot/cycle clearance uses mapped path footprints (including `width` and pedestrian
areas), with the same defaults used for ground surfaces. The visible flared trunk
base clears the footprint; upper crowns may overhang. Elevated paths and tunnels
do not clear ground trees. Car-road and surveyed street-area exclusions remain.
Path preparation has its own content-addressed dataset; structural input identities
and terrain caches do not depend on it. First pass still omits decorative vegetation.

## Source comparison

Measured against pre-fix `f9b76480` using the same shared raw sources and source-only
vegetation production, without rebuilding tile meshes. These are local source
counts; the selective deployed probe compares actual full manifests with source.
Ground key-point heights, buildings, surveyed streets, water and mapped tree points
are retained in both runs. The path-free column removes only OSM graph path rules
from the old producer, isolating the original loss from the clearance suspicion.

Lidar columns come directly from `raw/laz/pistepilvi/*.laz`, classifications 3/4/5,
clipped to each 250 m tile. Covered area counts occupied 1 m cells; neither cells nor
returns are individual tree counts. Raw imagery/point clouds are not published.

| Tile | Before trees | Old, without graph paths | After trees | Vegetation returns | Covered m² |
| --- | ---: | ---: | ---: | ---: | ---: |
| `t0_-2_2` | 51 | 79 | 110 | 997,219 | 34,264 |
| `t0_-1_2` | 32 | 35 | 408 | 1,706,486 | 29,979 |
| `t0_0_2` | 293 | 340 | 434 | 1,724,682 | 30,848 |
| `t0_1_2` | 146 | 188 | 110 | 1,168,792 | 19,805 |
| `t0_-2_3` | 103 | 128 | 183 | 1,306,641 | 24,186 |
| `t0_-1_3` | 9 | 13 | 488 | 1,347,585 | 28,333 |
| `t0_0_3` | 23 | 32 | 421 | 1,741,858 | 28,309 |
| `t0_-1_4` | 74 | 120 | 400 | 1,381,067 | 27,836 |
| `t0_0_4` | 53 | 74 | 116 | 1,191,798 | 22,771 |

On the reported tile, the open exclusion shrinks from 26,227 to 1,887 m² while
water remains 5,655 m². Other severely affected park tiles recover hundreds of trees.
The eastern control `t0_1_2` has no recreation-park overlap; its lower after count
reflects actual wider pedestrian footprints instead of the previous fixed 2 m graph
buffer. It is not a blanket density increase. Optional lidar canopy remains optional;
these drafts use mapped area density when their ground source has no vegetation returns.

## Verification

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m pytest -q \
  pipeline/tests/test_path_tree_clearance.py pipeline/tests/test_coarse.py \
  pipeline/tests/test_coarse_sources.py pipeline/tests/test_coarse_latency.py \
  -k 'forest or recreation_park or path or structural or prepared_keys or draft_has_osm_trees'
python3 scripts/dev/probe_park_trees.py --commit "$(git rev-parse HEAD)"
```

The deployed probe selects the reported pond tile and the two other strongest park
losses: `t0_-1_2`, `t0_-1_3`, `t0_0_3`. It builds first/full with 45/100-second
per-pass budgets, checks building/collision counts against inputs, compares full tree
counts with source production, checks every path/trunk, open-surface and water
exclusion, and requires one shipped trunk collider per tree. The run has the
standard hard ten-minute total budget. Visual inspection is Jonni's in-game check.
