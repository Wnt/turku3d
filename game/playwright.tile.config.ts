import { defineConfig } from '@playwright/test';
import os from 'node:os';
import path from 'node:path';

// Phase 0 exit check (`make tile`): the built game (dist/) served by tools/dev/serve-dist.mjs with the synced tile
// dist mounted at /tiles. TILE_DIST overrides the mirror (default ~/turku3d-data/dist/tiles).
const PORT = Number(process.env.E2E_TILE_PORT ?? 4175);
export const TILE_DIST = process.env.TILE_DIST ?? path.join(os.homedir(), 'turku3d-data/dist/tiles');
export default defineConfig({
  testDir: 'tests/e2e-tile',
  timeout: 120_000,
  workers: 1,
  reporter: [['list']],
  outputDir: 'test-results/pw-tile',
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
    command: `node ../tools/dev/serve-dist.mjs --root dist --port ${PORT} --mount /tiles=${TILE_DIST}`,
    url: `http://localhost:${PORT}/`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
