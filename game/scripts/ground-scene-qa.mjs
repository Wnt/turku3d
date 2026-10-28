import { chromeArgs, chromeIgnoreDefaultArgs } from '../../tools/dev/chrome-args.mjs';
// Before/after uses identical assembled geometry. Baseline removes only the declared ground binding.
import { chromium } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { isolateBrowser, reportsRoot } from '../../tools/dev/environment.mjs';

isolateBrowser();
const base = process.argv[2] ?? 'http://127.0.0.1:5313/';
const browserRoot = process.env.TURKU3D_BROWSER_DIR;
if (!browserRoot) throw new Error('Source sandbox.env first');
const output = path.join(reportsRoot, 'ground-scene');
await fs.mkdir(output, { recursive: true });
const views = [
  { id: 'office-front', x: 175.963, z: 257.297, yaw: 144.819 },
  { id: 'office-west', x: 147.292, z: 271.906, yaw: 128.637 },
  { id: 'office-east', x: 203.504, z: 241.290, yaw: 172.443 },
];
function withoutGroundBinding(buffer) {
  const size = buffer.readUInt32LE(12);
  const json = JSON.parse(buffer.subarray(20, 20 + size).toString());
  for (const scene of json.scenes ?? []) if (scene.extras) delete scene.extras.ground_binding;
  const raw = Buffer.from(JSON.stringify(json));
  const text = Buffer.alloc(Math.ceil(raw.length / 4) * 4, 32);
  raw.copy(text);
  const tail = buffer.subarray(20 + size);
  const header = Buffer.from(buffer.subarray(0, 20));
  header.writeUInt32LE(20 + text.length + tail.length, 8);
  header.writeUInt32LE(text.length, 12);
  return Buffer.concat([header, text, tail]);
}
const modes = process.argv[3] ? [process.argv[3]] : ['before', 'after'];
if (modes.some(mode => !['before', 'after'].includes(mode))) throw new Error('Mode must be before or after');
let results = [];
if (process.argv[3]) {
  try { results = JSON.parse(await fs.readFile(path.join(output, 'manifest.json'), 'utf8')).filter(r => !modes.includes(r.mode)); }
  catch { /* No previous capture. */ }
}
for (const backend of (process.argv[4] ?? 'webgpu,webgl').split(',')) {
  const context = await chromium.launchPersistentContext(path.join(browserRoot, 'ground-scene', backend), {
    ignoreDefaultArgs: chromeIgnoreDefaultArgs, channel: 'chrome', headless: true, viewport: { width: 1280, height: 960 }, deviceScaleFactor: 1,
    args: chromeArgs(['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist']),
  });
  try {
    for (const view of views) for (const mode of modes) {
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      page.on('console', e => { if (e.type() === 'error') errors.push(e.text()); });
      if (mode === 'before') await page.route('**/*.glb', async route => {
        const response = await route.fetch();
        await route.fulfill({ response, body: withoutGroundBinding(await response.body()) });
      });
      const url = new URL(base);
      url.searchParams.set('tiles', '/tiles/tileset.json');
      url.searchParams.set('backend', backend);
      url.searchParams.set('cam', [23459500 + view.x, 6704500 - view.z, 6.2, view.yaw, -12, 65].join(','));
      await page.goto(url.href);
      await page.waitForFunction(() => window.__qaReady || window.__qaError, undefined, { timeout: 120000 });
      const state = await page.evaluate(async () => {
        if (window.__qaError) throw new Error(window.__qaError);
        const { Raycaster, Vector3 } = await import('/node_modules/three/build/three.webgpu.js');
        const group = window.__game.tiles.tiles.group;
        const camera = window.__render.camera;
        const ground = [], buildings = [];
        group.traverse(node => {
          if (!node.isMesh) return;
          if (node.userData.layer === 'ground') ground.push(node);
          if (node.userData.layer === 'buildings') buildings.push(node);
        });
        const ray = new Raycaster(new Vector3(camera.position.x, 100, camera.position.z), new Vector3(0, -1, 0));
        const hit = ray.intersectObjects(ground, false)[0];
        if (!hit) throw new Error('No actual ground under QA camera');
        camera.position.y = hit.point.y + 1.7;
        camera.updateMatrixWorld();
        await new Promise(resolve => { let n = 0; const frame = () => ++n > 40 ? resolve() : requestAnimationFrame(frame); frame(); });
        return { groundMeshes: ground.length, buildingMeshes: buildings.length,
          texturedGroundMeshes: ground.filter(m => m.material.name === 'ground-ortho-stochastic-v1').length,
          eyeHeight: 1.7, camera: camera.position.toArray(), residentTris: window.__game.tiles.residentTris,
          perf: window.__perf };
      });
      if (!state.buildingMeshes || !state.groundMeshes || (mode === 'after' && !state.texturedGroundMeshes)) {
        throw new Error(`Missing integrated scene content: ${JSON.stringify(state)}`);
      }
      if (errors.length) throw new Error(errors.join('\n'));
      const file = `${backend}-${view.id}-${mode}.png`;
      const bytes = await page.screenshot({ path: path.join(output, file) });
      results.push({ backend, view: view.id, headingDeg: view.yaw, pitchDeg: -12, fovDeg: 65, quality: 'medium', mode, file, sha256: createHash('sha256').update(bytes).digest('hex'), ...state, errors });
      await fs.writeFile(path.join(output, 'manifest.json'), JSON.stringify(results, null, 2));
      console.log(`${file}: ${state.texturedGroundMeshes} textured ground / ${state.buildingMeshes} building meshes`);
      await page.close();
    }
  } finally { await context.close(); }
}
