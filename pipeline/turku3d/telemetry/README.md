# Content priority from play telemetry

`turku3d.telemetry.priority` reads the play sessions that the localhost game records
(`game/src/telemetry/README.md`) and ranks what to generate next: facades for the buildings the player sees most
and closest, and ground (plus buildings and roads) for the tiles the player walks into beyond the built area.

```sh
source ../sandbox.env    # or the main environment
python -m turku3d.telemetry.priority                          # $TURKU3D_WORK/telemetry -> priority.json, priority.md
python -m turku3d.telemetry.priority --sessions ~/turku3d/data/telemetry --out ~/turku3d/data/telemetry/priority.json
python -m turku3d.telemetry.priority --hours 2                # only the last two hours of samples
python -m pytest -q tests/test_telemetry.py                   # from pipeline/, synthetic sessions
```

Options: `--sessions DIR` (default `$TURKU3D_WORK/telemetry`, which is `$TURKU3D_DATA/telemetry` on main), `--out
FILE` (default `<sessions>/priority.json`; `priority.md` is written next to it), `--hours N`, `--assembly DIR`
(default `$TURKU3D_DATA/facade-approvals/assembly`).

## Scoring

- **Wall**: `score = Σ_samples screen_fraction × clamp(60 / distance_m, 0.2, 3)`. `seen_seconds` counts the samples
  (one per second) in which the wall was listed.
- **Building**: the sum of its walls; `best_wall` is its highest scoring wall.
- **Missing tile**: one point per second the player stood in it, 0.5 per second it was one of the two tiles ahead.
- **Status** from the approval assembly (`MANIFEST.json` `bound`, the per-wall sidecar `source_mode`, and
  `facades/<building>/style.json`): `generated` (own generated style), `derived-only` (derived maps without an own
  style), `neighbour` (only `derived-neighbour` maps) and `unbound` (no map).

## `priority.json`

```json
{"generated_at": "2026-10-27T12:00:00+00:00", "sessions": ["session-20261027.jsonl"], "sample_count": 120,
 "buildings": [{"building_id": "103295901U", "score": 12.3, "status": "derived-only|generated|neighbour|unbound",
                "best_wall": "103295901U_23",
                "walls": [{"facade_id": "103295901U_23", "score": 1.2, "seen_seconds": 40, "mean_distance_m": 22.5,
                           "max_screen_fraction": 0.08}]}],
 "missing_tiles": [{"tile": "t0_2_1", "score": 30.0, "stood_seconds": 25, "looked_seconds": 10,
                    "bbox_en": [23460000, 6704000, 23460250, 6704250]}]}
```

Buildings and walls are sorted by score, highest first; `bbox_en` is `[E0, N0, E1, N1]` of the tile.
`priority.md` lists the top 20 buildings and every missing tile.
