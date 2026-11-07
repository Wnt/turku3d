// Long-session memory: tiles streamed in and out many times leave nothing alive. A model kept by a listener, a map
// keyed by tile or a preparation closure keeps its geometry arrays and textures for the whole session (city.lab M1 Max:
// 2 -> 7 GB in 33 min, then back-to-back 100-280 ms frames). Every model the service hands out is watched through a
// WeakRef; after the player leaves and V8 collects, none may survive.
import { test, expect } from 'vitest';
import v8 from 'node:v8';
import vm from 'node:vm';
import * as THREE from 'three/webgpu';
import { CoarseTiles, cellsOf, type CoarseHost } from '../../src/world/coarseTiles';
import { GraphicsPreparation } from '../../src/render/graphicsPreparation';
import type { Manifest } from '../../src/world/manifest';

v8.setFlagsFromString('--expose-gc');
const gc = vm.runInNewContext('gc') as () => void;
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
/** WeakRefs keep their target until the job that read them ends: collect in a later task, twice for finalizers. */
async function collect() { for (let i = 0; i < 3; i++) { await tick(); gc(); } }

function tileService() {
  const fragment = (id: string) => ({ coarse: true, tile: id, pass_kind: 'full', generation: 'g1',
    visual: { [id]: { url: 'vis.glb', sha256: `v-${id}`, bytes: 3, tris: 12, h: [0, 1] } },
    vegetation: { trees: [] },
    collision: Object.fromEntries(cellsOf(id).map(cid => [cid, { url: `col/${cid}.col`, sha256: cid, bytes: 4, tris: 2, h: [0, 1] }])) });
  return (async (url: string) => {
    const u = new URL(url);
    const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
    if (u.pathname.endsWith('.col')) return { ok: true, status: 200, arrayBuffer: async () => Uint8Array.of(1, 2, 3, 4).buffer } as unknown as Response;
    const m = /^\/coarse\/(t0_-?\d+_-?\d+)\/manifest\.json$/.exec(u.pathname);
    return json(m ? fragment(m[1]) : { state: 'idle' });
  }) as typeof globalThis.fetch;
}

test('tiles loaded and unloaded through five round trips leave no model alive', async () => {
  // The city's lifetime signal outlives every tile, as in main.ts cityWorld: anything a job hangs on it stays.
  const city = new AbortController();
  const scene = new THREE.Scene(), manifest = { visual: {}, collision: {} } as unknown as Manifest;
  const graphics = new GraphicsPreparation(scene, () => {}, release => release(), () => Promise.resolve());
  const made: WeakRef<THREE.Object3D>[] = [];
  const host: CoarseHost = {
    scene, manifest, pageUrl: 'http://127.0.0.1:5171/',
    renderer: { library: { fromMaterial: () => new THREE.MeshStandardNodeMaterial() } } as unknown as THREE.WebGPURenderer,
    unloadCollision: () => {}, refreshCollision: () => {}, checkCollision: async () => 2, fetch: tileService(),
    prepareVegetation: async model => { await graphics.enqueue(model, () => {}, city.signal); },
    preparePreview: model => graphics.enqueue(model, () => {}, city.signal),
    loadModel: async () => {
      // 1 MiB of vertex data per model: what a real tile's arrays would keep alive.
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(1 << 18), 3));
      const model = new THREE.Group();
      model.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial()));
      made.push(new WeakRef(model));
      return model;
    },
  };
  const coarse = new CoarseTiles(host);
  let now = 0;
  const stay = async (x: number, z: number, seconds: number) => {
    for (let t = 0; t < seconds; t++) { coarse.update(x, z, now += 1000); for (let i = 0; i < 8; i++) await tick(); }
  };
  // Two places 2.5 km apart: each visit loads its tiles, each departure drops them.
  for (let round = 0; round < 5; round++) { await stay(125, 125, 8); await stay(2625, 125, 8); }
  const resident = () => [...coarse.entries.values()].filter(e => e.model).length;
  expect(made.length).toBeGreaterThan(40);
  expect(resident()).toBeGreaterThan(0);
  // Away from both, past every hold and the 60 s return cache (RETURN_CACHE_MS): nothing of either place may stay.
  const fromBothPlaces = made.length;
  await stay(20125, 20125, 70);
  await collect();
  expect(made.slice(0, fromBothPlaces).filter(ref => ref.deref()).length).toBe(0);
  city.abort();
});
