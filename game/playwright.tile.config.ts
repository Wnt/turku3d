import { chromeArgs } from '../tools/dev/chrome-args.mjs';
import { jobs } from '../tools/dev/environment.mjs';
import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { port, reportsRoot, isolateBrowser, gameDist, tileDist } from '../tools/dev/environment.mjs';

isolateBrowser();

// Phase 0 exit check (`make tile`): the built game (dist/) served by tools/dev/serve-dist.mjs with the local tile
// dist mounted at /tiles. TILE_DIST overrides the local output (default $TURKU3D_WORK/dist/tiles).
const PORT = Number(process.env.E2E_TILE_PORT ?? port(1));
const TILE_DIST = tileDist;
const GAME_DIST = process.env.E2E_GAME_DIST || gameDist;
const DEV = process.env.E2E_TILE_DEV === '1';
export default defineConfig({
  testDir: 'tests/e2e-tile',
  timeout: 120_000,
  // One retry, used only after a failure on a loaded host (loadRetry in tests/e2e/fixtures.ts).
  retries: 1,
  workers: jobs,
  expect: { timeout: 60_000 },
  fullyParallel: true,
  reporter: [['list']],
  outputDir: path.join(reportsRoot, 'playwright-tile'),
  use: {
    baseURL: `http://localhost:${PORT}/`,
    channel: 'chrome',
    headless: process.env.HEADED ? false : true,
    viewport: { width: 960, height: 540 },
    launchOptions: { args: chromeArgs(['--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU', '--use-angle=metal', '--ignore-gpu-blocklist']) },
  },
  projects: [
    { name: 'webgpu', use: { backendParam: 'webgpu',
      ...(process.env.TURKU3D_BROWSER_WS_WEBGPU ? { connectOptions: { wsEndpoint: process.env.TURKU3D_BROWSER_WS_WEBGPU } } : {}) } as never },
    { name: 'webgl', use: { backendParam: 'webgl',
      ...(process.env.TURKU3D_BROWSER_WS_WEBGL ? { connectOptions: { wsEndpoint: process.env.TURKU3D_BROWSER_WS_WEBGL } } : {}) } as never },
  ],
  webServer: process.env.E2E_TILE_EXTERNAL_SERVER === '1' ? undefined : {
    command: DEV ? `./node_modules/.bin/vite --host 127.0.0.1 --port ${PORT} --strictPort`
      : `node ../tools/dev/serve-dist.mjs --root ${JSON.stringify(GAME_DIST)} --port ${PORT} --mount ${JSON.stringify(`/tiles=${TILE_DIST}`)}`,
    url: `http://localhost:${PORT}/`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
