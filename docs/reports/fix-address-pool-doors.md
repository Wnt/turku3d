# Delivery address pool: door and wall selection fix

Date: 2026-10-27. Code: `pipeline/turku3d/content/addresses.py` (rules in `pipeline/turku3d/content/README.md`).

## Problem

Linnankatu 13a (building `103304672S`) was missing from the pool. The register point for 13a is nearest to
the 3.2 m wall that `103304672S` shares with its neighbour `103304667L` (Linnankatu 13b). The selector picked that
wall because the walkable network lies ahead of it, behind the neighbour. All three sidewalk spots (2.5, 4 and
1.5 m out) of that wall fall inside `103304667L`, so the address was dropped. The real Linnankatu wall was
rejected: OSM traces the sidewalk along the facade line, about 1.3 m inside the footprint, and the old
"ahead" test (the cosine from the wall's midpoint to the nearest network point had to exceed 0.3) failed for it.

## Rule change

- **Street in front, by signed distance.** A wall is street-side when the walkable network crosses the strip
  the wall sweeps along its outward normal, at a signed distance from 2 m behind the wall's line
  (`ALONG_WALL_M`) to 12 m in front of it (`FRONT_STREET_M`). A sidewalk drawn along the wall counts as in front.
  Walls the layer flags `street_facing` still count when the network is elsewhere.
- **No door on a wall without a clear spot.** Doors are tried best first. A door whose sidewalk spots all lie in
  a footprint (any footprint, its own or a neighbour's) is skipped for the next candidate instead of dropping the
  address. The drop-off stays outside every footprint.
- **The address's street first.** The address without its house number is matched against named OSM highway
  centre lines from the road graph's OSM snapshot (`roads/MANIFEST.json` `inputs.osm.path`). Walls with that
  centre line within 25 m in front of them (`NAMED_STREET_M`), and with the walkable network in front of them,
  are tried before the other street-side walls. Inside each group the old order applies: OSM entrance, then the
  wall nearest to the register point, then the widest wall.
- The area, street-distance and route checks are unchanged: a drop-off outside the area's L0 tiles, more than
  40 m from the walkable network, or within 60 m of the pizzeria still drops the address.

## Before and after (four core tiles)

Inputs: the integration build's layers (`~/turku3d/wt/integration/work/build/layers`, buildings + roads) and the
city register address points. Before is the published pool `addresses/c36726df6272cfba.json`; the old code
reproduces it byte for byte from these inputs. After is the same address step with this change.

| | Before | After |
|---|---|---|
| Addresses | 78 | 81 |
| t0_0_0 / t0_0_1 / t0_1_0 / t0_1_1 | 31 / 14 / 16 / 17 | 35 / 13 / 16 / 17 |
| Unchanged drop-off | | 65 |
| Gained | | 4 |
| Lost | | 1 |
| Moved by more than 3 m | | 12 (none moved by 0–3 m) |

"Street" below is the distance from the drop-off to the nearest OSM centre line carrying the address's street name.

### Gained

| Address | Building | Drop-off (x, z) | Door | Street | Foot m | Note |
|---|---|---|---|---|---|---|
| Linnankatu 13a | 103304672S | 183.24, 116.26 | wall | 6.9 m | 129.0 | the case above |
| Linnankatu 12 | 103304672S | 186.70, 113.90 | wall | 6.9 m | 133.2 | register point 19.8 m north, across Linnankatu (see follow-up) |
| Eerikinkatu 17 | 103304677X | 14.77, 65.25 | wall | 7.6 m | 257.1 | register point inside the footprint |
| Kristiinankatu 6a | 1033046480 | 1.37, 152.70 | entrance | 5.9 m | 112.0 | the building's own address; same entrance as Kristiinankatu 6 |

### Lost

| Address | Building | Before (x, z) | Reason |
|---|---|---|---|
| Volter Kilven katu 1 | 102725984D | 25.61, 497.93 (north wall entrance) | The wall facing Volter Kilven katu now comes first. Its drop-off (27.77, 500.25) lies 0.25 m into tile t0_0_2, outside the four core tiles. The address comes back when t0_0_2 is published. |

### Moved by more than 3 m

| Address | Moved | Before (x, z), door, street | After (x, z), door, street | Foot m |
|---|---|---|---|---|
| Aurakatu 3 | 4.6 m | 211.97, 70.94, wall, 13.4 m | 210.20, 75.18, wall, 9.5 m | 190.0 → 187.5 |
| Eerikinkatu 16 | 10.1 m | 66.58, 57.61, entrance, 15.0 m | 63.11, 48.15, wall, 5.2 m | 179.1 → 189.1 |
| Eerikinkatu 18 | 3.7 m | 30.65, 78.30, wall, 12.1 m | 26.99, 77.80, wall, 9.6 m | 204.1 → 213.8 |
| Kristiinankatu 2b | 4.8 m | 61.22, 245.77, wall, 9.3 m | 63.17, 241.40, wall, 5.2 m | 84.0 → 78.0 |
| Läntinen Rantakatu 1 | 4.3 m | 455.31, 11.58, wall, 6.2 m | 457.60, 15.19, wall, 2.8 m | 516.8 → 514.3 |
| Läntinen Rantakatu 13 | 4.3 m | 188.21, 241.56, wall, 8.4 m | 192.21, 243.04, wall, 5.2 m | 212.5 → 218.0 |
| Läntinen Rantakatu 15 | 5.0 m | 185.87, 242.72, wall, 8.4 m | 184.30, 247.46, wall, 4.9 m | 212.1 → 209.0 |
| Läntinen Rantakatu 19 | 6.5 m | 71.92, 300.09, wall, 10.4 m | 77.34, 303.65, wall, 4.7 m | 171.8 → 162.6 |
| Läntinen Rantakatu 19a | 33.5 m | 68.71, 269.85, entrance on Kristiinankatu, 16.7 m from Kristiinankatu | 81.60, 300.77, wall, 5.2 m | 106.0 → 157.5 |
| Läntinen Rantakatu 21 | 5.3 m | 20.77, 328.42, wall, 9.8 m | 25.54, 330.84, wall, 5.4 m | 224.3 → 221.1 |
| Linnankatu 13b | 14.7 m | 147.70, 131.51, wall, 14.2 m | 162.35, 130.41, wall, 6.8 m | 95.6 → 103.8 |
| Linnankatu 14 | 6.5 m | 158.17, 155.02, wall, 11.2 m | 152.35, 152.03, wall, 5.4 m | 103.8 → 95.1 |

Every moved drop-off is now closer to its own street's centre line. Every address stays on the same building.

## Linnankatu 13a

Drop-off **(183.24, 116.26)**, door `wall`, on the Linnankatu wall of `103304672S` at the point nearest the
register point (179.10, 110.19). It is 2.5 m out from the facade, outside every footprint, and 6.9 m from the
Linnankatu centre line. Its street snap point is (181.12, 113.07). Routes: foot 129.0 m, car 127.9 m. This is the
same point `game/src/gameplay/content/deliveries.ts` uses as the scripted fallback (fix-scripted-dropoffs). Once a
build publishes this pool, the first delivery resolves through the pool instead of that fallback.

## Reproduce

```sh
source ~/turku3d/wt/fix-address-pool-doors/sandbox.env && cd ~/turku3d/wt/fix-address-pool-doors/repo
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_addresses.py
# The address step alone on the integration layers (4 s); prints 81 and the per-tile counts.
bash pipeline/env/run.sh python -c '
import json; from pathlib import Path; from turku3d.content import addresses as A
root = Path.home() / "turku3d/wt/integration/work/build/layers"
manifest = json.loads((root / "roads/MANIFEST.json").read_text())
graph = json.loads((root / "roads" / manifest["params"]["graph"]["file"]).read_text())
pool = A.build_pool(root / "buildings", graph, A.read_address_points(), A.PIZZERIA,
    {"t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1"}, A.read_streets(Path(manifest["inputs"]["osm"]["path"])))
print(len(pool["addresses"]), pool["tiles"])'
```
