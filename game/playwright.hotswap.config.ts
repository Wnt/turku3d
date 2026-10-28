import { chromeArgs } from '../tools/dev/chrome-args.mjs';
import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { port, reportsRoot, isolateBrowser } from '../tools/dev/environment.mjs';

isolateBrowser();

// The localhost game's hot path (tools/dev/localhost/README.md): the spec runs its own Vite dev server on a copy of
// the game sources (port base + 4), edits them and checks what the open tab keeps. One worker: the copy is shared.
const PORT = Number(process.env.E2E_HOTSWAP_PORT ?? port(4));
export default defineConfig({
  testDir: 'tests/e2e-hotswap',
  timeout: 180_000,
  retries: 1,
  workers: 1,
  expect: { timeout: 60_000 },
  reporter: [['list']],
  outputDir: path.join(reportsRoot, 'playwright-hotswap'),
  use: {
    baseURL: `http://localhost:${PORT}/`,
    channel: 'chrome',
    headless: true,
    viewport: { width: 960, height: 540 },
    launchOptions: { args: chromeArgs(['--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU', '--use-angle=metal', '--ignore-gpu-blocklist']) },
  },
  projects: [{ name: 'webgpu' }],
});
