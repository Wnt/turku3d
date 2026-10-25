import { jobs } from '../tools/dev/environment.mjs';
import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { port, reportsRoot, isolateBrowser, gameDist, tileDist } from '../tools/dev/environment.mjs';

isolateBrowser();

// Phase 0 exit check (`make tile`): the built game (dist/) served by tools/dev/serve-dist.mjs with the local tile
// dist mounted at /tiles. TILE_DIST overrides the local output (default $TURKU3D_WORK/dist/tiles).
const PORT = Number(process.env.E2E_TILE_PORT ?? port(1));
const TILE_DIST = tileDist;
export default defineConfig({
  testDir: 'tests/e2e-tile',
  timeout: 120_000,
  workers: jobs,
  expect: { timeout: 60_000 },
  fullyParallel: true,
  reporter: [['list']],
  outputDir: path.join(reportsRoot, 'playwright-tile'),
  use: {
    baseURL: `http://localhost:${PORT}/`,
    channel: 'chrome',
    headless: process.env.HEADED ? false : true,
    viewport: { width: 1280, height: 720 },
    launchOptions: { args: ['--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU', '--use-angle=metal', '--ignore-gpu-blocklist'] },
  },
  projects: [
    { name: 'webgpu', use: { backendParam: 'webgpu' } as never },
    { name: 'webgl', use: { backendParam: 'webgl' } as never },
  ],
  webServer: {
    command: `node ../tools/dev/serve-dist.mjs --root ${JSON.stringify(gameDist)} --port ${PORT} --mount ${JSON.stringify(`/tiles=${TILE_DIST}`)}`,
    url: `http://localhost:${PORT}/`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
