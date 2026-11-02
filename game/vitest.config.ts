import { jobs } from '../tools/dev/environment.mjs';
import { defineConfig } from 'vitest/config';
import wasm from 'vite-plugin-wasm';
import { fileURLToPath } from 'node:url';

// @dimforge/rapier3d only declares "module"; point Node-side resolution at it explicitly and inline it so
// vite-plugin-wasm handles its .wasm import (same build the browser worker uses).
export default defineConfig({
  plugins: [wasm()],
  // CPU shader tests import Three internals; one source registry prevents numeric node-ID
  // collisions between those internals and the separately bundled WebGPU/TSL modules.
  resolve: { alias: {
    'three/webgpu': fileURLToPath(new URL('./node_modules/three/src/Three.WebGPU.js', import.meta.url)),
    'three/tsl': fileURLToPath(new URL('./node_modules/three/src/Three.TSL.js', import.meta.url)),
    '@dimforge/rapier3d': fileURLToPath(new URL('./node_modules/@dimforge/rapier3d/rapier.js', import.meta.url)),
  } },
  test: { maxWorkers: jobs, include: ['tests/unit/**/*.test.ts'], environment: 'node', testTimeout: 30000, server: { deps: { inline: [/rapier3d/, /three/] } } },
});
