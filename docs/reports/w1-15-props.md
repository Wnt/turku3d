# W1-15 props first version

The producer is rebased onto batch-1 main (`aaf46d1`). It writes layer v1 only;
no game runtime, assembler, shared schema or gate routing changes are included.
Development defaults to the office core (250×250 m, 16 collision tiles).
The retained final acceptance layer covers the four L0 tiles plus 50 m.

## Delivery and budget

`/Users/wnt/turku3d/wt/w1-15-props/work/build/layers/props/MANIFEST.json`
references 14 visual tiles and 88 collision tiles, including the surrounding margin.
Source inputs are hashed, raw data was not modified, and every output hash verifies.

| Type | Placed |
|---|---:|
| Bench | 113 |
| Bin | 137 |
| Bollard | 2 |
| Bike rack location | 51 |
| Föli station location | 10 |
| Parking meter | 8 |
| OSM street lamp | 86 |
| Bus stop | 23 |
| Supported traffic sign | 329 |
| Generic post / unsupported type | 534 |
| **Total** | **1,293** |

- 109,704 visual triangles, 1,598,828 source GLB bytes (1.52 MiB).
- 1,904 collision primitives, 133,594 compressed NPZ bytes.
- All 88 primitive-only tiles round-tripped through the assembler codec; encoded
  collision total 113,216 bytes. No collision mesh triangles are introduced.
- Mesh geometry is shared by object nodes per type/sign within each visual tile.
  GPU draw batching is not implemented: Chrome overview measured 1,259 calls and
  106,632 visible triangles, including the preview grid. This is a source-layer
  smoke test, not a 1440p integrated-game frame-rate claim.
- Final source processing/height sampling: 42.678 s under concurrent host load.

## Verification and evidence

```sh
source ../sandbox.env
bash pipeline/env/run.sh python -m turku3d.props             # office core development
bash pipeline/env/run.sh python -m turku3d.props --area phase1  # final acceptance
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_props.py
bash pipeline/env/run.sh scripts/dev/gate
bash pipeline/env/run.sh python -m turku3d.props.preview
```

The focused suite covers type mapping, actual parquet ingestion, unknown sign
fallbacks, duplicate stop suppression, ground interpolation/rejection, road-facing
rotation, primitive transforms, negative/edge tile ownership, shared mesh references,
GLB/manifest round trips, stale file exclusion and nondegenerate geometry.

Browser outputs (1600×1000 Chrome/Metal; no page errors):

- [Model catalogue](/Users/wnt/turku3d/wt/w1-15-props/work/reports/props/catalog.png)
- [Actual office riverside placements](/Users/wnt/turku3d/wt/w1-15-props/work/reports/props/detail.png)
- [Actual AOI placements](/Users/wnt/turku3d/wt/w1-15-props/work/reports/props/aoi.png)
- [Browser counters](/Users/wnt/turku3d/wt/w1-15-props/work/reports/props/browser.json)
- [Gate output](/Users/wnt/turku3d/wt/w1-15-props/work/reports/props-gate-final.log)

Visual review caught a pole protruding through the sign face. The face was moved
forward and the catalogue rerendered; prohibitory/directional signs now have circular
plates and B5 has a triangular plate. Catalogue nodes are rearranged for inspection;
the other views preserve actual source coordinates. Ground/buildings are omitted.

The first gate invocation lacked the native meshopt environment and failed three
existing assembler tests. Running through `pipeline/env/run.sh` resolved this;
the pre-rebase selected gate passed in 76.319 s. The final gate is recorded in the
linked log and the run result. No assertions or thresholds were weakened.

## Limitations and concrete follow-ups

- The city electricity/pole layer is restricted (HTTP 401 in the source inventory).
  OSM supplies the 86 lamps; do not infer missing city poles from unlabelled codes.
- Eight objects lack ground samples within 15 m and are omitted with their IDs in
  metrics. The maximum accepted nearest ground distance is 13.77 m; bridge-deck and
  bank heights need later review against assembled ground and photographs.
- Dimensions and road-facing yaw are representative. No lidar orientation fitting
  or model-specific capacity inference is claimed. Unknown signs remain generic posts.
- Eight stylised Finnish faces are generated locally; H25 text and other sign codes
  remain for a later artwork pass. Source and CC0 details are in the producer README.
- Integrate the completed layer with the other scene layers, then profile rendering
  and consider per-type GPU batching while retaining stable object IDs. The gate-speed
  owner has already included props routing in its separate branch (`988886f`).
