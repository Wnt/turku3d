import { afterAll, beforeAll, expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { BuildingModelCache, updateBuildingDistances, type BuildingPlacements } from '../../src/world/buildingPlacements';
import { disposeModelResources } from '../../src/world/disposeModel';
import { SharedMaterials } from '../../src/render/sharedMaterials';
import { CoarseTiles, cellsOf } from '../../src/world/coarseTiles';
import type { Manifest } from '../../src/world/manifest';
import { PreparationBudget } from '../../src/render/preparationBudget';
import { LOD_FADE_SECONDS, LodFades, dissolveReady, dissolveTwin, fadeTier, isDissolveTwin, precompileDissolve } from '../../src/render/lodFade';

beforeAll(() => vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => setTimeout(() => callback(performance.now()), 0)));
afterAll(() => vi.unstubAllGlobals());

const id = 'a'.repeat(64), base = new URL('http://localhost/coarse/t0_0_0/');
const data = (): BuildingPlacements => ({ version: 1,
  models: [{ id, uri: `../shared/${id}.glb`, bounds: { min: [-10, 0, -20], max: [10, 30, 20] },
    occluders: { version: 1, provenance: 'lod2-inset-boxes-v1', boxes: [] } }],
  placements: [{ id: 'building', model: id, position: [250, 0, 120], viewDistance: 300 }] });

function prototype() {
  const root = new THREE.Group(), geometry = new THREE.BoxGeometry();
  geometry.setAttribute('_kit_test', new THREE.Float32BufferAttribute(new Float32Array(geometry.attributes.position.count), 1));
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ map: new THREE.Texture() }));
  mesh.userData = { layer: 'buildings', building_id: 'building' }; root.add(mesh);
  return { root, mesh };
}

test('content models decode once across generation URLs and survive either placement retiring', async () => {
  const { root, mesh } = prototype(), load = vi.fn(async () => root), shared = new SharedMaterials();
  const cache = new BuildingModelCache(load, root => disposeModelResources(root, shared));
  const first = new THREE.Group(), second = new THREE.Group();
  const [releaseFirst, releaseSecond] = await Promise.all([
    cache.attach(first, data(), base), cache.attach(second, data(), new URL('http://localhost/coarse/_v/new/t0_1_0/')),
  ]);
  expect(load).toHaveBeenCalledOnce();
  const a = first.children[0].children[0].children[0] as THREE.Mesh;
  const b = second.children[0].children[0].children[0] as THREE.Mesh;
  expect(a.geometry).toBe(b.geometry); expect(a.geometry).toBe(mesh.geometry);
  expect(a.material).not.toBe(b.material);
  expect(a.userData).toEqual(mesh.userData);
  expect(first.children[0].userData.occluders).toEqual(data().models[0].occluders);
  const disposed = vi.fn(); mesh.geometry.addEventListener('dispose', disposed);
  disposeModelResources(first, shared); releaseFirst(); await Promise.resolve();
  expect(disposed).not.toHaveBeenCalled();
  disposeModelResources(second, shared); releaseSecond(); await Promise.resolve();
  expect(disposed).toHaveBeenCalledOnce();
  releaseSecond(); expect(disposed).toHaveBeenCalledOnce();
});

test('placement culling uses model extents and preserves seam-crossing geometry', async () => {
  const shared = new SharedMaterials(), cache = new BuildingModelCache(async () => prototype().root, root => disposeModelResources(root, shared));
  const root = new THREE.Group(), release = await cache.attach(root, data(), base);
  updateBuildingDistances(root, 560, 120); expect(root.children[0].visible).toBe(true);
  updateBuildingDistances(root, 561, 120); expect(root.children[0].visible).toBe(false);
  updateBuildingDistances(root, 245, 120); expect(root.children[0].visible).toBe(true);
  root.updateMatrixWorld(true);
  expect(new THREE.Vector3().setFromMatrixPosition(root.children[0].matrixWorld).toArray()).toEqual([250, 0, 120]);
  disposeModelResources(root, shared); release();
});

test('failed model loads release successful siblings and can retry; malformed placements do not load', async () => {
  const shared = new SharedMaterials(), release = vi.fn(root => disposeModelResources(root, shared));
  let fail = true;
  const cache = new BuildingModelCache(async url => {
    if (url.includes('b'.repeat(64)) && fail) throw new Error('missing model');
    return prototype().root;
  }, release);
  const input = data(); input.models.push({ ...input.models[0], id: 'b'.repeat(64), uri: `../shared/${'b'.repeat(64)}.glb` });
  input.placements.push({ ...input.placements[0], id: 'other', model: 'b'.repeat(64) });
  const root = new THREE.Group();
  await expect(cache.attach(root, input, base)).rejects.toThrow('missing model');
  expect(root.children).toHaveLength(0); expect(release).toHaveBeenCalledOnce();
  fail = false; const done = await cache.attach(root, input, base);
  expect(root.children).toHaveLength(2); disposeModelResources(root, shared); done();
  input.placements[0].position[0] = NaN;
  await expect(cache.attach(new THREE.Group(), input, base)).rejects.toThrow('Invalid building placement');
});

test('a building-only revision swaps even when terrain and collision hashes do not change', async () => {
  const tile = 't0_0_0', scene = new THREE.Scene();
  const fragment = { coarse: true as const, tile, pass_kind: 'full' as const, buildingPlacements: data(),
    visual: { [tile]: { url: 'vis.glb', sha256: 'terrain' } },
    collision: Object.fromEntries(cellsOf(tile).map(cid => [cid, { url: `${cid}.col`, sha256: cid, bytes: 4 }])) };
  const checked = vi.fn(async () => {}), loaded: string[] = [];
  const coarse = new CoarseTiles({ scene, renderer: {} as THREE.WebGPURenderer,
    manifest: { collision: {} } as Manifest, pageUrl: 'http://localhost/',
    checkCollision: checked, refreshCollision: vi.fn(), unloadCollision: vi.fn(),
    loadModel: async url => { loaded.push(url); return new THREE.Group(); },
    fetch: (async url => String(url).endsWith('.col') ? new Response(new Uint8Array(4)) : Response.json(String(url).includes('/want') ? {} : fragment)) as typeof fetch });
  coarse.preload([{ x: 20, z: 20 }], 0);
  const settle = async () => { for (let i = 0; i < 15; i++) await new Promise(resolve => setTimeout(resolve, 0)); };
  coarse.update(20, 20, 0); await settle();
  const first = coarse.entries.get(tile)!.model!;
  expect(first.children.some(c => c.userData.archetype === id)).toBe(true);
  fragment.buildingPlacements.models[0].id = 'b'.repeat(64);
  fragment.buildingPlacements.models[0].uri = `../shared/${'b'.repeat(64)}.glb`;
  fragment.buildingPlacements.placements[0].model = 'b'.repeat(64);
  coarse.update(20, 20, 100000); await settle();
  expect(coarse.entries.get(tile)!.model).not.toBe(first);
  expect(first.parent).toBeNull(); expect(checked).toHaveBeenCalledTimes(16);
  expect(loaded.filter(url => url.includes('/shared/'))).toHaveLength(2);
});


test('placement cloning yields between atomic models without changing transforms, leases or publication', async () => {
  const input = data();
  input.placements = Array.from({ length: 8 }, (_, i) => ({ ...input.placements[0], id: `building-${i}`, position: [250 + i, 0, 120] }));
  const make = (paced: boolean) => {
    const { root, mesh } = prototype(), shared = new SharedMaterials();
    let now = 0, clones = 0, frame!: () => void;
    const copy = root.clone.bind(root);
    root.clone = recursive => { clones++; now += paced ? 3 : 0; return copy(recursive); };
    const load = vi.fn(async () => root);
    const budget = new PreparationBudget(() => now, () => new Promise(resolve => { frame = resolve; }));
    const cache = new BuildingModelCache(load, model => disposeModelResources(model, shared), budget);
    const owner = new THREE.Group(); let published = false;
    const done = cache.attach(owner, input, base).then(release => { published = true; return release; });
    return { owner, mesh, done, load, shared, get clones() { return clones; }, get published() { return published; }, advance: () => { now += 16; frame(); } };
  };
  const baseline = make(false), paced = make(true);
  const flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };
  await flush();
  expect(baseline.clones).toBe(8); expect(baseline.published).toBe(true);
  expect(paced.clones).toBe(1); expect(paced.published).toBe(false);
  for (let i = 1; i < 8; i++) {
    expect(paced.owner.parent).toBeNull();
    paced.advance(); await flush(); expect(paced.clones).toBe(i + 1);
  }
  const [releaseBaseline, releasePaced] = await Promise.all([baseline.done, paced.done]);
  expect(paced.load).toHaveBeenCalledOnce(); expect(paced.published).toBe(true);
  expect(paced.owner.children.map(c => [c.name, c.position.toArray(), c.userData])).toEqual(
    baseline.owner.children.map(c => [c.name, c.position.toArray(), c.userData]));
  for (const state of [baseline, paced]) {
    const disposed = vi.spyOn(state.mesh.geometry, 'dispose');
    disposeModelResources(state.owner, state.shared); expect(disposed).not.toHaveBeenCalled();
    (state === baseline ? releaseBaseline : releasePaced)(); await Promise.resolve();
    expect(disposed).toHaveBeenCalledOnce();
  }
});


test('simultaneous owners recheck their shared budget before cloning another placement', async () => {
  const input = data(); input.placements = Array.from({ length: 4 }, (_, i) => ({ ...input.placements[0], id: `building-${i}` }));
  const { root, mesh } = prototype(), shared = new SharedMaterials();
  let now = 0, clones = 0, frame!: () => void;
  const copy = root.clone.bind(root);
  root.clone = recursive => { clones++; now += 3; return copy(recursive); };
  const load = vi.fn(async () => root);
  const budget = new PreparationBudget(() => now, () => new Promise(resolve => { frame = resolve; }));
  const cache = new BuildingModelCache(load, model => disposeModelResources(model, shared), budget);
  const owners = [new THREE.Group(), new THREE.Group()];
  const attachments = owners.map(owner => cache.attach(owner, input, base));
  const flush = async () => { for (let i = 0; i < 24; i++) await Promise.resolve(); };
  await flush(); expect(clones).toBe(1);
  for (let i = 1; i < 8; i++) {
    now += 16; frame(); await flush(); expect(clones).toBe(i + 1);
  }
  const releases = await Promise.all(attachments);
  expect(load).toHaveBeenCalledOnce();
  expect(owners.map(owner => owner.children.length)).toEqual([4, 4]);
  const disposed = vi.spyOn(mesh.geometry, 'dispose');
  for (const [i, owner] of owners.entries()) { disposeModelResources(owner, shared); releases[i](); await Promise.resolve(); }
  expect(disposed).toHaveBeenCalledOnce();
});

test('production preparation and retirement preserve shared packed archetype geometry across owners', async () => {
  const { VisualTiles } = await import('../../src/world/tiles');
  const { interleaveProgram } = await import('../../src/render/facadeProgramAttributes');
  const { root, mesh } = prototype(); mesh.geometry.clearGroups();
  mesh.material = new THREE.MeshStandardMaterial();
  const count = mesh.geometry.attributes.position.count;
  mesh.geometry.setAttribute('_kit_opening', new THREE.Float32BufferAttribute(new Float32Array(count * 4).fill(.25), 4));
  mesh.geometry.setAttribute('_kit_wall', new THREE.Float32BufferAttribute(new Float32Array(count * 3).fill(.75), 3));
  interleaveProgram(mesh.geometry);
  const attribute = mesh.geometry.getAttribute('_kit_opening') as THREE.InterleavedBufferAttribute;
  const buffer = attribute.data, bytes = buffer.array.slice(), disposed = vi.fn();
  mesh.geometry.addEventListener('dispose', disposed);
  const shared = new SharedMaterials(), load = vi.fn(async () => root);
  const cache = new BuildingModelCache(load, model => disposeModelResources(model, shared));
  const input = data();
  input.placements.push({ ...input.placements[0], id: 'second-building', position: [270, 0, 120] });
  const first = new THREE.Group(), second = new THREE.Group();
  const releaseFirst = await cache.attach(first, input, base), releaseSecond = await cache.attach(second, input, base);
  const tiles = new VisualTiles();
  await tiles.prepareModelAsync(first);
  expect(first.getObjectByName('static-placement-batch')).toBeDefined();
  expect(first.getObjectByName('tile-shadow-batch')).toBeDefined();
  expect(mesh.geometry.getAttribute('_kit_opening')).toBe(attribute);
  expect(attribute.data).toBe(buffer); expect(buffer.array).toEqual(bytes);
  updateBuildingDistances(first, 2000, 2000);
  expect(first.getObjectByName('static-placement-batch')!.visible).toBe(false);
  updateBuildingDistances(first, 260, 120);
  expect(first.getObjectByName('static-placement-batch')!.visible).toBe(true);
  tiles.releasePreparedModel(first); disposeModelResources(first, shared); releaseFirst(); await Promise.resolve();
  expect(disposed).not.toHaveBeenCalled();
  const remaining = second.children[0].children[0].children[0] as THREE.Mesh;
  expect(remaining.geometry).toBe(mesh.geometry);
  expect(remaining.geometry.getAttribute('_kit_opening')).toBe(attribute);
  expect(buffer.array).toEqual(bytes);
  await tiles.prepareModelAsync(second);
  expect(second.getObjectByName('static-placement-batch')).toBeDefined();
  tiles.releasePreparedModel(second); disposeModelResources(second, shared); releaseSecond(); await Promise.resolve();
  expect(load).toHaveBeenCalledOnce(); expect(disposed).toHaveBeenCalledOnce();
});

test('blocks swap all their HD children against one LOD parent, and an all-far tile shows one merged parent', async () => {
  const lodId = 'b'.repeat(64);
  const lodRoot = () => {
    const root = new THREE.Group(), geometry = new THREE.BoxGeometry(20, 10, 20); geometry.clearGroups();
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial()); mesh.userData = { layer: 'building_lod', lod: 'walls-glazed' };
    root.add(mesh); return root;
  };
  const roots: Record<string, THREE.Object3D> = { [id]: prototype().root, [lodId]: lodRoot() };
  const load = vi.fn(async (url: string) => roots[url.includes(lodId) ? lodId : id]), shared = new SharedMaterials();
  const styled = vi.fn((root: THREE.Object3D) => root.traverse(o => { if ((o as THREE.Mesh).isMesh) (o as THREE.Mesh).material = new THREE.MeshBasicMaterial(); }));
  const cache = new BuildingModelCache(load, root => disposeModelResources(root, shared), undefined, undefined, styled);
  const input = data();
  input.models.push({ id: lodId, uri: `../shared/${lodId}.glb`, bounds: { min: [-30, 0, -20], max: [30, 30, 20] } });
  input.placements[0].block = 'b1';
  input.placements.push({ ...input.placements[0], id: 'neighbour', position: [270, 0, 120] });
  input.blocks = [{ id: 'b1', position: [260, 0, 120], bounds: { min: [240, 0, 100], max: [280, 30, 140] }, hdDistance: 280,
    children: ['building', 'neighbour'], lod: lodId }];
  const owner = new THREE.Group(), release = await cache.attach(owner, input, base);
  expect(styled).toHaveBeenCalledOnce();
  const lod = owner.getObjectByName('block-lod:b1')!, merged = owner.getObjectByName('block-lod-tile')!;
  const houses = owner.children.filter(o => o.userData.archetype);
  updateBuildingDistances(owner, 260, 300); // 160 m: HD
  expect(houses.every(h => h.visible)).toBe(true); expect(lod.visible).toBe(false); expect(merged.visible).toBe(false);
  updateBuildingDistances(owner, 260, 425); // 285 m: inside the hysteresis band, still HD
  expect(houses.every(h => h.visible)).toBe(true);
  houses[0].userData.viewDistance = 250; // A child's own shorter view distance never opens a hole before the swap.
  updateBuildingDistances(owner, 260, 425);
  expect(houses.every(h => h.visible)).toBe(true);
  updateBuildingDistances(owner, 260, 440); // 300 m: the only block is far, so the merged tile parent replaces it
  expect(houses.some(h => h.visible)).toBe(false); expect(lod.visible).toBe(false); expect(merged.visible).toBe(true);
  updateBuildingDistances(owner, 260, 425); // 285 m coming back: stays LOD until 270 m
  expect(merged.visible).toBe(true);
  updateBuildingDistances(owner, 260, 405);
  expect(houses.every(h => h.visible)).toBe(true); expect(merged.visible).toBe(false);
  const tri = (merged as THREE.Mesh).geometry.index!.count / 3;
  expect(tri).toBe(12);
  release(); disposeModelResources(owner, shared);
});

test('a block LOD shell dissolves in over its HD children and out again, never popping either way', async () => {
  const lodId = 'b'.repeat(64);
  const lodRoot = () => {
    const root = new THREE.Group(), geometry = new THREE.BoxGeometry(20, 10, 20); geometry.clearGroups();
    root.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial())); return root;
  };
  const roots: Record<string, THREE.Object3D> = { [id]: prototype().root, [lodId]: lodRoot() };
  const load = vi.fn(async (url: string) => roots[url.includes(lodId) ? lodId : id]), shared = new SharedMaterials();
  const shell = new THREE.MeshStandardNodeMaterial();
  const styled = (root: THREE.Object3D) => root.traverse(o => { if ((o as THREE.Mesh).isMesh) (o as THREE.Mesh).material = shell; });
  const cache = new BuildingModelCache(load, root => disposeModelResources(root, shared), undefined, undefined, styled);
  const input = data();
  input.models.push({ id: lodId, uri: `../shared/${lodId}.glb`, bounds: { min: [-30, 0, -20], max: [30, 30, 20] } });
  input.placements[0].block = 'b1';
  input.blocks = [{ id: 'b1', position: [260, 0, 120], bounds: { min: [240, 0, 100], max: [280, 30, 140] }, hdDistance: 280,
    children: ['building'], lod: lodId }];
  const owner = new THREE.Group(), release = await cache.attach(owner, input, base);
  new THREE.Scene().add(owner);
  const fades = new LodFades(), slots = { setActive: () => {} } as unknown as Parameters<typeof updateBuildingDistances>[3];
  const lod = owner.getObjectByName('block-lod:b1')!, merged = owner.getObjectByName('block-lod-tile')!;
  const shellMesh = lod.getObjectByProperty('isMesh', true) as THREE.Mesh;
  const house = owner.children.find(o => o.userData.archetype)!;
  const at = (z: number) => updateBuildingDistances(owner, 260, z, slots, fades);
  // Without a compiled twin the swap is immediate: a dissolve never builds its shader mid-frame.
  const bare = new THREE.Group(); bare.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardNodeMaterial()));
  expect(fades.start(bare, 0, 1)).toBe(false); expect(fades.size).toBe(0);
  await precompileDissolve([lod], [0], () => {}); // the tile's preparation path (main.ts prepareGraphics)
  at(300); // 160 m: settles on HD without a dissolve
  expect([house.visible, lod.visible, merged.visible, fades.size]).toEqual([true, false, false, 0]);
  at(440); // 300 m: the shell dissolves in on top while HD keeps drawing
  expect([house.visible, lod.visible, merged.visible]).toEqual([true, true, false]);
  expect(isDissolveTwin(shellMesh.material as THREE.Material)).toBe(true);
  expect(shellMesh.userData.lodCoverage).toBe(0);
  const start = performance.now();
  fades.update(start + LOD_FADE_SECONDS * 500);
  expect(shellMesh.userData.lodCoverage).toBeGreaterThan(.3);
  at(440);
  expect(house.visible).toBe(true);
  fades.update(start + LOD_FADE_SECONDS * 1000 + 50);
  expect(shellMesh.material).toBe(shell);
  at(440); // covered: HD hides and the all-far tile shows its merged parent
  expect([house.visible, lod.visible, merged.visible]).toEqual([false, false, true]);
  at(400); // 260 m: HD returns at once and the shell dissolves away on top of it
  expect([house.visible, lod.visible, merged.visible]).toEqual([true, true, false]);
  expect(shellMesh.userData.lodCoverage).toBe(1);
  fades.update(performance.now() + LOD_FADE_SECONDS * 1000 + 50);
  at(400);
  expect([house.visible, lod.visible, merged.visible, fades.size]).toEqual([true, false, false, 0]);
  at(440); fades.cancelWithin(owner); // a tile disposed mid-dissolve gets its own materials back
  expect(shellMesh.material).toBe(shell); expect(fades.size).toBe(0);
  release(); disposeModelResources(owner, shared);
});

test('dissolve twins keep their base program, add a dithered mask and a depth bias, and die with the base', () => {
  const base = new THREE.MeshStandardNodeMaterial({ name: 'distant-city-buildings' });
  const twin = dissolveTwin(base) as THREE.MeshStandardNodeMaterial;
  expect(dissolveTwin(base)).toBe(twin);
  expect([twin.name, twin.polygonOffset, twin.polygonOffsetUnits < 0, !!twin.maskNode, !!twin.maskShadowNode])
    .toEqual(['distant-city-buildings:dissolve', true, true, true, true]);
  expect(twin.positionNode).toBeNull(); // never the deformed velocity path (render/rigidVelocity.ts)
  // The bias shrinks with distance (one unit is ~0.6e-6 d^2 m), so a 4 km node is not pulled hundreds of metres.
  expect([fadeTier(300), fadeTier(1000), fadeTier(4000)]).toEqual([0, 1, 2]);
  const far = dissolveTwin(base, 2) as THREE.MeshStandardNodeMaterial;
  expect(far).not.toBe(twin); expect(dissolveTwin(base, 2)).toBe(far);
  expect([far.polygonOffsetUnits, isDissolveTwin(far)]).toEqual([-2, true]);
  const farDisposed = vi.fn(); far.addEventListener('dispose', farDisposed);
  const disposed = vi.fn(); twin.addEventListener('dispose', disposed);
  base.dispose();
  expect(disposed).toHaveBeenCalledOnce(); expect(farDisposed).toHaveBeenCalledOnce();
  expect(dissolveTwin(new THREE.MeshBasicMaterial()).name).not.toContain('dissolve');
});

test('block swap distance is 3D: a camera high above a near block sees its shell', async () => {
  const lodId = 'b'.repeat(64);
  const roots: Record<string, THREE.Object3D> = { [id]: prototype().root,
    [lodId]: (() => { const r = new THREE.Group(); r.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardNodeMaterial())); return r; })() };
  const cache = new BuildingModelCache(async (url: string) => roots[url.includes(lodId) ? lodId : id], () => {});
  const input = data();
  input.models.push({ id: lodId, uri: `../shared/${lodId}.glb`, bounds: { min: [-30, 0, -20], max: [30, 30, 20] } });
  input.placements[0].block = 'b1';
  input.blocks = [{ id: 'b1', position: [260, 0, 120], bounds: { min: [240, 0, 100], max: [280, 30, 140] }, hdDistance: 280,
    children: ['building'], lod: lodId }];
  const owner = new THREE.Group(); await cache.attach(owner, input, base);
  const fades = new LodFades(), slots = { setActive: () => {} } as unknown as Parameters<typeof updateBuildingDistances>[3];
  const house = owner.children.find(o => o.userData.archetype)!;
  updateBuildingDistances(owner, 260, 300, slots, fades, 10); // 160 m away on the ground: HD
  expect(house.visible).toBe(true);
  const lone = new THREE.Group(); await cache.attach(lone, input, base);
  updateBuildingDistances(lone, 260, 300, slots, fades, 400); // same spot, 370 m above the roofs: shell
  expect(lone.children.find(o => o.userData.archetype)!.visible).toBe(false);
  updateBuildingDistances(lone, 260, 300, slots, fades); // unknown height keeps the horizontal rule: 160 m is HD
  expect(lone.children.find(o => o.userData.archetype)!.visible).toBe(true);
});

test('every dissolve twin shares one mask, and preparation compiles twins on kept stand-ins, never touching the live model', async () => {
  const a = new THREE.MeshStandardNodeMaterial({ name: 'a' }), b = new THREE.MeshStandardNodeMaterial({ name: 'b' });
  const ta = dissolveTwin(a) as THREE.MeshStandardNodeMaterial, tb = dissolveTwin(b, 1) as THREE.MeshStandardNodeMaterial;
  expect(ta.maskNode).toBe(tb.maskNode); expect(ta.maskShadowNode).toBe(tb.maskShadowNode);
  const root = new THREE.Group(), mesh = new THREE.Mesh(new THREE.BoxGeometry(), a); root.add(mesh);
  const seen: THREE.Material[] = [], live: THREE.Material[] = [];
  await precompileDissolve([root], [0, 2], async r => {
    const proxy = r.children[0] as THREE.Mesh;
    seen.push(proxy.material as THREE.Material); live.push(mesh.material as THREE.Material);
    // A detached keeper with a three-vertex stand-in of the mesh's attribute layout, not the tile's geometry.
    expect(proxy).not.toBe(mesh); expect(proxy.geometry).not.toBe(mesh.geometry);
    expect(Object.keys(proxy.geometry.attributes).sort()).toEqual(Object.keys(mesh.geometry.attributes).sort());
    expect(proxy.geometry.attributes.position.count).toBe(3);
    expect(proxy.geometry.attributes.position.array).toBeInstanceOf(Float32Array);
    expect(proxy.geometry.index?.array).toBeInstanceOf(mesh.geometry.index!.array.constructor as never);
  });
  expect(seen.map(m => m.name)).toEqual(['a:dissolve', 'a:dissolve-2']);
  expect(live).toEqual([a, a]); expect(mesh.material).toBe(a);
  expect([dissolveReady(a, 0), dissolveReady(a, 1), dissolveReady(a, 2)]).toEqual([true, false, true]);
  const calls = vi.fn();
  await precompileDissolve([root], [0], calls); // already compiled: nothing to do
  expect(calls).not.toHaveBeenCalled();
  const fades = new LodFades();
  mesh.castShadow = true;
  expect(fades.start(root, 0, 1, undefined, 0, 1, 1)).toBe(false); // tier 1 was never compiled
  expect(fades.start(root, 0, 1, undefined, 0, 1, 2)).toBe(true);
  expect([(mesh.material as THREE.Material).name, mesh.castShadow]).toEqual(['a:dissolve-2', false]);
  fades.update(2000);
  expect([mesh.material, mesh.castShadow]).toEqual([a, true]);
});

test('model reads start before the tile GLB, nearest tile first, and a prefetch lease alone keeps nothing resident', async () => {
  const models = (prefix: string, count: number): BuildingPlacements => {
    const ids = Array.from({ length: count }, (_, k) => `${prefix}${k}`.padEnd(64, '0'));
    return { version: 1, models: ids.map(model => ({ id: model, uri: `../shared/${model}.glb`, bounds: { min: [-1, 0, -1], max: [1, 1, 1] } })),
      placements: ids.map((model, k) => ({ id: `p${k}`, model, position: [10 * k, 0, 0], viewDistance: 300 })) };
  };
  const started: string[] = [], finish: (() => void)[] = [], shared = new SharedMaterials();
  const disposed = vi.fn();
  const cache = new BuildingModelCache(url => { started.push(url.slice(url.lastIndexOf('/') + 1, url.lastIndexOf('/') + 2));
    return new Promise(resolve => finish.push(() => resolve(prototype().root))); }, root => { disposed(); disposeModelResources(root, shared); });
  const far = cache.prefetch(models('f', 8), base, 400);
  expect(started).toHaveLength(cache.reads); // reads begin at once, before any attach
  const nearOwner = new THREE.Group(), near = cache.attach(nearOwner, models('e', 2), base, 20);
  finish.shift()!(); await new Promise(resolve => setTimeout(resolve, 0));
  expect(started.at(-1)).toBe('e'); // the nearer tile's read goes ahead of the far prefetch backlog
  while (finish.length) { finish.shift()!(); await new Promise(resolve => setTimeout(resolve, 0)); }
  const release = await near;
  far(); await new Promise(resolve => setTimeout(resolve, 0));
  expect(disposed).toHaveBeenCalledTimes(8); // released prefetch leases free their models
  release(); await new Promise(resolve => setTimeout(resolve, 0));
  expect(disposed).toHaveBeenCalledTimes(10);
});

test('compiled twins stay compiled until their base material goes: keepers are kept, one per layout, then disposed', async () => {
  const base = new THREE.MeshStandardNodeMaterial({ name: 'kept' });
  const root = new THREE.Group();
  // Three meshes, two layouts: the two boxes share one keeper, the plane (no normals) gets its own.
  const plane = new THREE.BufferGeometry();
  plane.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(9), 3));
  root.add(new THREE.Mesh(new THREE.BoxGeometry(), base), new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2), base), new THREE.Mesh(plane, base));
  // A shadow-only batch and a non-node material never dissolve, so they need no keeper.
  const shadow = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardNodeMaterial()); shadow.userData.shadowBatch = true;
  root.add(shadow, new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()));
  const compiled: THREE.Object3D[] = [];
  await precompileDissolve([root], [0], r => { compiled.push(...r.children); });
  expect(compiled).toHaveLength(2);
  const disposed = vi.fn();
  for (const keeper of compiled) keeper.addEventListener('dispose', disposed);
  const fades = new LodFades();
  expect(fades.canStart(root, 0)).toBe(true);
  expect(fades.start(root, 0, 1, undefined, 0, 1, 0)).toBe(true);
  expect((shadow.material as THREE.Material).name).not.toContain('dissolve');
  fades.cancel(root);
  // Recompiling finds every layout kept; disposing the base releases its keepers, and the twins need compiling again.
  const again = vi.fn(); await precompileDissolve([root], [0], again); expect(again).not.toHaveBeenCalled();
  base.dispose();
  expect(disposed).toHaveBeenCalledTimes(2);
  expect(fades.canStart(root, 0)).toBe(false);
});
