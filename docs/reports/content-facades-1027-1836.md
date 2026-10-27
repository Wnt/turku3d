# Generated facade styles for high-visibility buildings

Date: 2026-10-27. Sandbox: `content-facades-1027-1836`.

The rollout used `gpt-6-luna` low for images, `gpt-6-astra` low for reference-grid and visual review, `-j 4`, and no more than one feedback retry per wall. Reference rendering followed Look Around → Google → Mapillary. The accepted walls used Look Around. The queue in `$TURKU3D_WORK/textures/rollout-queue.json` held only the six screened candidates listed below. The reference and generated contact sheet, which contains street photos, remains outside the repository at `$TURKU3D_WORK/reports/content-facades-1027-1836/contact-generated.jpg`. Derived contact sheets are in the same directory.

| Building | Reference screening and source choice | Result | Image calls |
| --- | --- | --- | ---: |
| `103277350D` | `_27` (42.6 m) deferred by the rollout's 3.5:1 aspect rule; `_31` (11.8 m) and `_29` (10.3 m) passed the grid. Chose widest eligible `_31`. | `_31` accepted after one feedback retry corrected ground-floor openings. Generated map plus 15 derived walls; 15/15 passed derived QA. | 2 |
| `gis-103277357M` | `_72` (12.9 m) failed grid review because foliage hides the target; `_99` (11.2 m) passed. Chose `_99`. | `_99` accepted after one feedback retry corrected the measured aspect. Generated map plus 22 derived walls; 22/22 passed derived QA. | 2 |
| `gis-103277356L` | `_69` (12.7 m) and `_45` (11.4 m) passed the reference-grid check. Tried the wider `_69` first, then `_45`. | Neither published. After one retry, `_69` still had the wrong roof-dormer and basement-opening grid; `_45` still had incorrect ground openings and a missing horizontal band. Existing fallback remains. | 4 |

**Image budget and quota.** Eight image calls, below the 12-call cap. `textures/.venv/bin/python -m turkutex.codex.usage` read **33% before** and **34% after**. The rollout summary records eight image calls, six reference-grid calls and the automated/manual verdicts. Approval bindings rose from 221 to 260: two generated sources and 37 derived walls. Publication used the export lock and preserved the approval inventory.

## Tile and street checks

Both accepted buildings are in `t0_1_0`. Rebuilding it bound 260 approved facades. Its manifest records **4,545,835 wire bytes**, below the 8,000,000-byte cap. The facade ground audit sampled **260/260** approved walls without failure; the accepted bases buried by sloping terrain were reported as buried, not floating. Street-pose captures reached `__qaReady` on both backends (six tests passed). The generated walls show the new style in the packaged tile.

| Wall | WebGPU | WebGL2 |
| --- | --- | --- |
| Accepted `103277350D_31` | [street render](content-facades-1027-1836/103277350D_31-webgpu.jpg) | [street render](content-facades-1027-1836/103277350D_31-webgl.jpg) |
| Accepted `gis-103277357M_99` | [street render](content-facades-1027-1836/gis-103277357M_99-webgpu.jpg) | [street render](content-facades-1027-1836/gis-103277357M_99-webgl.jpg) |
| Rejected `gis-103277356L_69` | [occluded street pose](content-facades-1027-1836/gis-103277356L_69-webgpu.jpg) | [occluded street pose](content-facades-1027-1836/gis-103277356L_69-webgl.jpg) |

The rejected building's target wall is hidden by adjacent geometry in the tested street poses. Those captures establish backend loading and the occlusion, not its wall appearance. Its reference-grid and reviewer records remain in the sandbox rollout run.

## Verification

```sh
source ../sandbox.env
textures/.venv/bin/python -m turkutex.codex.usage
make tile-build T=t0_1_0
bash pipeline/env/run.sh python -m turku3d.package.facade_ground_audit
scripts/dev/gate --fast --base main
```

The next content pass can seek a different visible wall for `gis-103277356L` or improve the generator's dormer and ground-opening geometry before publishing a style for it.
