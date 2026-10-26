# W1-09f: fully generated facade pilot

Date: 2026-10-26. Pilot sandbox: `w1-09f-generated-facades`.
The reuse study is [helsinki-rides-reuse.md](../research/helsinki-rides-reuse.md).

## Selected walls and image use

The wall closest to the office coordinate is `103295893K_36` (Läntinen Rantakatu 15). The other two buildings are the red timber wall `103295888D_22` and the stone/plaster wall `103295899S_259`. All three are street-facing, geometrically matched to the authoritative buildings layer, and had four panorama reference views available. Two full-wall rectified views per wall were used in the staged metadata. The source photos were used as appearance references only; the three final albedos were generated anew.

| Facade | Wall, metres | Staged texture | Image calls | Max content-aspect error | Opening/foreign-object QA |
| --- | ---: | ---: | ---: | ---: | --- |
| `103295893K_36` | 11.032 × 6.984 | 348 × 220 | 2 | 0.027% | five upper windows, four plinth vents; clean |
| `103295888D_22` | 12.702 × 5.214 | 400 × 164 | 1 | 0.082% | four shuttered windows; clean |
| `103295899S_259` | 8.748 × 17.869 | 276 × 564 | 1 | 0.082% | four upper openings, two bay-window rows, arched window and door; clean |

One office-wall image was rejected after visual QA found three plinth vents rather than the four visible in a second reference. One edit call corrected the vent count, and the new image passed the same QA. The final three maps have matched metre texel density near 32 pixels/metre in staging, validated albedo/normal/ORM KTX2 files, and source/image hashes. Initial 64 pixels/metre encodes forced visible reductions in adjacent walls to meet the 8 MB tile cap. Re-encoding these three at 32 pixels/metre restored the neighboring walls' baseline clarity while retaining the generated opening and material detail.

The built-in image generator made **four calls** (2 + 1 + 1), below the 30-call cap. Weekly quota read 12% before the pilot and 15% after; the delta is account-wide and includes other active agents. The visual QA used four additional, non-image-generation Codex calls. Calls and final image hashes are in `$TURKU3D_WORK/textures/pilot/generated-manifest.json`; QA answers and the exact generated image bytes are in the pilot run directory.

## Publication and evidence

All three accepted KTX2 map sets and JSON sidecars were published under the export lock to `$TURKU3D_DATA/facade-approvals/assembly/facades/`. The persistent shared manifest had the same authoritative inventory hash and 340 bound IDs after publication. The sandbox clone at `$TURKU3D_WORK/textures/assembly` was used for the four-tile build with 318 published-baseline bindings. The live-release symlink was not modified.

- [Texture before/after sheet](w1-09f-generated-facades/texture-comparison.jpg)
- [WebGPU street before/after sheet](w1-09f-generated-facades/webgpu-comparison.jpg)
- [WebGL2 street before/after sheet](w1-09f-generated-facades/webgl-comparison.jpg)

The sheets use the same camera pose, time and viewport for each before/after pair. Individual full-size screenshots and render metadata remain in `$TURKU3D_WORK/reports/generated-facades/`.

## Verification

```sh
source ../sandbox.env
scripts/dev/gate --paths pipeline/turku3d/package/assemble.py pipeline/turku3d/package/README.md pipeline/tests/test_facade_density.py textures/turkutex/building_jobs.py textures/turkutex/facade_batch.py textures/turkutex/facade_generated_qa.py textures/prompts/facade_generate.md textures/tests/test_facade_batch.py docs/research/helsinki-rides-reuse.md textures/README.md
textures/.venv/bin/python -m turkutex.facade_generated_qa --facades "$TURKU3D_WORK/textures/pilot/facades.geojson" --refs "$TURKU3D_WORK/textures/refs" --generated "$TURKU3D_WORK/textures/pilot/generated" --staging "$TURKU3D_WORK/textures/pilot/staging32" --visual-qa "$TURKU3D_WORK/textures/pilot/run_visual_qa/out.jsonl" --output "$TURKU3D_WORK/textures/pilot/acceptance32.json" --min-ppm 32
make tile-build T='t0_0_0 t0_0_1 t0_1_0 t0_1_1'
make tile-validate
```

The focused gate passed in 23.900 seconds after rebase (Python lint, policy, texture and pipeline tests). The automated generated-map acceptance passed all three final images. The sandbox build bound 318 facades and produced four visual tiles at 7,096,861, 7,977,925, 7,955,301 and 7,941,039 wire bytes respectively, all under the 8 MB cap. WebGPU and WebGL2 renders completed for all three views; the linked comparison sheets contain the final 32 pixels/metre build.

The sandbox `make tile-validate` report was compared with a run against `$TURKU3D_DATA/live/tiles` using the same `t0_0_0` input, layers, 2,000 walkers and validator options. The report check objects are identical. Both report cars failing in 16/16 collision tiles, buildings in 4/16 (`c_1_3`, `c_3_0`, `c_3_1`, `c_3_3`), curbs in 1/16 (`c_1_2`), and stairs lacking testable input. Format, seams, holes and below-ground checks pass in both. These failures predate the pilot. Reports: `$TURKU3D_WORK/reports/validate-aoi.json` and `$TURKU3D_WORK/reports/live-validation/validate-aoi.json`.

## Follow-ups

- Published-release collision validation has the same existing failures as the sandbox: cars 16/16, buildings 4/16 (`c_1_3`, `c_3_0`, `c_3_1`, `c_3_3`), curbs 1/16 (`c_1_2`), and no testable stairs input. The validator check objects match exactly between both runs.
