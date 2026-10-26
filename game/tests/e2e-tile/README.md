# Real tile Chrome checks

After `make refresh`, build the four L0 tiles and the game in your sourced sandbox:

```sh
make tile-build T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"
make game-build
cd game && npx playwright test -c playwright.tile.config.ts tests/e2e-tile/area.spec.ts
```

`viewpoints.json` contains deterministic street poses on road or sidewalk triangles
sampled from the four published ground tiles, plus the bridge and office poses.
The area test reuses one QA page per backend across 24 street poses and one aerial
pose. It writes `$TURKU3D_WORK/reports/tile-area-{webgpu,webgl}.json`, including
draws and rendered triangles at each view and the worst street location. Every
street view must stay below 500 draws. Four distributed ID views also require
visible facade pixels. The full landing suite runs all e2e tile specs.
