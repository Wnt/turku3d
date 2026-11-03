# lidarwires: overhead wires, hung street lamps and the first migration train

Overhead wires (lighting span wires, utility lines) are detected from the city survey lidar and drawn in the game,
with the street lamps hung on span wires lit like the lamp posts. The same agent owns the first shared migration
train, which brings `wires@2`, lidartrees' `trees-lidar@1` and rulesmith's `furniture@1` to the existing tiles
without a regeneration. Status log: `~/turku3d/loops/lidarwires/STATUS.md`; helper: `~/turku3d/loops/lidarwires/train1.sh`.

## What it is

- **Detection** `pipeline/turku3d/coarse/wires.py`, a city-wide rule with no per-street data:
  - wire returns from `pipeline/turku3d/wire_points.py`, the stage shared with lidarpc's point-cloud wire layer
    (classes 1/12 in free air, line-like, near horizontal, connected stretches);
  - per stretch a plan line and a parabola (catenary at small sag) profile, cut at bends; collinear pieces joined;
  - ends carried to the facade or pole they hang from (overlap-strip returns count as buildings), or to a knot with
    another wire;
  - lamps where span halves meet in mid air, or as a compact blob just below a wire;
  - each tile lists the wires whose middle it owns (search 60 m around it).
- **Migration** `wires@2` (`pipeline/turku3d/migrations/wires.py`) writes `lights.wires` inside the public `lights`
  field: no payload change, no farm public-key change. The chain runs at the end of every generator build (parity).
- **Client** `game/src/render/lights/wires.ts`: all wires in one draw (camera-facing ribbons along the sag, 1 px
  floor with a fade for thinner wires, analytic edge), hung lamps in one instanced draw on the lamp posts'
  material; their lights join the street lights.
- **City pass** `scripts/dev/tile_city_pass.py` (`scripts/dev/tile-migrate pass`):
  - centre-first rings; nice 19, one lazrs thread per worker;
  - the cap follows the one-minute load per core (`--high`/`--low`), and is 0 while the farm disk has under 12 GB free;
  - resumable; it holds back tiles whose catalogued survey blocks are missing;
  - `--supply DIR` hard-links blocks whose size and SHA-256 match the base catalogue; every link goes to `supply.jsonl`;
  - each lane keeps 9 decoded LAZ blocks (byte-identical wires);
  - `--checkpoint wires@2` stores each tile after the wires step, so later trees or furniture changes rerun only those steps.

## Merged to integration

| Branch | Integration | What |
|---|---|---|
| eaa9ff4e | bcef10fd | wires@1 generator + migration |
| 653115dc | 0824cd4d | LAZ streamed, no decoded cache on disk |
| fb062c4a | 3e744bd8 | client wires and hung lamps (boot check ok) |
| 41890b02 | 7b765281 | wires@2: one detector shared with lidarpc (`wire_points.py`) |
| 9113f54f | 8b814edc | overlap-strip walls and roofs count as buildings |
| 0af24cd9 | b66a8ea7 | city pass; CLI migrations read the base's own `data_root` |
| 88a913ce | 2549d32c | pass checkpoints |
| d57932d9 | 71e8e99e | pass waits for catalogued survey blocks; `--supply` |
| 29f32034 | 53c4f59e | lanes keep decoded blocks (byte-identical on the 4 preview tiles) |
| 376cb37b | 96b1b969 | pass holds new tiles under 12 GB free disk |

## Train 1 and the pass

- Alias **`train1`**: revision 6531e905, generation affad6f5, code 376cb37b (integration 96b1b969), registered with
  `--rebuilds-from bulkgen2-3d1445b2+maa2cf1ce`.
  - Chain: chain-record@1, stand-roof@1 (pinned rebuilds), floodlights@1, wires@2, trees-lidar@1 (READY at 61ecbbf4),
    furniture@1 (READY at 5f8a4216).
  - floodlights reruns per tile (cheap): rulesmith's props commits changed its import closure.
- **2 km pass started** 2026-11-03 23:32 UTC on osgallery-dev, unattended: pid 346874, `--radius 8` (285 tiles),
  `--max-jobs 4 --high 4.0 --low 3.0`, `--checkpoint wires@2`, `--supply /bulk/turku3d/lidarpc/raw/pistepilvi`.
  Expected 4-5 h at the farm's load.
- Test tiles checked on `train1` before leaving it running: t0_0_0, t0_1_0, t0_0_-1 and t0_1_-1 are served with the
  full chain (`... wires, trees-lidar, furniture`, not stale), and their `lights.wires` is byte-identical to the
  :5221 preview (`lidarwires-m3`). They were already in this generation from rulesmith's `rulesmith-m4` (same code).
- **Watch** (on osgallery-dev; G = `~/turku3d-local/loops/tiles/farm/migrations/affad6f566872a27a7f32b5dc71a07b5bc94ca1bd9dbf823d960bc1f1fa987fa`):
  - `python3 -m json.tool $G/pass.json`: cap, running, done/left, load, free disk, ETA, CPU per step, tiles waiting
    for survey, blocks supplied;
  - `tail $G/pass.jsonl`: per tile state, wall/CPU, steps;
  - `$G/supply.jsonl`: every linked block;
  - `~/turku3d-local/loops/tiles/farm/logs/migrate-376cb37b42db-pass.log`: errors;
  - from the Mac: `~/turku3d/loops/lidarwires/train1.sh status` or `estimate`.
- **Stop**: `ssh osgallery-dev 'kill -TERM -$(ps -o pgid= -p 346874 | tr -d " ")'`. Running tiles are lost and
  redone on resume; finished ones are kept.
- **Resume or extend**: `~/turku3d/loops/lidarwires/train1.sh ring2km` (same ring) or `train1.sh outward` (the whole
  city, ring by ring). Held-back tiles are picked up as lidarpc's downloads arrive: rerun the same command. Today
  287 of the 289 tiles within 2 km have their blocks (1 block missing); the city has 1,282 blocks, 268 on disk.
- **A step changes later**: register a new alias from integration (`train2`) and run the pass with the same
  `--checkpoint wires@2`. Tiles resume after the wires step if wires@2 is unchanged.
- **Cost** (farm, nice 19, load 2.5-3 per core):
  - wires@2 ~130-150 s CPU per tile cold, less on reused lane blocks;
  - trees-lidar@1 ~30 s per cold block plus ~1 s per tile;
  - furniture@1 ~0 without benches, median ~60 s with;
  - floodlights ~3 s.
  - Disk: furniture's TileStore and decoded caches grow `farm/work/migrate/cache` by about 1.4 GB per new block
    area. The pass pauses below 12 GB free; that cache is evictable.

## Verify and switch the main pin (coordinator only, after district spot checks and Jonni's look)

1. Check the pass is done for the target area: `pass.json` `left` 0 for the radius, no `failed` rows in `pass.jsonl`.
2. Spot checks per district, on the alias:
   - `curl -s http://192.168.1.114:7950/v/train1/coarse/<tile>/manifest.json`: `migrations` ends with
     `wires`, `trees-lidar`, `furniture`; `lights.wires` is present on surveyed tiles; `stale` is false;
   - `python3 scripts/dev/shared-tile-audit.py` through your own proxy;
   - a look on a preview game pinned to `train1` (as :5221 is to `lidarwires-m3`).
3. Switch atomically under the lock (macOS has no `flock` binary):
   ```sh
   python3 - <<'PY'
   import fcntl, json, os, pathlib
   d = pathlib.Path.home() / 'turku3d/loops/integrator'
   with open(d / 'repin.lock', 'w') as lock:
       fcntl.flock(lock, fcntl.LOCK_EX)
       p = d / 'tile-service.json'; s = json.loads(p.read_text())
       s['version'] = 'train1'
       t = p.with_suffix('.json.tmp'); t.write_text(json.dumps(s, indent=1) + '\n'); os.replace(t, p)
   PY
   ```
   The proxy hot-reads the pin. Jonni presses F5 on :5220. Unchanged payloads keep their URLs, so only changed tiles
   reload.
4. Publish to the share: `~/turku3d/wt/loop-integration/repo/tools/share/deploy.sh` (it re-pins city.lab's tile
   proxy to the local pin; `https://city.lab.madekivi.fi/version.json` must name the commit).

Tiles outside the finished radius are served as their base and migrate on request, which is slow for surveyed
tiles. Either wait for `train1.sh outward`, or switch with on-demand fallback accepted.

## The :5221 preview

The coordinator's own: the integration game with its own tile proxy (:5236) pinned to `lidarwires-m3`. Its four centre
test tiles are migrated (wires 16/14/4/23, lamps 0/3/0/4; trees-lidar before lidartrees' ellipse; no furniture).
Look check: `?cam=23459781,6704305,7.8,325,8,60` (Aurakatu looking NW), add `&time=22:00` for the hung lamps.

## Open items, ranked

1. **Recall through crowns**: the shared stage takes classes 1/12 in free air only, so wires through tree crowns or
   filed as vegetation are missed. The centre 2x2 gives 57 wires, 739 m and 7 lamps; the first detector found 94,
   1,570 m and 22, with some roof edges among them. Next: bridge collinear pieces through crowded space in
   `wire_points.py`, with lidarpc; bump `wires@`.
2. **Shared decode**: wires, trees-lidar and furniture each read the same LAZ blocks their own way. Worst is
   furniture's TileStore, which writes ~300 MB decoded shards per block to disk. One per-block derived cache, or the
   pass lane's in-memory blocks for all three, would cut CPU and disk. Do it before the city-wide pass.
3. The rest of the city needs lidarpc's downloads (catalogue-verified via `--supply`); rerun `train1.sh outward`.
4. No real-GPU frames yet: the wire draw (`overhead-wires`) and the lamps (`hung-street-lamps`) are estimated at under
   0.1 ms; check with winperf/airperf at the Aurakatu pose.
