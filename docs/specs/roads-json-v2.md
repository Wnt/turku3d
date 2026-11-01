# `roads.json` v2: lanes, heights, stable ids, junctions, signals, crossings

Status: spec (2026-11-01), phase G3 of the street-richness plan. Writer:
`pipeline/turku3d/roads/` (`build_tile_graph`, called by `coarse/inputs.py::prepare_roads`; heights
added in `coarse/build.py`). Reader: `game/src/gameplay/roadGraph.ts` (`parseRoadGraph`,
`mergeRoadGraphs`, `laneOffset`). Branch `local/g3-roads`.

v2 is **additive**. The file keeps `"format": "turku3d-roadgraph"`, `"version": 1` and every v1 field
with its v1 meaning, so a v1 reader (today's client: `checkFormat` accepts only `version === 1`)
still routes on it. v2 content is announced with `"revision": 2`. A reader uses the v2 fields only
when `revision >= 2`; a file without `revision` is v1. A future breaking change bumps `version`.

## 1. Frame, units, rounding

- Engine frame: `x` east, `z` south, `y` up (N2000 metres), as in [tiles.md](tiles.md).
- Node and vertex x/z are rounded to 0.1 m (unchanged). Heights to 0.001 m. Arc lengths (`s`) to
  0.01 m. Widths to 0.1 m.
- Per tile: a draft tile `t0_i_j` writes the graph of its 250 m bbox. A way crossing the tile edge is
  clipped there; both neighbours produce the same clip point, and the pieces meet in a node
  (flag `SEAM`).
- The file is content-addressed (`roads.<sha256>.json` in the tile manifest `roads.url`). That
  sha256 is the tile's **road-graph hash** for multiplayer version agreement: clients that agree on
  it have byte-identical graphs for the tile.

## 2. v1 fields (unchanged)

```json
{"format": "turku3d-roadgraph", "version": 1, "revision": 2, "frame": "engine-xz",
 "area": "coarse-t0_0_0", "bbox": [xmin, zmin, xmax, zmax],
 "bits": {"foot": 1, "car": 2, "oneway": 4, "class_shift": 3,
          "classes": ["path", "service", "street", "major"], ...v2 tables, see 3.6},
 "nodes": [x0, z0, x1, z1, ...],
 "edges": [[a, b, flags, length_m, x, z, ...interior vertices], ...],
 "edgeHeights": [[y0, y1, ...] | null, ...]}
```

`flags`: FOOT 1, CAR 2, ONEWAY 4 (car travel only a→b), class in bits 3–4. Edge direction a→b is the
OSM way direction (reversed for `oneway=-1`). Interior vertices may be denser than OSM (heights are
sampled at least every 8 m; the line is the same).

## 3. v2 fields

All `edge*` arrays are parallel to `edges` (same index); `nodeFlags` is parallel to the node list.

### 3.1 `edgeHeights` (now on every edge)

v1 had a Y array only on bridge-deck and tunnel edges. v2 has one on **every** edge: one Y per
polyline vertex (node a, interior vertices, node b), so `edgeHeights[i].length == edges[i].length / 2`.
Source by priority: bridge deck surface (OSM `bridge=*` ways matched to the built deck), tunnel
floor, else the tile's walkable ground surface (terrain + kerbs/road top). Interpolate linearly
between vertices.

### 3.2 `edgeIds`: stable, seam-independent ids

`edgeIds[i]` is a 16-hex-digit string. It identifies the **logical edge** the row belongs to, and it
is identical whichever tile produced the row and whatever order a client loads tiles in.

- A logical edge is the stretch of one OSM way between two graph nodes (way ends, or vertices shared
  with another routable way), before any tile clipping.
- `id = sha256("o" + way_id + "|" + qa + "|" + qb + "|" + qm)[:16]`, where `qa`, `qb`, `qm` are
  `"<q(x)>,<q(z)>"` of the logical edge's start, end and arc-length midpoint, `q(v) = floor(v/0.5 + 0.5)`,
  computed on the 0.1 m-rounded vertices.
  (`o` = OSM; a future city-centreline source uses `k` + Keskilinjat `LineId`.)
- **Seam rule.** Every tile reads OSM at least 100 m around itself. A logical edge stays *whole*
  across tile seams only when it lies inside the 90 m-buffered box of every tile it touches; then
  every tile sees the whole edge and computes the same id. Otherwise (long rural edges) the edge is
  cut at each tile seam and each piece is its own logical edge. Both cases are deterministic.
- A whole edge that crosses seams appears as several rows (one per tile, or more if it re-enters a
  tile) with the **same id** and disjoint spans (3.3). `mergeRoadGraphs` re-joins them when both
  pieces are loaded.

### 3.3 `edgeAttrs` and `edgeAttrColumns`

`edgeAttrs[i]` is a fixed-width numeric row; `edgeAttrColumns` names the columns (read by name, new
columns are appended):

| Column | Meaning |
|---|---|
| `way` | OSM way id (0 when unknown) |
| `s0`, `s1` | arc length (m) of this row's node a and node b along its logical edge, `0 <= s0 < s1 <= length` |
| `length` | full logical edge length (m). A row is the whole logical edge iff `s0 == 0 && s1 == length` |
| `lanes_fwd`, `lanes_bwd` | car lanes in a→b and b→a direction. FOOT-only edges: 0, 0. ONEWAY: n, 0 |
| `width` | carriageway width (m), kerb to kerb, parking lanes excluded |
| `kmh` | speed limit (km/h): OSM `maxspeed` (or `zone:maxspeed` / `FI:urban` …) else class default |
| `car_density` | 0–15 ambient-traffic weight (0 = never spawn cars) |
| `foot_density` | 0–15 ambient-walker weight (0 = never spawn walkers) |
| `flags` | per-edge v2 flags, 3.5 |
| `highway` | index into `bits.highways` (OSM `highway` value) |
| `setback0`, `setback1` | distance (m) from the logical start / end where the junction box begins (half the widest other carriageway at that junction + 1 m; 0 when the end is not a car junction). Stop lines and centre-line paint stop there |

Lane rules when OSM has no tag: `lanes=n` on a two-way road splits `ceil(n/2)` forward and
`floor(n/2)` backward unless `lanes:forward` / `lanes:backward` say otherwise; two-way roads
default to 1+1, oneways to 1 (major oneways 2). `lanes=1` on a two-way road gives 1+1 with flag
`SINGLE_TRACK` (both directions share one physical lane). Width: OSM `width` (or `est_width`), else
`lanes_total * lane_width` with lane width 3.5 m (major), 3.25 m (street), 3.0 m (service),
minimum 3.0 m.

Speed defaults (km/h): motorway 100, trunk 80, primary/secondary 50, tertiary/unclassified/
residential 40, service 20, living_street 20, other 10. Speed tier for traffic is the reader's
choice (`speedTier()` in `roadGraph.ts` maps ≤20, 30, 40, 50, 60, ≥70 to 0…5).

Density rules (car / foot), before clamping to 0…15: motorway/trunk 15/0, primary 13/7,
secondary 11/7, tertiary 8/6, unclassified 5/4, residential 4/5, living_street 1/8, service 2/3
(`service=parking_aisle|driveway` 1/2), pedestrian 0/12, footway 0/6 (`footway=sidewalk` 0/8),
steps 0/3, cycleway 0/3, path/track 0/2; car +1 for each lane beyond two. Car density is 0 on
non-CAR edges, foot density 0 on non-FOOT edges.

### 3.4 Lane geometry (right-hand traffic)

For a direction vector `d = (dx, dz)` along a→b, the right-hand normal is `r = (-dz, dx)` (x east,
z south). With `F = lanes_fwd`, `B = lanes_bwd`, lane width `w = width / (F + B)`, the
carriageway spans offsets `[-width/2, +width/2]` along `r`, and the divider between the
directions is at `o_div = -width/2 + B * w`.

- Forward lane `i` (0 = next to the divider, a→b traffic): centre offset `o_div + (i + 0.5) * w`.
- Backward lane `j` (0 = next to the divider, b→a traffic): centre offset `o_div - (j + 0.5) * w`.
- A single-lane oneway therefore drives on the centreline; a 1+1 road drives at `±width/4`.
- `SINGLE_TRACK` (two-way, one physical lane): both directions use `±width/4` as above, which is
  where real cars meet and pass.
- At a polyline vertex the offset follows the bisector of the adjacent segments (mitre limited to
  2×). `laneOffset(edge, lane, s)` in `roadGraph.ts` returns the offset point and Y.

### 3.5 Per-edge `flags` (column `flags`)

| Bit | Name | Meaning |
|---:|---|---|
| 1 | `PAVED` | asphalt/concrete/paving (OSM `surface`, else class default: paved except track/path) |
| 2 | `LIT` | OSM `lit=yes` (or `lit=24/7|automatic|limited`) |
| 4 | `BRIDGE` | OSM `bridge=*` |
| 8 | `TUNNEL` | OSM `tunnel=*` (incl. `building_passage`) |
| 16 | `ROUNDABOUT` | `junction=roundabout|circular` |
| 32 | `LINK` | `*_link` ramp |
| 64 | `KMH_TAGGED` | `kmh` from an OSM tag, not the class default |
| 128 | `LANES_TAGGED` | lane counts from OSM `lanes*` |
| 256 | `WIDTH_TAGGED` | width from OSM `width`/`est_width` |
| 512 | `START_JUNCTION` | the logical start (s = 0) is a car junction (≥3 car edge ends) |
| 1024 | `END_JUNCTION` | the logical end (s = length) is a car junction |
| 2048 | `SHARED_SPACE` | living_street / pedestrian zone (no lane paint, walkers on the carriageway) |
| 4096 | `PSV_ONLY` | bus/taxi only (`busway`, `access=no` + `psv|bus=yes`); no ambient cars |
| 8192 | `NO_MARKINGS` | OSM `lane_markings=no`: the generator paints no centre or lane lines |
| 16384 | `SINGLE_TRACK` | two-way road with `lanes=1` |
| 32768 | `SMOOTH` | asphalt-like surface (`asphalt`, `concrete*`, `paved`, `chipseal`, or untagged car road) that takes line paint |

### 3.6 `nodeFlags`

| Bit | Name | Meaning |
|---:|---|---|
| 1 | `JUNCTION` | ≥3 car edge ends meet here in the logical (unclipped) graph |
| 2 | `SIGNALS` | a traffic-signal node (OSM `highway=traffic_signals`, or a crossing with `crossing=traffic_signals`) sits on this node |
| 4 | `CROSSING` | an OSM pedestrian/cycle crossing node sits on this node |
| 8 | `GIVE_WAY` | OSM `highway=give_way` on this node |
| 16 | `STOP` | OSM `highway=stop` on this node |
| 32 | `SEAM` | clip point on the tile edge; not a real junction (the edge continues in the next tile) |
| 64 | `DEAD_END` | way end with no other edge (turning circle, cul-de-sac, data end) |

Node identity across tiles is its coordinate: nodes within 0.11 m are the same node (as in v1).

`bits` gains the tables `"edge_flags"`, `"node_flags"`, `"event_kinds"`, `"event_bits"` (name → bit
value, as above and in 3.7) and `"highways"` (the list the `highway` column indexes).

### 3.7 `edgeEvents`: signals, crossings, give-way and stop positions

`edgeEvents[i]` is `null` or a flat list of triples `[s, code, ref, s, code, ref, ...]`, sorted by `s`:

- `s`: arc length (m) from this row's node a, `0 <= s <= length of the row`. An event exactly on a
  seam point appears in both rows; a merge keeps it once.
- `code = kind | direction | detail`:
  - kind (low 4 bits): `1` SIGNAL (stop position of a traffic signal), `2` CROSSING (pedestrian or
    cycle crossing point on the carriageway), `3` GIVE_WAY, `4` STOP;
  - direction: `16` FORWARD (applies to a→b traffic), `32` BACKWARD (b→a); both bits = both;
  - detail (crossings): `64` MARKED (zebra paint, i.e. not `crossing:markings=no` /
    `crossing=unmarked`), `128` SIGNALISED (`crossing=traffic_signals`), `256` EXPLICIT (an OSM
    crossing way exists; its own geometry carries the paint), `512` CYCLE (cycle crossing).
- `ref`: index into `controllers` for SIGNAL and SIGNALISED crossings, else `-1`.

Placement: OSM signal nodes in Turku are stop-position nodes with `traffic_signals:direction`
(174/175 in the city centre clip); their direction is taken from that tag relative to the way and
then mapped to the edge's a→b. A signal node without a direction applies toward the nearer car
junction (within 40 m) or both ways. A signal or crossing on a graph node is recorded on every car
edge ending there (s = 0 or s = row length), and in `nodeFlags`. Crossing events appear on the car
edge the crossing cuts across (car edges only).

### 3.8 `controllers`: signal groups

`controllers` is a list of `[id, x, z]`: `id` a 16-hex stable id, `x, z` the group centre. A group
is the single-linkage cluster (35 m) of signal nodes and signalised crossing nodes; `id = sha256("s"
+ sorted member coordinates quantised to 0.5 m)[:16]`. All approaches of one junction share a
controller, so a clock-driven phase `phase(t, controller.id, approach heading)` is identical on every
client. Readers remap `ref` through `id` when merging tiles.

## 4. Client merge (`mergeRoadGraphs`)

- Parts may mix v1 (published playable-tiles graph, older drafts) and v2 (G3 drafts).
- Output is **canonical**: nodes sorted by quantised (x, z), edges by (`id`, `s0`); the same set of
  parts in any load order yields identical node indices, edge indices, ids, spans, lanes and heights.
- An edge carried by two parts (overlapping areas) is kept once (key: `id` + span, or v1 geometry);
  a v2 copy wins over a v1 copy.
- Rows with the same `id` and touching spans (`|s1 - s0'| < 0.2 m`) that meet at a shared node are
  re-joined into one edge (span union, heights and events concatenated).
- v1 rows get a fallback id `"v1:"` + 14 hex digits of a cyrb53 hash of the quantised start, end and
  midpoint (client-only, never compared with generator ids), lanes 1+1 (oneway: 1), width by class,
  no events.

`RoadEdge` exposes `id`, `span` (`[s0, s1, length]`), `lanes` (`{fwd, bwd, width}`), `kmh`,
`carDensity`, `footDensity`, `eflags`, `way`, `highway`, `setbacks`, `events` and `heights`;
`RoadGraph` exposes `nodeFlags` and `controllers`.

## 5. Example (abridged)

```json
{"format":"turku3d-roadgraph","version":1,"revision":2,"frame":"engine-xz","area":"coarse-t0_0_0",
 "bbox":[0,0,250,250],"bits":{"foot":1,"car":2,"oneway":4,"class_shift":3,"classes":["path","service","street","major"],
   "edge_flags":{"PAVED":1,"...":0},"node_flags":{"JUNCTION":1,"...":0},
   "event_kinds":{"SIGNAL":1,"CROSSING":2,"GIVE_WAY":3,"STOP":4},
   "event_bits":{"FORWARD":16,"BACKWARD":32,"MARKED":64,"SIGNALISED":128,"EXPLICIT":256,"CYCLE":512},
   "highways":["motorway","trunk","primary","secondary","tertiary","..."]},
 "nodes":[12.3,0.0,40.1,22.7],
 "edges":[[0,1,19,36.0]],
 "edgeHeights":[[3.912,4.105]],
 "edgeIds":["9f3a0c51d2e47b18"],
 "edgeAttrColumns":["way","s0","s1","length","lanes_fwd","lanes_bwd","width","kmh","car_density","foot_density","flags","highway","setback0","setback1"],
 "edgeAttrs":[[23861523,14.2,50.2,71.9,1,1,6.5,40,8,6,1539,4,4.3,0.0]],
 "edgeEvents":[[3.1,17,0,6.0,114,-1]],
 "nodeFlags":[32,1],
 "controllers":[["4be1f0a29c3d7e65",41.0,25.9]]}
```
