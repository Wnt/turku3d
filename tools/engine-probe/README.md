# engine-probe

Throwaway feasibility probe for the Turku open-world browser game: three.js r186 (WebGPURenderer, auto/forced WebGL2 fallback) + Rapier 0.21 WASM.

Scene (`src/level.js`): one merged trimesh with a 12 cm granite curb, 8-step staircase (17 cm rise / 30 cm run),
a 15° ramp (must climb), a 55° slope (must block), and an 8° driveway ramp for the car.
`src/sim.js` = physics only (same code in Node and browser): 3 capsule walkers (KinematicCharacterController,
autostep 25 cm, snap-to-ground 30 cm, max climb 45°) + DynamicRayCastVehicleController car.

```bash
# Source your sandbox.env before entering tools/engine-probe.
npm ci
npm run build
npm run test:node            # physics in Node, prints position log + verdict
npm run test:headless        # vite preview + headless Google Chrome (Playwright) -> PASS/FAIL, probe-chrome.png
npm run test:headless:webgl  # same, forced WebGL2 backend
npm run test:splat           # Spark gaussian splats + mesh + Rapier (WebGLRenderer); `node scripts/splat-test.mjs webgpu` shows the WebGPU failure
npm run test:fx              # 1080p frame-time probe: CSM + GTAO + TRAA + bloom, WebGPU vs WebGL2
npm run test:glb             # loads server-built meshopt+KTX2 GLBs (public/assets) and builds a Rapier trimesh from them
npm run bench:trimesh        # Rapier trimesh size/time/query benchmark
npm run bench:snapshot       # Rapier world snapshot size/restore benchmark
npm run dev                  # interactive: open http://localhost:$TURKU3D_PORT_BASE/ (arrow keys drive the car), /splat.html, /fx.html, /glb.html
```
Full findings: `../../docs/research/engine.md`.

Builds and Vite caches use `$TURKU3D_WORK/build/engine-probe` and `$TURKU3D_WORK/cache/engine-probe-vite`.
Screenshots use `$TURKU3D_WORK/reports/engine-probe`; Chrome profiles use `$TURKU3D_BROWSER_DIR/tmp`.
Preview/headless, splat, FX and GLB probes use offsets +3, +4, +5 and +6 from `TURKU3D_PORT_BASE`.
Run probes separately from other tests using those ports within the same sandbox.

From the repository root, `make engine-probe-test` installs pinned dependencies, builds and runs the Node physics check.
