## Result: DONE

## What changed

Tile shadow batches now use layer 1. The sun shadow camera enables that layer before CSM clones it into each cascade; colour, normal and reflection cameras retain layer 0. Static geometry remains merged into one shadow caster per tile, without submitting that duplicate geometry to the other passes. Original meshes and vegetation retain their existing rendering and shadow behaviour. A synthetic unit check verifies camera exclusion, cloned shadow-camera inclusion and ordinary caster inclusion.

The integration logs first record the regression in `integration-4dd727850c69.1-advisory.log`: 516 draws / 3,844,745 triangles on both backends. The preceding measured batch, `integration-ec6377551eff-1.log`, records 458 / 3,723,951. The visible tile set grew from six to twelve; ground/ortho meshes grew 3 → 8, shadow proxies (`unknown/unknown`) 6 → 8, props palette/sign/bus-stop meshes 3/3/2 → 6/6/4, and outer building meshes 3 → 4. Tree bark meshes fell 12 → 1 and foliage grew 9 → 10. The logs identify the first failing integrated batch, but contain no intermediate bridge samples isolating each tree/facade landing between those commits; they do not establish that the fast facade producer alone caused the increase.

On a sandbox APFS copy of the published tiles, the fixed WebGPU bridge sample uses the same visible tiles and material/mesh counts as the 516-draw advisory, but records **492 draws / 3,377,309 triangles**. Removing redundant shadow-proxy submissions saves 24 draws and 467,436 submitted triangles. The 500-draw threshold is unchanged. The remaining eight-draw margin is small; future visible geometry still needs batching discipline.

## How to verify

From the repository root:

```sh
source ../sandbox.env
mkdir -p "$TURKU3D_WORK/dist"
# Once, when no sandbox tile copy exists:
cp -cR "$TURKU3D_DATA/live/tiles" "$TURKU3D_WORK/dist/tiles"
scripts/dev/codex-task wait-cmd -- make game-build
scripts/dev/codex-task wait-cmd --timeout 900 --tail 65 -- python3 scripts/dev/host_slots.py browser -- game/node_modules/.bin/playwright test -c game/playwright.tile.config.ts game/tests/e2e-tile/area.spec.ts --project webgpu --grep 'bridge gameplay draw budget' --workers 1
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

The browser uses sandbox port 5201 and the sandbox Chrome profile. Its existing `bridge-gameplay-performance` attachment records the full sample under `$TURKU3D_WORK/reports/playwright-tile`. Inspect the pinned r186 `ShadowNode.updateShadow` and `CSMShadowNode._init`: the explicit shadow layer mask is retained and cloned into the cascades.

## Gate output

- Production build/typecheck: PASS, 2.5 s.
- Focused WebGPU bridge spec: 1 passed, 14.6 s; browser budget charged 0.3 minutes.
- Fast gate: GREEN, 16.933 s (including first dependency synchronisation). Policy, lint, game types and game unit stages passed.

## Follow-ups

None required for this fix. WebGL2 browser verification remains with the integrator; the requested WebGPU spec passed.
