import { chromeArgs } from '../tools/dev/chrome-args.mjs';
import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { gameDist, isolateBrowser, port, reportsRoot } from '../tools/dev/environment.mjs';

const tiles = process.env.TILE_DIST ?? path.join(reportsRoot, 'missing-real-tiles');
isolateBrowser();
const webPort = port(1);
const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;
export default defineConfig({
  testDir: 'tests/e2e', testMatch: 'mp.spec.ts', workers: 1, retries: 0, timeout: 180_000,
  expect: { timeout: 60_000 },
  reporter: [['list']], outputDir: path.join(reportsRoot, 'mp-real-playwright'),
  use: { baseURL: `http://localhost:${webPort}/`, channel: 'chrome', headless: true,
    viewport: { width: 960, height: 540 },
    launchOptions: { args: chromeArgs(['--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU', '--use-angle=metal', '--ignore-gpu-blocklist']) } },
  projects: [{ name: 'real-webgpu' }],
  webServer: { command: `node ../tools/dev/serve-dist.mjs --root ${quote(gameDist)} --port ${webPort} --mount ${quote(`/tiles=${tiles}`)}`,
    url: `http://localhost:${webPort}/`, reuseExistingServer: false, timeout: 30_000 },
});
