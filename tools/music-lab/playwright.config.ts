import { chromeArgs } from '../dev/chrome-args.mjs';
import { defineConfig } from '@playwright/test';
const port = Number(process.env.TURKU3D_PORT_BASE || 5170) + 3;
export default defineConfig({ testDir: 'tests', timeout: 30000, use: { baseURL: `http://127.0.0.1:${port}`, channel: 'chrome', headless: true, launchOptions: { args: chromeArgs(['--autoplay-policy=no-user-gesture-required']) } }, webServer: { command: `npm run dev -- --port ${port}`, url: `http://127.0.0.1:${port}`, reuseExistingServer: false }, reporter: 'list', outputDir: process.env.TURKU3D_WORK ? `${process.env.TURKU3D_WORK}/reports/music-lab-playwright` : 'test-results' });
