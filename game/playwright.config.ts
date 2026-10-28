import { chromeArgs, chromeIgnoreDefaultArgs } from '../tools/dev/chrome-args.mjs';
import { jobs } from '../tools/dev/environment.mjs';
import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { port, reportsRoot, isolateBrowser } from '../tools/dev/environment.mjs';

isolateBrowser();

// Real Chrome (channel 'chrome') in headless mode uses the Mac's Metal GPU (docs/research/engine.md §8).
const PORT = Number(process.env.E2E_PORT ?? port(1));
export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 120_000,
  // One retry, used only after a failure on a loaded host (loadRetry in tests/e2e/fixtures.ts).
  retries: 1,
  workers: jobs,
  expect: { timeout: 60_000 },
  fullyParallel: true,
  reporter: [['list']],
  outputDir: path.join(reportsRoot, 'playwright'),
  use: {
    baseURL: `http://localhost:${PORT}/`,
    channel: 'chrome',
    headless: process.env.HEADED ? false : true,
    viewport: { width: 960, height: 540 },
    launchOptions: { ignoreDefaultArgs: chromeIgnoreDefaultArgs, args: chromeArgs(['--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU', '--use-angle=metal', '--ignore-gpu-blocklist']) },
  },
  projects: [
    { name: 'webgpu', use: { backendParam: 'webgpu',
      ...(process.env.TURKU3D_BROWSER_WS_WEBGPU ? { connectOptions: { wsEndpoint: process.env.TURKU3D_BROWSER_WS_WEBGPU } } : {}) } as never },
    { name: 'webgl', use: { backendParam: 'webgl',
      ...(process.env.TURKU3D_BROWSER_WS_WEBGL ? { connectOptions: { wsEndpoint: process.env.TURKU3D_BROWSER_WS_WEBGL } } : {}) } as never },
  ],
  webServer: {
    command: `node scripts/make-render-fixture.mjs && ./node_modules/.bin/vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}/`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
