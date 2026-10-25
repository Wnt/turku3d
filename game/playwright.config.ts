import { defineConfig } from '@playwright/test';

// Real Chrome (channel 'chrome') in headless mode uses the Mac's Metal GPU (docs/research/engine.md §8).
const PORT = Number(process.env.E2E_PORT ?? 4174);
export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 120_000,
  workers: 2,
  reporter: [['list']],
  outputDir: 'test-results/pw',
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
    command: `npx vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}/`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
