import { defineConfig } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';
import { settings } from '../tools/dev/play.mjs';

const s = settings();
fs.mkdirSync(s.browser, { recursive: true });
process.env.TMPDIR = s.browser;
const report = process.env.PLAYTEST_REPORT ?? s.report;
const game = process.env.PLAY_GAME_DIST ?? s.game;
const backends = ['webgpu', 'webgl'];
// Each backend has its own server; Chrome contexts/profiles are isolated by Playwright.
const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;
export default defineConfig({
  testDir: 'tests',
  testMatch: ['e2e-playtest/**/*.spec.ts', 'e2e/gamepad.spec.ts'],
  fullyParallel: true,
  workers: 2,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 8_000 },
  outputDir: path.join(report, 'results'),
  reporter: [['list'], ['./tests/e2e-playtest/reporter.ts', { outputDir: report }]],
  use: {
    channel: 'chrome', headless: !process.env.HEADED,
    viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1,
    video: { mode: 'on', fps: 12, size: { width: 640, height: 360 } }, screenshot: 'only-on-failure', trace: { mode: 'retain-on-failure', screenshots: false, snapshots: true, sources: true },
    launchOptions: { args: ['--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU', '--use-angle=metal', '--ignore-gpu-blocklist'] },
  },
  projects: backends.map((backend, index) => ({ name: backend, use: { baseURL: `http://127.0.0.1:${s.port + 2 + index}/` } })),
  webServer: backends.map((_, index) => ({
    command: `node ../tools/dev/serve-dist.mjs --root ${quote(game)} --port ${s.port + 2 + index} --mount ${quote(`/tiles=${s.tiles}`)}`,
    url: `http://127.0.0.1:${s.port + 2 + index}/`, reuseExistingServer: false, timeout: 15_000,
  })),
});
