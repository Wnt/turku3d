# Facade styles for high-visibility buildings

Date: 2026-10-27. Sandbox: `content-facades-1027-1806`. The rollout used
`gpt-6-luna` low for generation, `gpt-6-astra` low for reference-grid and
visual review, `-j 4`, and at most one feedback retry per wall. The published
approval inventory hash remained `ae978911…785ebd3e4df2f`.

## Results

| Building | Screened street-facing walls, widest first | Selected source and result | Image calls |
| --- | --- | --- | ---: |
| `103440113U` | `_26` (14.6 m; grid found pavement and foliage), `_142` (7.3 m; usable four-storey reference) | `_142` rejected. First image retained pavement; retry removed it but failed the measured aspect check. No map was published. | 2 |
| `103304672S` | `_10` (8.4 m; usable seven-storey reference) | `_10` accepted on the first image, passed generated QA and manual inspection. `_13` and `_12` derived from its new building style; both passed derived QA. | 1 |
| `gis-103277359P` | `_7` (44.5 m; foreground building obscured it), `_20` (26.7 m; usable curtain-wall reference), `_12` (19.1 m; pavement and road) | `_20` rejected. The first image had separate windows and pavement; the retry moved the entrance and failed both aspect and opening-grid checks. No map was published. | 2 |

Reference rendering screened Look Around, then Google, then Mapillary. The
reference/generated contact sheet remains outside the repository at
`$TURKU3D_WORK/reports/content-facades-1027-1806/reference-generated-contact.jpg`.
The generated/derived map contact sheet is at
`$TURKU3D_WORK/reports/content-facades-1027-1806/derived-contact.jpg`.
No street imagery was committed.

Five image calls were made, below the 12-call cap. The weekly Codex quota was
**32% before** generation and **33% after** publication (`python -m
turkutex.codex.usage`). The immediate rollout summary read 32% at both ends;
the later quota reading advanced to 33%. The reviewer made six initial
reference-grid calls and five visual QA calls (three initial, two after retry).
The approval assembly increased from 218 to 221 bound walls: one generated and
two derived. The building style uses `103304672S_10` as its source and passed
2/2 trial derivations. `facade_derive --building` lets the planner include its
street-facing walls outside the office core.

## Tile and street check

Rebuilt `t0_0_0`, `t0_1_0`, and `t0_1_1`. Wire sizes from the sandbox
`dist/tiles/manifest.json` are below the 8,000,000-byte cap:

| Tile | Wire bytes |
| --- | ---: |
| `t0_0_0` | 5,026,359 |
| `t0_1_0` | 3,134,735 |
| `t0_1_1` | 3,309,408 |

The facade ground audit sampled **221/221** approved walls without failure.
Its 61 buried bases are accepted on sloped terrain. Street-pose screenshots
were captured from both Chrome backends after `__qaReady`. They show the
accepted building and the current fallback appearance of the two rejected
buildings. The game scene is visibly hazy in these captures; they establish
backend loading and placement, while the generated/derived contact sheet is
the clearer texture inspection.

| Building | WebGPU | WebGL2 |
| --- | --- | --- |
| `103440113U` | [street view](content-facades-1027-1806/103440113U_142-webgpu.jpg) | [street view](content-facades-1027-1806/103440113U_142-webgl.jpg) |
| `103304672S` | [street view](content-facades-1027-1806/103304672S_10-webgpu.jpg) | [street view](content-facades-1027-1806/103304672S_10-webgl.jpg) |
| `gis-103277359P` | [street view](content-facades-1027-1806/gis-103277359P_20-webgpu.jpg) | [street view](content-facades-1027-1806/gis-103277359P_20-webgl.jpg) |

## Verification

```sh
source ../sandbox.env
textures/.venv/bin/python -m pytest -q textures/tests/test_facade_style.py
make tile-build T='t0_0_0 t0_1_0 t0_1_1'
bash pipeline/env/run.sh python -m turku3d.package.facade_ground_audit
scripts/dev/gate --fast --base main
```

The two rejected buildings remain concrete follow-up work: find a different
geometry-matched street-facing wall or improve the generator's aspect and
opening placement before publishing their styles.
