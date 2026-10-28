import { defineConfig } from 'vitest/config';
import wasm from 'vite-plugin-wasm';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [wasm()],
  resolve: { alias: { '@dimforge/rapier3d': fileURLToPath(new URL('./node_modules/@dimforge/rapier3d/rapier.js', import.meta.url)) } },
  test: { include: ['scripts/pole-qa.test.ts'], maxWorkers: 1, environment: 'node', testTimeout: 120000,
    server: { deps: { inline: [/rapier3d/] } } },
});
