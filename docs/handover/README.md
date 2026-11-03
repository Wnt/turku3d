# Handover, 2026-11-04 (coordinator)

The agent session that started on 2026-11-03 is wrapped up. Every agent wrote and merged its own handover in this
folder. This page is the index and the cross-cutting state.

## Where things stand

| Goal | Mac M1 Max (medium) | Windows 560X (reduced, full screen) | M1 Air (reduced, live) |
|---|---|---|---|
| FPS ≥ 40 | ~50–51 in the drive replay (`20261103-144904-0rb85a`) | ~41 | 51–55 cool, ~50 hot |
| Slow frames (> 50 ms per ~96 s drive) | 13–26 | ~150 | 52–72 |
| HD coverage (mean / 3×3) | ~95 / 99.3 % | 61 / 78 % (before stream2's 27b7bf15 budget fix; re-measure) | 69–75 / 87–92 % |

The FPS goal is met on all three machines. The open work is slow frames on Windows (main-thread bound, see
perf3, winperf) and coverage on the reduced tiers (GPU and memory budgets, see stream2, airperf).

## Index

| Note | Area |
|---|---|
| [perf3](perf3.md) | render CPU: frozen matrices, reflection scope, shadow mesh, uniform/geometry pools (hang fixed), warm-ups |
| [stream2](stream2.md) | streaming budgets, GPU/memory residency per tier, memory census, releases |
| [lodblend](lodblend.md) | LOD bands, dissolves, tree cross-fades, settle rule |
| [npc](npc.md) | car tiers, incremental traffic graph, walker allocation |
| [physstall](physstall.md) | budgeted collision builds, render-pose extrapolation (rotation fix) |
| [taa](taa.md) | TRAA velocity fix, idle-slope fix |
| [winperf](winperf.md) | Windows measurements, tiering (probe + cores), governor |
| [airperf](airperf.md) | Air measurements, heap findings |
| [benchlab](benchlab.md) | bench modes and A/B method (warm-up, interleave, frozen port, fresh tab) |
| [water](water.md) | river W1–W8 (SSR on reduced, sun glint, bank fade, night, rain) |
| [floodlights2](floodlights2.md) | lit venues: field wash, spill, dome, beams |
| [techstudy](techstudy.md) | GTA V / Bodycam studies on the Windows host |
| [editor](editor.md) | in-game object editor (DualSense), edit sessions |
| [rulesmith](rulesmith.md) | edit sessions → generator rules (`furniture@1`) |
| [lidarpc](lidarpc.md) | LiDAR COPC product, overlay, wire layer |
| [lidartrees](lidartrees.md) | LiDAR-conformed tree crowns (`trees-lidar@1`) |
| [lidarwires](lidarwires.md) | overhead wires (`wires@2`), the `train1` migration and city pass |
| [cityshare](cityshare.md) | public test site https://city.lab.madekivi.fi on osgallery-dev |
| [storage](storage.md) | bulk HDD pool, moved data, restore commands |

## Running unattended (osgallery-dev)

- **`train1` city pass** (wires + LiDAR trees + editor furniture rules), 2 km ring first, ~4–5 h; status and stop in
  [lidarwires](lidarwires.md). The main pin is **not** switched: after the pass, spot-check several districts, let
  Jonni look (preview on the Mac at :5221 serves `lidarwires-m3`), then switch `tile-service.json` under
  `repin.lock` and run `tools/share/deploy.sh`.
- **LiDAR city conversion and wire layer** (~14 h left), see [lidarpc](lidarpc.md); stop the wire job when the
  conversion log says `done`.
- **Services as user units** (survive reboots): `shared-tiles` (7950), `local-horizon` (7947), `lidar-copc` (7960),
  `city-web`/`city-tiles`/`city-mp`/`city-forwarder`. Still hand-started: the loop tile servers on 5225/5775/5776.

## Decisions waiting for Jonni

1. **Urgent: the 12:23 ZFS snapshot** `data/subvol-950-disk-0@bulkgen2-2e6515f4-20261103` now holds ~141 GB;
   osgallery-dev has ~28 GB free and the city pass writes to that disk (its guard holds new tiles below 12 GB free).
   The protected 2e6515f4 products have a verified read-only copy at `bulk/turku3d-protected`. See [storage](storage.md).
2. Whether LiDAR absence should delete register benches ([rulesmith](rulesmith.md)).
3. Look decisions: LOD fade length (0.6 s default), crown `stretch` vs `spread` on medium (+1.2 ms beauty on the 560X),
   cheaper leaves (`?leaves=`), SSR fallback look on reduced (re-shoot pending).
4. Storage: `private/gmaps-data` local copy, other Mac candidates, `copies=2` on the single-HDD `bulk` pool.

## Known issues not yet assigned

- **Kristiinankatu 1 has no windows** (and other bulk-generated buildings may share it): yesterday's frozen build
  6098c5bd shows the same, so it is in the bulk tile generation, not today's client changes. Fix via a facade
  migration with generator parity.
- The pizzeria mission point sits 1.24 m from a lamp post (`game/src/gameplay/content/locations.ts`).
- Junction gridlock at the centre seam ([npc](npc.md)).

## Measurement method that held up

Frozen build on its own Vite port; a fresh tab per run (back-forward cache keeps the old page's GPU device and
memory); one discarded warm-up run; interleaved variants; three runs per comparison. Single runs swing ±10 %.
