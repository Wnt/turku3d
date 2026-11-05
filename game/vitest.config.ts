import { jobs } from '../tools/dev/environment.mjs';
import { defineConfig } from 'vitest/config';
import wasm from 'vite-plugin-wasm';
import { fileURLToPath } from 'node:url';
import { readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';

// Vitest's fsModuleCache keys a module by its source, not by the files an import.meta.glob expands to: a registry
// would keep serving the model set of its first transform after models are added. Key the fleet registry by its
// glob directory listing as well.
const globRegistries: Record<string, string> = { 'actors/fleet/registry.ts': 'models' };
const globKey = { name: 'glob-registry-key', configureVitest(ctx: { defineCacheKeyGenerator: (f: (c: { id: string }) => string | undefined) => void }) {
  ctx.defineCacheKeyGenerator(({ id }) => {
    const entry = Object.entries(globRegistries).find(([file]) => id.endsWith(`/src/${file}`));
    if (!entry) return undefined;
    const listing = readdirSync(path.join(path.dirname(id), entry[1])).sort().join('\n');
    return createHash('sha1').update(listing).digest('hex');
  });
} };

// @dimforge/rapier3d only declares "module"; point Node-side resolution at it explicitly and inline it so
// vite-plugin-wasm handles its .wasm import (same build the browser worker uses).
export default defineConfig({
  plugins: [wasm(), globKey],
  // CPU shader tests import Three internals; one source registry prevents numeric node-ID
  // collisions between those internals and the separately bundled WebGPU/TSL modules.
  resolve: { alias: {
    'three/webgpu': fileURLToPath(new URL('./node_modules/three/src/Three.WebGPU.js', import.meta.url)),
    'three/tsl': fileURLToPath(new URL('./node_modules/three/src/Three.TSL.js', import.meta.url)),
    '@dimforge/rapier3d': fileURLToPath(new URL('./node_modules/@dimforge/rapier3d/rapier.js', import.meta.url)),
  } },
  test: { maxWorkers: jobs, include: ['tests/unit/**/*.test.ts'], environment: 'node', testTimeout: 30000, server: { deps: { inline: [/rapier3d/, /three/] } } },
});
