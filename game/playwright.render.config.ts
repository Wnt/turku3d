import { defineConfig } from '@playwright/test';
import path from 'node:path';

import { port, reportsRoot, isolateBrowser } from '../tools/dev/environment.mjs';

isolateBrowser();
const PORT = Number(process.env.E2E_PORT ?? port(1));
const args = ['--enable-unsafe-webgpu', '--use-angle=metal', '--ignore-gpu-blocklist'];
export default defineConfig({
  testDir: 'tests/render', timeout: 90_000, workers: 1, reporter: [['list']],
  outputDir: path.join(reportsRoot, 'render-tests'),
  use: {
    baseURL: `http://localhost:${PORT}/`, channel: 'chrome', headless: true,
    viewport: { width: 2560, height: 1440 }, deviceScaleFactor: 1,
    launchOptions: { args },
  },
  // Only throughput measurements unlock vsync. Functional screenshots otherwise flood the GPU while Playwright
  // decodes images or checks DOM state, delaying navigation/teardown and contending with other sandboxes.
  projects: ['webgpu', 'webgl'].flatMap(backend => [
    { name: backend, testIgnore: '**/performance.spec.ts' },
    { name: `${backend}-perf`, testMatch: '**/performance.spec.ts',
      use: { launchOptions: { args: [...args, '--disable-gpu-vsync', '--disable-frame-rate-limit'] } } },
  ]),
  webServer: { command: `node scripts/make-render-fixture.mjs && ./node_modules/.bin/vite preview --port ${PORT} --strictPort`, url: `http://localhost:${PORT}/`,
    reuseExistingServer: false, timeout: 30_000 },
});
