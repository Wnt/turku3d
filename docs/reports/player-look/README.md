# Player look: bind pose and camera correction

> Images derived from street-level photos (Google Street View, Apple Look Around, Mapillary) or
> Google 3D tiles were removed from the public repository on 2026-10-27; they remain in the local archive.

The 1.79 m MakeHuman/UAL character already had an appropriate overall height and textured skin, jeans and shoes. The visual defect came from the offline retarget: MakeHuman's `spine_03` rest joint is at 1.095 m, while UAL's corresponding `DEF-spine.003` joint is at 1.315 m. Moving chest vertices by that 22 cm difference pulled the hoodie collar up to the nose. The head was largely hidden by the clothing, especially from the default camera, which looked down from above head height. The source UAL idle also has a wide stance; that is an animation choice, not a broken bind pose.

The rebuilt mesh keeps the source torso and clavicle vertices at the human's 1.035 height scale while retaining UAL's joints, inverse bind matrices and clips. The camera now aims 1.25 m above the feet at a slight downward pitch. Collider position, foot IK, clip timing and skin/cloth roughness remain the same. Bind-pose inspection shows the collar below the chin; the feet remain on the ground in the captured idle, walk and run states.

These are 1280×720 Chrome captures from the synthetic render fixture. The baseline uses the prior committed asset and camera. Each state was driven with a virtual Xbox controller on both backends; walk used a partial left-stick deflection and run used full deflection plus sprint.

| Backend | State | Before | After |
|---|---|---|---|
| WebGPU | Idle | *PNG (withheld)* | *PNG (withheld)* |
| WebGPU | Walk | [PNG](before/webgpu-walk.png) | [PNG](after/webgpu-walk.png) |
| WebGPU | Run | [PNG](before/webgpu-run.png) | [PNG](after/webgpu-run.png) |
| WebGL2 | Idle | [PNG](before/webgl-idle.png) | [PNG](after/webgl-idle.png) |
| WebGL2 | Walk | [PNG](before/webgl-walk.png) | [PNG](after/webgl-walk.png) |
| WebGL2 | Run | [PNG](before/webgl-run.png) | [PNG](after/webgl-run.png) |

To repeat the final captures from the repo root, source the sandbox environment and run:

```sh
npm --prefix game run build
PLAYER_CAPTURE=after TURKU3D_JOBS=2 game/node_modules/.bin/playwright test -c game/playwright.config.ts game/tests/e2e/character-look.spec.ts
```

The files are written under `$TURKU3D_WORK/reports/player-look/after/`. The same test without `PLAYER_CAPTURE` checks grounded feet and errors without writing screenshots.

A read-only check against the published Turku tiles also loaded the updated player in *WebGPU (withheld)*: 701,120 resident visual triangles and no game errors. The matching live WebGL2 attempt did not settle within 60 s under concurrent Chrome load; the WebGL2 synthetic captures and controller tests above passed. Fresh four-tile assembly was stopped while encoding textures after four minutes to respect this brief's timebox.
