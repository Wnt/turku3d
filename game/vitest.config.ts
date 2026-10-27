import { jobs } from '../tools/dev/environment.mjs';
import { defineConfig } from 'vitest/config';
import wasm from 'vite-plugin-wasm';
import { fileURLToPath } from 'node:url';

// @dimforge/rapier3d only declares "module"; point Node-side resolution at it explicitly and inline it so
// vite-plugin-wasm handles its .wasm import (same build the browser worker uses).
export default defineConfig({
  plugins: [wasm()],
  resolve: { alias: { '@dimforge/rapier3d': fileURLToPath(new URL('./node_modules/@dimforge/rapier3d/rapier.js', import.meta.url)) } },
  test: { maxWorkers: jobs, include: ['tests/unit/**/*.test.ts'], environment: 'node', testTimeout: 30000, server: { deps: { inline: [/rapier3d/] } } },
});
