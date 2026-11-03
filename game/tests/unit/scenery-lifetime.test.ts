// Node simulation of a long scenery drive: cell models, their geometries and merged batches must stay bounded and
// every released cell's geometry must be disposed (no closure or cache keeping decoded cells alive).
import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { ScreenshotScenery } from '../../src/world/screenshotScenery';
import { lodFades, precompileDissolve } from '../../src/render/lodFade';
import { SharedMaterials } from '../../src/render/sharedMaterials';
import type { CoarseHost, Entry } from '../../src/world/coarseContracts';

const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

test('a 12 km scenery drive keeps cells, geometries and merged batches bounded and disposes every released cell', async () => {
  const scene = new THREE.Scene(), nearby = new Map<string, Entry>();
  const live = new Set<THREE.BufferGeometry>();
  let created = 0, disposed = 0;
  const geometry = () => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(3 * 300), 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(3 * 300), 3));
    created++; live.add(g);
    g.addEventListener('dispose', () => { if (live.delete(g)) disposed++; });
    return g;
  };
  const host: CoarseHost = { scene, renderer: { domElement: { height: 720 } } as unknown as THREE.WebGPURenderer, manifest: { collision: {} } as never,
    checkCollision: vi.fn(), unloadCollision: vi.fn(), refreshCollision: vi.fn(), trackModel: vi.fn(),
    retire: release => release(),
    // As main.ts prepareSceneryGraphics: each cell's preparation compiles its dissolve tiers (batches prepare too).
    prepareGraphics: async model => { const tiers = model.userData.dissolveTiers as number[] | undefined;
      if (tiers) await precompileDissolve([model], tiers, () => {}); } };
  const key = (tile: string) => tile.replace(/[^0-9a-z]/g, 'x').padEnd(64, '0').slice(0, 64);
  const request = vi.fn(async (url: string) => {
    if (url.includes('/scenery?')) {
      const tiles = new URL(url, 'http://localhost/').searchParams.get('tiles')!.split(',');
      return new Response(JSON.stringify({ tiles: tiles.map(tile => ({ tile, key: key(tile), h: [0, 20], source_sha256: tile })), missing: [] }));
    }
    const k = url.split('/').pop()!.replace('.json', '');
    const tile = [...url.matchAll(/scenery\/([0-9a-z]+)\.json/g)][0] ? k : k;
    return new Response(JSON.stringify({ coarse: true, tile: lookup.get(tile), pass_kind: 'full', visual: { [lookup.get(tile)!]: { url: 'scene.glb', sha256: tile } } }));
  });
  const lookup = new Map<string, string>();
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (url: string) => {
    if (url.includes('/scenery?')) for (const tile of new URL(url, 'http://localhost/').searchParams.get('tiles')!.split(',')) lookup.set(key(tile), tile);
    return original(url);
  });
  // Pooled per-layer materials, as styleScenery shares them: cells of one 1 km cell merge into batches.
  const materials = { ground: new THREE.MeshStandardNodeMaterial(), buildings: new THREE.MeshStandardNodeMaterial() };
  const load = vi.fn(async () => {
    const model = new THREE.Group();
    for (const layer of ['ground', 'buildings'] as const) {
      const mesh = new THREE.Mesh(geometry(), materials[layer]); mesh.userData.layer = layer; model.add(mesh);
    }
    return model;
  });
  // Merged batch geometries: created by the batcher, disposed when their batch is replaced or released.
  const batchLive = new Set<THREE.BufferGeometry>();
  const add = scene.add.bind(scene);
  scene.add = ((...objects: THREE.Object3D[]) => {
    for (const object of objects) if (object.name === 'distant-city-cell') {
      const g = (object as THREE.Mesh).geometry; batchLive.add(g); g.addEventListener('dispose', () => batchLive.delete(g));
    }
    return add(...objects);
  }) as typeof scene.add;
  const dispose = vi.fn((model: THREE.Object3D) => {
    model.removeFromParent();
    model.traverse(o => { if ((o as THREE.Mesh).isMesh) (o as THREE.Mesh).geometry.dispose(); });
  });
  // The game's material pool (CoarseTiles passes it): styled scenery materials are shared across cells.
  const scenery = new ScreenshotScenery(host, nearby, request, load, () => {}, dispose, '/coarse/', 'http://localhost/', new SharedMaterials());
  // Every dissolve the cut asks for must start: a refusal is an instant swap (a pop), e.g. a batched cell's twins
  // not found because merging hid its meshes.
  let started = 0, refused = 0;
  const start = lodFades.start.bind(lodFades);
  const spy = vi.spyOn(lodFades, 'start').mockImplementation((...args: Parameters<typeof lodFades.start>) => {
    const ok = start(...args); if (ok) started++; else refused++; return ok;
  });
  const view = (x: number, z: number) => ({ x, y: 20, z, dx: 1, dz: 0, halfAngle: .8, focalPixels: 600 });
  scenery.setRadius(4000, view(0, 0));
  const samples: { km: number; entries: number; live: number; batches: number; batchGeometries: number }[] = [];
  const internal = scenery as unknown as { entries: Map<string, { model?: THREE.Object3D }> };
  let now = 1000, handedOver = false;
  // 12 km eastwards at 30 m/s, 10 Hz updates; the camera view follows.
  for (let step = 0; step <= 4000; step++) {
    const x = step * 3;
    now += 100;
    scenery.setView(view(x, 0), now);
    scenery.update(x, 0, now);
    lodFades.update(now);
    await settle();
    if (step % 400 === 0) samples.push({ km: x / 1000, entries: internal.entries.size, live: live.size,
      batches: scene.children.filter(c => c.name === 'distant-city-cell').length, batchGeometries: batchLive.size });
    if (step === 2000) {
      // A full tile arrives over a drawn cell that is merged into a batch (its meshes hidden by a cleared layer mask):
      // the cell must dissolve out over it, as in the game (round 5: 27 swaps 'twin not compiled').
      const batched = [...internal.entries].find(([id, e]) => /^t0_/.test(id) && e.model?.visible && !e.model.userData.lodFading
        && e.model.children.every(child => child.layers.mask === 0));
      expect(batched).toBeDefined();
      const [id, e] = batched!;
      nearby.set(id, { model: new THREE.Group(), state: 'draft', nextPoll: 0, collision: new Map() });
      scenery.nearbyChanged(id);
      handedOver = lodFades.has(e.model!);
    }
  }
  expect(handedOver).toBe(true);
  const peak = Math.max(...samples.map(s => s.entries)), last = samples.at(-1)!;
  process.stderr.write(JSON.stringify({ created, disposed, started, refused, samples }) + '\n');
  expect(started).toBeGreaterThan(100);
  expect(refused).toBe(0);
  spy.mockRestore();
  // Cells come and go along the drive; the resident set stays bounded and every released cell was disposed.
  expect(created).toBeGreaterThan(2 * peak);
  expect(last.entries).toBeLessThanOrEqual(peak);
  expect(live.size).toBeLessThanOrEqual(2 * peak + 8);
  for (const s of samples.slice(2)) expect(s.live).toBeLessThanOrEqual(2 * peak + 8);
  // Steady state (camera still, cut settled): allocation of the per-frame scenery update, summed over heap increases.
  for (let i = 0; i < 50; i++) { now += 16; scenery.update(12000, 0, now); lodFades.update(now); await settle(); }
  let allocated = 0, previous = process.memoryUsage().heapUsed;
  const frames = 400;
  for (let i = 0; i < frames; i++) {
    now += 16; scenery.setView(view(12000, 0), now); scenery.update(12000, 0, now); lodFades.update(now);
    const used = process.memoryUsage().heapUsed;
    if (used > previous) allocated += used - previous;
    previous = used;
  }
  process.stderr.write(JSON.stringify({ steadyBytesPerFrame: Math.round(allocated / frames) }) + '\n');
  expect(Math.max(...samples.map(s => s.batches))).toBeGreaterThan(0);
  // Every merged batch still alive is one drawn in the scene: replaced batches released their copies.
  for (const s of samples) expect(s.batchGeometries).toBe(s.batches);
  scenery.setRadius(0);
  // Loads in flight finish their (now awaited) preparation, then find their selection gone and dispose the model.
  for (let i = 0; i < 5; i++) await new Promise(resolve => setTimeout(resolve, 0));
  expect(live.size).toBe(0); expect(batchLive.size).toBe(0);
  expect(scene.children.filter(c => c.name === 'distant-city-cell')).toHaveLength(0);
}, 60000);
