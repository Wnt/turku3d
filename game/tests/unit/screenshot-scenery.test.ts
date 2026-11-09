import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { STANDBY_MARGIN, ScreenshotScenery, dissolveTiers } from '../../src/world/screenshotScenery';
import type { CoarseHost, Entry } from '../../src/world/coarseContracts';
import { PLACES } from '../../src/gameplay/content/places';
import { Environment } from '../../src/render/environment';
import { LOD_FADE_SECONDS, lodFades } from '../../src/render/lodFade';
import { styleScenery } from '../../src/render/sceneryMaterial';
import { TILE_DETAIL, sceneryBounds } from '../../src/world/sceneryView';

const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

test('incoming source scenery awaits shader preparation before attaching or claiming visual ownership', async () => {
  const { scene, host, scenery, request, dispose } = setup();
  let finish!: () => void;
  host.prepareGraphics = vi.fn(model => {
    expect(model.parent).toBeNull(); expect(model.userData.scenery).toBe(true);
    return new Promise<void>(resolve => { finish = resolve; });
  });
  request.mockImplementation(async url => url.includes('/scenery?')
    ? Response.json({ tiles: [{ tile: 't2_-2_1', h: [0, 107], key: 'source', source_sha256: 'base' }], missing: [] })
    : Response.json({ coarse: true, tile: 't2_-2_1', pass_kind: 'full', visual: { 't2_-2_1': { url: 'scene.glb', sha256: 'visual' } } }));
  scenery.setRadius(6000, { x: 3751, y: 164, z: 430, dx: -1, dz: 0, halfAngle: .8 });
  await settle(); scenery.update(3751, 430, 1000); await settle();
  expect(host.prepareGraphics).toHaveBeenCalledTimes(1);
  expect(scene.children).toHaveLength(0);
  expect(host.trackModel).not.toHaveBeenCalled();
  finish(); await settle();
  expect(scene.children).toHaveLength(1);
  expect(host.trackModel).toHaveBeenCalledTimes(1);
  expect(dispose).not.toHaveBeenCalled();
});
function setup() {
  const scene = new THREE.Scene(), nearby = new Map<string, Entry>();
  const host: CoarseHost = { scene, renderer: {} as THREE.WebGPURenderer, manifest: { collision: {} } as never,
    checkCollision: vi.fn(), unloadCollision: vi.fn(), refreshCollision: vi.fn(), trackModel: vi.fn() };
  const request = vi.fn(async (url: string) => {
    const id = url.split('/')[2];
    return new Response(JSON.stringify({ coarse: true, tile: id, pass_kind: 'full',
      visual: { [id]: { url: 'scene.glb', sha256: id } } }));
  });
  const load = vi.fn(async () => new THREE.Group());
  const dispose = vi.fn((model: THREE.Object3D) => model.removeFromParent());
  const scenery = new ScreenshotScenery(host, nearby, request, load, () => {}, dispose, '/coarse/', 'http://localhost/');
  return { scene, nearby, host, request, load, dispose, scenery };
}

test('kilometre scenery advances beyond one demand batch without adding physics and resets cleanly', async () => {
  const { scene, host, scenery, request, dispose } = setup();
  scenery.update(125, 125, 0); expect(request).not.toHaveBeenCalled();
  scenery.setRadius(1000);
  scenery.update(125, 125, 1000); await settle();
  expect(scenery.progress.total).toBeGreaterThan(64);
  expect(scenery.demand().length).toBeLessThanOrEqual(64);
  expect(scenery.progress.loaded).toBe(2);
  const first = request.mock.calls[0][0];
  for (let i = 2; i < 65; i++) { scenery.update(125, 125, i * 1000); await settle(); }
  expect(scenery.progress.loaded).toBe(scenery.progress.total);
  expect(request.mock.calls.filter(([url]) => url === first).length).toBeGreaterThan(1);
  expect(host.checkCollision).not.toHaveBeenCalled(); expect(host.manifest.collision).toEqual({});
  scenery.setRadius(0); expect(scene.children).toHaveLength(0); expect(dispose).toHaveBeenCalled();
  expect(scenery.demand()).toEqual([]);
});

test('normal residency takes over scenery; reset invalidates an in-flight model', async () => {
  const { scene, nearby, scenery, load, dispose } = setup();
  scenery.setRadius(1000); scenery.update(125, 125, 0); await settle();
  const first = scene.children[0];
  nearby.set('t0_0_0', { model: new THREE.Group(), state: 'draft', nextPoll: 0, collision: new Map() });
  scenery.update(125, 125, 1000); await settle();
  expect(first.parent).toBeNull();
  const releases: ((model: THREE.Group) => void)[] = [];
  load.mockImplementation(() => new Promise(resolve => { releases.push(resolve); }));
  scenery.setRadius(0); scenery.setRadius(1000); scenery.update(125, 125, 2000); await settle();
  scenery.setRadius(0);
  const late = new THREE.Group();
  for (const release of releases) release(late);
  await settle();
  expect(late.parent).toBeNull(); expect(dispose).toHaveBeenCalledWith(late, expect.any(String));
});

test('prepared scenery owns the first visual until full detail and preview returns when scenery is released', async () => {
  const { scene, nearby, scenery, load } = setup();
  load.mockImplementation(async () => {
    const model = new THREE.Group();
    for (const layer of ['ground', 'buildings', 'vegetation']) {
      const child = new THREE.Group(); child.userData.layer = layer; model.add(child);
    }
    return model;
  });
  scenery.setRadius(1000); scenery.update(125, 125, 0); await settle();
  const source = scene.children[0];
  const entry: Entry = { model: new THREE.Group(), preview: {coarse:true,tile:'t0_0_0',visual:{},collision:{}}, state: 'draft', nextPoll: 0, collision: new Map() };
  nearby.set('t0_0_0', entry);
  scenery.nearbyChanged('t0_0_0');
  expect(source.visible).toBe(true);
  expect(entry.model!.visible).toBe(false);
  expect(source.children.map(c => c.visible)).toEqual([true, true, true]);
  scenery.setRadius(0);
  expect(entry.model!.visible).toBe(true);
  scenery.setRadius(1000);scenery.update(125,125,500);await settle();
  const replacement=scene.children[0];
  delete entry.preview;
  entry.cachedAt=1000;entry.model!.visible=false;
  scenery.nearbyChanged('t0_0_0');expect(replacement.visible).toBe(true);
  entry.cachedAt=undefined;entry.model!.visible=true;
  scenery.nearbyChanged('t0_0_0');
  // The cell dissolves away on top of the detailed tile, outside its merged batch, and is kept meanwhile.
  expect(replacement.visible).toBe(true);
  expect(lodFades.has(replacement)).toBe(true);
  scenery.update(125, 125, 600); await settle();
  expect(replacement.parent).toBe(scene);
  lodFades.update(500 + LOD_FADE_SECONDS * 1000);
  expect(replacement.visible).toBe(false);
  expect(lodFades.has(replacement)).toBe(false);
  scenery.update(125, 125, 1200); await settle();
  expect(replacement.parent).toBeNull();
});

test('5 km from the supplied east-city viewpoint includes the cathedral and farther scenery', () => {
  const { scenery } = setup();
  scenery.setRadius(5000); scenery.update(3749.71, 436.7, 0, true);
  const cathedral = PLACES.find(p => p.id === 'tuomiokirkko')!;
  const id = `t0_${Math.floor((cathedral.E - 23459500) / 250)}_${Math.floor((6704500 - cathedral.N) / 250)}`;
  expect((scenery as unknown as { wanted: string[] }).wanted).toContain(id);
  expect(scenery.progress.total).toBeGreaterThan(400);
});

test('photo range extends clipping without changing weather fog', () => {
  const scene = new THREE.Scene();
  const renderer = new THREE.WebGPURenderer({ canvas: { width: 1, height: 1, style: {} } as HTMLCanvasElement });
  const env = new Environment(scene, renderer, 'medium');
  const camera = new THREE.PerspectiveCamera(60, 1, .1, 6000);
  env.setSun(180, 45, 13);
  const fog = (scene.fog as THREE.Fog).clone();
  env.setScreenshotDistance(camera, 5000);
  expect(camera.far).toBeGreaterThan(5000 * Math.sqrt(3));
  expect(env.sky.scale.x / 2).toBeGreaterThan(5000);
  expect((scene.fog as THREE.Fog).near).toBe(fog.near);
  expect((scene.fog as THREE.Fog).far).toBe(fog.far);
  env.setScreenshotDistance(camera, 0);
  expect(camera.far).toBe(6000); expect(env.sky.scale.x).toBe(4500);
  expect((scene.fog as THREE.Fog).near).toBe(fog.near); expect((scene.fog as THREE.Fog).far).toBe(fog.far);
  env.dispose();
});

test('large views use derivative sources without requesting source regeneration', async () => {
  const { scenery, request, host, load } = setup();
  request.mockImplementation(async (url: string) => {
    if (url.includes('/scenery?')) return new Response(JSON.stringify({ tiles: [{ tile: 't2_-2_1', h: [0, 107], key: 'a'.repeat(64), source_sha256: 'base' }], missing: [] }));
    return new Response(JSON.stringify({ coarse: true, scenery: true, tile: 't2_-2_1', pass_kind: 'full',
      visual: { 't2_-2_1': { url: '../scenery/lod.test.glb', sha256: 'derived' } } }));
  });
  const view = { x: 3751.26, y: 164.15, z: 430.05, dx: -1, dz: 0, halfAngle: .8 };
  scenery.setRadius(6000, view); view.dx = 1;
  await settle(); scenery.update(3751, 430, 1000); await settle();
  expect(scenery.heading).toBe(270);
  expect(scenery.demand()).toEqual([]);
  expect(scenery.progress.loaded).toBe(1);
  expect(request.mock.calls.every(([url]) => url.includes('/scenery'))).toBe(true);
  expect(load).toHaveBeenCalledWith('http://localhost/coarse/scenery/lod.test.glb', expect.any(String));
  expect(host.checkCollision).not.toHaveBeenCalled();
  scenery.update(5000, 1000, 40000); await settle();
  expect(load).toHaveBeenCalledTimes(1);
});

test('near scenery awaits known tree placements; approaching a cached far tile upgrades vegetation once', async () => {
  const {scenery,request,host,load}=setup();
  const ids=['t0_0_0','t0_4_0'],keys=['a'.repeat(64),'b'.repeat(64)];
  host.vegetationDistance=180;
  host.prepareVegetation=vi.fn(async()=>{});
  request.mockImplementation(async url=>{
    if(url.includes('/scenery?'))return new Response(JSON.stringify({tiles:ids.map((tile,i)=>({tile,key:keys[i],h:[0,20],source_sha256:'same',trees_url:`scenery/${keys[i]}.trees.json`})),missing:[]}));
    if(url.endsWith('.trees.json'))return new Response('{"version":1,"nodes":[]}');
    const i=url.includes(keys[0])?0:1;
    return new Response(JSON.stringify({coarse:true,tile:ids[i],pass_kind:'full',visual:{[ids[i]]:{url:'scene.glb',sha256:ids[i]}}}));
  });
  const view={x:125,y:5,z:125,dx:1,dz:0,halfAngle:1,focalPixels:1600};
  scenery.setRadius(4000,view);await settle();scenery.update(125,125,1000);await settle();
  expect(load).toHaveBeenCalledTimes(2);
  expect(host.prepareVegetation).toHaveBeenCalledTimes(1);
  scenery.setView({...view,x:1125},2000);await settle();scenery.update(1125,125,2000);await settle();
  expect(load).toHaveBeenCalledTimes(2);
  expect(host.prepareVegetation).toHaveBeenCalledTimes(2);
  scenery.update(1125,125,3000);await settle();
  expect(host.prepareVegetation).toHaveBeenCalledTimes(2);
});

test('a catalogue without tree sidecars does not repoll geometry every frame or expose nearby cones', async () => {
  const {scenery,request,host,load}=setup();host.vegetationDistance=180;host.prepareVegetation=vi.fn(async()=>{});
  const key='a'.repeat(64);
  request.mockImplementation(async url=>new Response(JSON.stringify(url.includes('/scenery?')
    ?{tiles:[{tile:'t0_0_0',key,h:[0,20],source_sha256:'same'}],missing:[]}
    :{coarse:true,tile:'t0_0_0',pass_kind:'full',visual:{t0_0_0:{url:'scene.glb',sha256:'same'}}})));
  load.mockImplementation(async()=>{const model=new THREE.Group(),cones=new THREE.Group();cones.userData.layer='vegetation';model.add(cones);return model;});
  scenery.setRadius(4000,{x:125,y:5,z:125,dx:1,dz:0,halfAngle:.5});await settle();
  for(let now=1000;now<1100;now++){scenery.update(125,125,now);await settle();}
  expect(load).toHaveBeenCalledTimes(1);expect(host.prepareVegetation).not.toHaveBeenCalled();
  expect((await load.mock.results[0].value).children[0].visible).toBe(false);
  expect(request.mock.calls.filter(([url])=>url.endsWith('.json'))).toHaveLength(1);
});

test('queued tree placements remain pending and retry promptly without claiming a source failure', async () => {
  const {scenery,request,host}=setup();host.vegetationDistance=180;host.prepareVegetation=vi.fn(async()=>{});
  const key='a'.repeat(64);let attempts=0;
  request.mockImplementation(async url=>{
    if(url.includes('/scenery?'))return new Response(JSON.stringify({tiles:[{tile:'t0_0_0',key,h:[0,20],source_sha256:'same',trees_url:`scenery/${key}.trees.json`}],missing:[]}));
    if(url.endsWith('.trees.json'))return ++attempts===1?new Response('{"state":"preparing"}',{status:202}):new Response('{"version":1,"nodes":[]}');
    return new Response(JSON.stringify({coarse:true,tile:'t0_0_0',pass_kind:'full',visual:{t0_0_0:{url:'scene.glb',sha256:'same'}}}));
  });
  scenery.setRadius(4000,{x:125,y:5,z:125,dx:1,dz:0,halfAngle:.5});await settle();
  scenery.update(125,125,1000);await settle();
  expect(scenery.progress.errors).toBe(0);expect(scenery.progress.pending).toBe(1);expect(scenery.progress.loaded).toBe(0);
  scenery.update(125,125,2999);await settle();expect(attempts).toBe(1);
  scenery.update(125,125,3001);await settle();
  expect(attempts).toBe(2);expect(scenery.progress.loaded).toBe(1);expect(scenery.progress.errors).toBe(0);
});

test('a ready skyline retry completes before hundreds of untouched scenery tiles', async () => {
  const { scenery, request, load } = setup();
  const chimney = 't2_-2_1';
  const attempts = new Map<string, number>();
  request.mockImplementation(async (url: string) => {
    if (url.includes('/scenery?')) {
      const tiles = new URL(url, 'http://localhost').searchParams.get('tiles')!.split(',');
      return new Response(JSON.stringify({ tiles: tiles.map(tile => ({ tile, key: tile,
        h: [0, tile === chimney ? 107 : 0], source_sha256: 'base' })), missing: [] }));
    }
    const tile = url.split('/').at(-1)!.replace('.json', '');
    const count = (attempts.get(tile) ?? 0) + 1; attempts.set(tile, count);
    if (tile !== chimney || count === 1) return new Response('{}', { status: 202 });
    return new Response(JSON.stringify({ coarse: true, tile, pass_kind: 'full',
      visual: { [tile]: { url: '../scenery/chimney.glb', sha256: 'derived' } } }));
  });
  scenery.setRadius(6000, { x: 3751.26, y: 164.15, z: 430.05, dx: -1, dz: 0, halfAngle: .8 });
  await settle();
  scenery.update(3751.26, 430.05, 0); await settle();
  expect(attempts.get(chimney)).toBe(1);
  expect(scenery.progress.total).toBeGreaterThan(50);
  scenery.update(3751.26, 430.05, 1000); await settle();
  expect(attempts.size).toBe(8); // Other scenery advances while the skyline retry is cooling down.
  scenery.update(3751.26, 430.05, 2000); await settle();
  expect(attempts.get(chimney)).toBe(2);
  expect(load).toHaveBeenCalledWith('http://localhost/coarse/scenery/chimney.glb', expect.any(String));
  expect(scenery.progress.loaded).toBe(1);
});

test('coarse city survives turns and is released after leaving its area', async () => {
  const { scenery, request, scene, load, dispose } = setup();
  const west = 't2_-2_1', east = 't2_7_0';
  request.mockImplementation(async (url: string) => {
    if (url.includes('/scenery?')) {
      const ids = new URL(url, 'http://localhost').searchParams.get('tiles')!.split(',');
      return new Response(JSON.stringify({ tiles: [west, east].filter(tile => ids.includes(tile))
        .map(tile => ({ tile, key: tile, h: [0, 107], source_sha256: 'base' })), missing: [] }));
    }
    const tile = url.split('/').at(-1)!.replace('.json', '');
    return new Response(JSON.stringify({ coarse: true, tile, pass_kind: 'full',
      visual: { [tile]: { url: `../scenery/${tile}.glb`, sha256: tile } } }));
  });
  const view = { x: 3751.26, y: 164.15, z: 430.05, dx: -1, dz: 0, halfAngle: .8 };
  scenery.setRadius(6000, view); await settle();
  scenery.update(view.x, view.z, 1000); await settle();
  expect(scenery.progress.loaded).toBe(2);
  const original = scene.children[0];
  scenery.setView({ ...view, dx: 1 }, 1200);
  await settle(); scenery.update(view.x, view.z, 1216); await settle();
  expect(scenery.heading).toBe(90); expect(scenery.progress.loaded).toBe(2);
  expect(load).toHaveBeenCalledWith('http://localhost/coarse/scenery/t2_7_0.glb', expect.any(String));
  expect(scene.children).toContain(original);
  const catalogues = request.mock.calls.filter(([url]) => url.includes('/scenery?')).length;
  scenery.setView(view, 1500); scenery.update(view.x, view.z, 1500); await settle();
  expect(scenery.heading).toBe(270); expect(scenery.progress.loaded).toBe(2);
  expect(request.mock.calls.filter(([url]) => url.includes('/scenery?'))).toHaveLength(catalogues);
  expect(load).toHaveBeenCalledTimes(2); expect(scene.children).toContain(original);
  scenery.setView({ ...view, x: 25000 }, 8000); scenery.update(25000, view.z, 8000); await settle();
  expect(dispose).toHaveBeenCalledWith(original, west);
  expect(scene.children).toHaveLength(0);
  expect(request.mock.calls.every(([url]) => url.includes('/scenery'))).toBe(true);
  scenery.setRadius(0); expect(scene.children).toHaveLength(0);
});

test('a city service without a scenery catalogue (404) is asked on the 30 s refresh, not every 2 s', async () => {
  const { scenery, request, load } = setup();
  request.mockImplementation(async () => new Response('', { status: 404 }));
  const view = { x: 3751.26, y: 164.15, z: 430.05, dx: -1, dz: 0, halfAngle: .8 };
  scenery.setRadius(6000, view); await settle();
  for (let t = 1000; t <= 25_000; t += 500) { scenery.update(view.x, view.z, t); await settle(); }
  const catalogues = request.mock.calls.filter(([url]) => url.includes('/scenery?')).length;
  expect(catalogues).toBeLessThanOrEqual(3);
  expect(scenery.progress.errors).toBe(0);
  expect(scenery.progress.missing).toBeGreaterThan(0);
  expect(load).not.toHaveBeenCalled();
});

test('a turn during catalogue loading cannot restore the old sector', async () => {
  const { scenery, request, load } = setup();
  let release!: (response: Response) => void;
  request.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  request.mockImplementation(async (url: string) => {
    if (url.includes('/scenery?')) return new Response(JSON.stringify({ tiles: [{ tile: 't2_7_0', key: 'east', h: [0, 10], source_sha256: 'base' }], missing: [] }));
    return new Response(JSON.stringify({ coarse: true, tile: 't2_7_0', pass_kind: 'full',
      visual: { 't2_7_0': { url: '../scenery/east.glb', sha256: 'east' } } }));
  });
  const view = { x: 3751.26, y: 164.15, z: 430.05, dx: -1, dz: 0, halfAngle: .4 };
  scenery.setRadius(6000, view);
  scenery.setView({ ...view, dx: 1 }, 1000);
  release(new Response(JSON.stringify({ tiles: [{ tile: 't2_-2_1', key: 'west', h: [0, 107], source_sha256: 'base' }], missing: [] })));
  await settle(); scenery.update(view.x, view.z, 1000); await settle();
  scenery.update(view.x, view.z, 1016); await settle();
  expect(scenery.heading).toBe(90);
  expect(load).toHaveBeenCalledTimes(1);
  expect(load).toHaveBeenCalledWith('http://localhost/coarse/scenery/east.glb', expect.any(String));
});

// A mobile collision preview does not cover the simplified building/terrain silhouette.
test('collision previews retain distant scenery until the detailed model replaces them', async () => {
  const { scenery, nearby, scene } = setup();
  scenery.setRadius(1000); scenery.update(125, 125, 0); await settle();
  const model = scene.children[0];
  const entry: Entry = { model: new THREE.Group(), preview: { coarse: true } as Entry['preview'],
    state: 'draft', nextPoll: 0, collision: new Map() };
  nearby.set('t0_0_0', entry);
  scenery.update(125, 125, 1000); await settle();
  expect(model.parent).toBe(scene);
  entry.preview = undefined;
  scenery.update(125, 125, 2000); await settle();
  expect(model.parent).toBeNull();
});


test('loaded parent covers refinement until all selected children arrive, without double drawing', async () => {
  const { scenery, scene } = setup();
  scenery.setRadius(8000, { x: 100, y: 20, z: 100, dx: 1, dz: 0, halfAngle: .8 });
  await settle();
  const state = scenery as unknown as { footprint: string[]; unavailable: Set<string>;
    entries: Map<string, { model: THREE.Group; next: number; seen: number; complete: boolean }> };
  state.footprint = ['t0_0_0', 't0_1_0']; state.unavailable.clear();
  const parent = new THREE.Group(), left = new THREE.Group(), right = new THREE.Group();
  scene.add(parent, left, right);
  const entry = (model: THREE.Group) => ({ model, next: Infinity, seen: 0, complete: true });
  state.entries.set('t2_0_0', entry(parent)); state.entries.set('t0_0_0', entry(left));
  scenery.update(100, 100, 1000, true);
  expect(parent.visible).toBe(true); expect(left.visible).toBe(false);
  right.visible = false; // as poll() installs a live-view model, until the cut commits it
  state.entries.set('t0_1_0', entry(right));
  scenery.update(100, 100, 1100, true);
  // The children dissolve in over the parent, which stays opaque underneath until they land.
  expect(left.visible).toBe(true); expect(right.visible).toBe(true); expect(parent.visible).toBe(true);
  expect(lodFades.has(left) && lodFades.has(right)).toBe(true);
  lodFades.update(1100 + LOD_FADE_SECONDS * 1000);
  scenery.update(100, 100, 1100 + LOD_FADE_SECONDS * 1000, true);
  expect(parent.visible).toBe(false); expect(left.visible).toBe(true); expect(right.visible).toBe(true);
  scenery.update(100, 100, 7000, true);
  expect(parent.parent).toBeNull();
});


test('old children remain visible while a coarser replacement is loading', async () => {
  const { scenery, scene } = setup();
  scenery.setRadius(8000, { x: 2000, y: 20, z: 100, dx: -1, dz: 0, halfAngle: .8 });
  await settle();
  const state = scenery as unknown as { footprint: string[]; unavailable: Set<string>;
    entries: Map<string, { model: THREE.Group; next: number; seen: number; complete: boolean }> };
  state.footprint = ['t2_0_0']; state.unavailable.clear();
  const child = new THREE.Group(), parent = new THREE.Group(); scene.add(child, parent);
  const entry = (model: THREE.Group) => ({ model, next: Infinity, seen: 0, complete: true });
  state.entries.set('t0_0_0', entry(child));
  scenery.update(2000, 100, 10000, true);
  expect(child.visible).toBe(true); expect(child.parent).toBe(scene);
  parent.visible = false; // as poll() installs a live-view model, until the cut commits it
  state.entries.set('t2_0_0', entry(parent));
  scenery.update(2000, 100, 10016, true);
  // Coarsening dissolves too: the parent fades in over the child, which stays opaque until it lands.
  expect([parent.visible, lodFades.has(parent), child.visible]).toEqual([true, true, true]);
  lodFades.update(10016 + LOD_FADE_SECONDS * 1000);
  scenery.update(2000, 100, 10016 + LOD_FADE_SECONDS * 1000, true);
  expect(parent.visible).toBe(true); expect(child.visible).toBe(false);
});


test('steep aerial views request ground on every side of the camera', () => {
  const { scenery } = setup();
  scenery.setRadius(4000);
  const camera = new THREE.PerspectiveCamera(60, 16 / 9, .1, 16000);
  camera.position.set(125, 150, 125);
  camera.lookAt(125, 0, 125); camera.updateMatrixWorld();
  scenery.follow(camera);
  const ids = (scenery as unknown as { footprint: string[] }).footprint;
  for (const id of ['t0_-1_0', 't0_1_0', 't0_0_-1', 't0_0_1']) expect(ids).toContain(id);
});

test('a collision preview yields to scenery and returns if that scenery leaves', async () => {
  const { scenery, nearby } = setup();
  const model = new THREE.Group();
  nearby.set('t0_0_0', { model, preview: {} as never, state: 'draft', nextPoll: 0, collision: new Map() });
  scenery.setRadius(1000); scenery.update(125, 125, 0); await settle();
  scenery.update(125, 125, 1000); await settle();
  expect(model.visible).toBe(false);
  scenery.setRadius(0);
  scenery.update(125, 125, 2000);
  expect(model.visible).toBe(true);
});

test('stable scenery skips ownership work but retains the full look-back TTL after a long stop', async () => {
  const { scenery, scene } = setup();
  scenery.setRadius(4000, {x:125,y:5,z:125,dx:1,dz:0,halfAngle:.8});
  await settle();
  const state = scenery as unknown as {
    footprint: string[]; unavailable: Set<string>; commitCut(now:number):void;
    entries: Map<string, {model:THREE.Group; next:number; seen:number; complete:boolean}>;
  };
  state.footprint = ['t0_0_0']; state.unavailable.clear();
  const model = new THREE.Group(); scene.add(model);
  state.entries.set('t0_0_0', {model,next:Infinity,seen:0,complete:true});
  const commit = vi.spyOn(state,'commitCut');
  scenery.update(125,125,1000,true);
  for (let now=2000;now<=20000;now+=1000) scenery.update(125,125,now,true);
  expect(commit).toHaveBeenCalledTimes(1);
  state.footprint = [];
  scenery.update(125,125,21000,true);
  expect(model.visible).toBe(false); expect(model.parent).toBe(scene);
  scenery.update(125,125,24999,true);
  expect(model.parent).toBe(scene);
  expect(commit).toHaveBeenCalledTimes(2);
  scenery.update(125,125,25000,true);
  expect(model.parent).toBeNull();
  expect(commit).toHaveBeenCalledTimes(3);
});

test('near-model replacement and caching invalidate a stable cut without changing the camera', async () => {
  const { scenery, nearby, scene } = setup();
  scenery.setRadius(4000, {x:125,y:5,z:125,dx:1,dz:0,halfAngle:.8});
  await settle();
  const state = scenery as unknown as {
    footprint:string[]; unavailable:Set<string>; commitCut(now:number):void;
    entries:Map<string,{model:THREE.Group;next:number;seen:number;complete:boolean}>;
  };
  state.footprint=['t0_0_0']; state.unavailable.clear();
  const far=new THREE.Group(), near=new THREE.Group(); scene.add(far,near);
  state.entries.set('t0_0_0',{model:far,next:Infinity,seen:0,complete:true});
  const entry:Entry={model:near,preview:{} as never,state:'draft',nextPoll:0,collision:new Map()};
  nearby.set('t0_0_0',entry);
  scenery.update(125,125,1000,true);
  expect(far.visible).toBe(true); expect(near.visible).toBe(false);
  const commit=vi.spyOn(state,'commitCut');
  scenery.update(125,125,1016,true); expect(commit).not.toHaveBeenCalled();
  entry.preview=undefined; near.visible=true;
  scenery.update(125,125,1032,true);
  // Under the full tile the camera stands on (within STANDBY_MARGIN) the cell is released, not kept on standby.
  expect(far.parent).toBeNull(); expect(near.visible).toBe(true);
  entry.cachedAt=1048; near.visible=false;
  scenery.update(125,125,1048,true);
  expect(commit).toHaveBeenCalledTimes(2);
  expect(scenery.progress.total).toBe(1);
});

test('the cell under a resident full tile is preloaded hidden and covers the square the moment the tile leaves', async () => {
  const { scenery, request, load, nearby } = setup();
  const key = 'a'.repeat(64);
  request.mockImplementation(async url => new Response(JSON.stringify(url.includes('/scenery?')
    ? { tiles: [{ tile: 't0_1_0', key, h: [0, 20], source_sha256: 'same' }], missing: [] }
    : { coarse: true, tile: 't0_1_0', pass_kind: 'full', visual: { t0_1_0: { url: 'scene.glb', sha256: 'same' } } })));
  const full: Entry = { model: new THREE.Group(), state: 'draft', nextPoll: 0, collision: new Map() };
  // A full tile 190 m away (beyond STANDBY_MARGIN): it can leave the window soon, so its cell waits on standby.
  nearby.set('t0_1_0', full);
  scenery.setRadius(4000, { x: 60, y: 5, z: 125, dx: 1, dz: 0, halfAngle: .5 }); await settle();
  for (let now = 1000; now < 1010; now++) { scenery.update(60, 125, now); await settle(); }
  expect(load).toHaveBeenCalledTimes(1);
  const cell = await load.mock.results[0].value as THREE.Group;
  expect(cell.parent).not.toBeNull(); expect(cell.visible).toBe(false);
  expect(scenery.progress.total).toBe((scenery as unknown as { wanted: string[] }).wanted.length);
  // The full tile leaves: the prepared cell takes the square back without another request.
  const requests = request.mock.calls.length;
  full.model = undefined;
  scenery.update(60, 125, 1100); await settle();
  expect(cell.visible).toBe(true);
  expect(request.mock.calls.filter(([url]) => !url.includes('/scenery?')).length)
    .toBe(request.mock.calls.slice(0, requests).filter(([url]) => !url.includes('/scenery?')).length);
});

test('a cell whose dissolve twins are not compiled swaps at once instead of fading or building a shader', async () => {
  const { scenery, scene } = setup();
  scenery.setRadius(8000, { x: 100, y: 20, z: 100, dx: 1, dz: 0, halfAngle: .8 });
  await settle();
  const state = scenery as unknown as { footprint: string[]; unavailable: Set<string>;
    entries: Map<string, { model: THREE.Group; next: number; seen: number; complete: boolean }> };
  state.footprint = ['t0_0_0', 't0_1_0']; state.unavailable.clear();
  const parent = new THREE.Group(), left = new THREE.Group(), right = new THREE.Group();
  // Real meshes with node materials and no compiled twins.
  for (const cell of [left, right]) cell.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardNodeMaterial()));
  scene.add(parent, left, right); left.visible = right.visible = false;
  const entry = (model: THREE.Group) => ({ model, next: Infinity, seen: 0, complete: true });
  state.entries.set('t2_0_0', entry(parent)); state.entries.set('t0_0_0', entry(left)); state.entries.set('t0_1_0', entry(right));
  scenery.update(100, 100, 1000, true);
  expect([left.visible, right.visible, parent.visible]).toEqual([true, true, false]);
  expect(lodFades.has(left) || lodFades.has(right)).toBe(false);
  expect(dissolveTiers(0)).toEqual([0, 1]); expect(dissolveTiers(2)).toEqual([1, 2]); expect(dissolveTiers(4)).toEqual([2]);
});

test('a full tile within STANDBY_MARGIN of the camera keeps no hidden cell: its square cannot leave the window soon', async () => {
  const { scenery, request, load, nearby } = setup();
  request.mockImplementation(async url => new Response(JSON.stringify(url.includes('/scenery?')
    ? { tiles: [{ tile: 't0_0_0', key: 'b'.repeat(64), h: [0, 20], source_sha256: 'same' }], missing: [] }
    : { coarse: true, tile: 't0_0_0', pass_kind: 'full', visual: { t0_0_0: { url: 'scene.glb', sha256: 'same' } } })));
  nearby.set('t0_0_0', { model: new THREE.Group(), state: 'draft', nextPoll: 0, collision: new Map() });
  scenery.setRadius(4000, { x: 125, y: 5, z: 125, dx: 1, dz: 0, halfAngle: .5 }); await settle();
  for (let now = 1000; now < 1010; now++) { scenery.update(125, 125, now); await settle(); }
  expect(load).not.toHaveBeenCalled();
  expect(STANDBY_MARGIN).toBe(150);
});

test('a kilometre fallback never draws over a square a full tile draws (Kauppatori tilted-ground regression)', async () => {
  const { scenery, scene, nearby } = setup();
  scenery.setRadius(8000, { x: 100, y: 8, z: 190, dx: 1, dz: 0, halfAngle: .5 });
  await settle();
  const state = scenery as unknown as { footprint: string[]; unavailable: Set<string>;
    entries: Map<string, { model: THREE.Group; next: number; seen: number; complete: boolean }> };
  // Refining the 1 km node t2_-1_0: its western cells are still loading while full tiles draw its eastern squares.
  state.footprint = ['t0_-1_0', 't0_-3_0']; state.unavailable.clear();
  const parent = new THREE.Group(); scene.add(parent);
  state.entries.set('t2_-1_0', { model: parent, next: Infinity, seen: 0, complete: true });
  scenery.update(100, 190, 1000, true);
  expect(parent.visible).toBe(true); // nothing HD under it yet: it covers the refinement
  nearby.set('t0_-1_0', { model: new THREE.Group(), state: 'draft', nextPoll: 0, collision: new Map() });
  scenery.nearbyChanged('t0_-1_0');
  scenery.update(100, 190, 1100, true);
  expect(parent.visible).toBe(false);
  expect(state.entries.has('t2_-1_0')).toBe(true); // still loaded: it returns when the full tile leaves
  nearby.delete('t0_-1_0'); scenery.nearbyChanged('t0_-1_0');
  scenery.update(100, 190, 1200, true);
  expect(parent.visible).toBe(true);
});


/** Textures and constants a TSL node graph reads. */
function graphInputs(node: unknown, seen = new Set<object>(), out = { textures: new Set<THREE.Texture>(), values: new Set<unknown>() }) {
  if (!node || typeof node !== 'object' || seen.has(node)) return out;
  seen.add(node);
  const n = node as { isTextureNode?: boolean; isConstNode?: boolean; value?: unknown };
  if (n.isTextureNode) out.textures.add(n.value as THREE.Texture);
  if (n.isConstNode) out.values.add(n.value);
  for (const child of Object.values(node))
    if (child && typeof child === 'object' && ((child as { isNode?: boolean }).isNode || Array.isArray(child))) graphInputs(child, seen, out);
  return out;
}

test('a kilometre fallback keeps its canopy shell off every square a full tile or a 250 m cell draws (city.lab slab)', async () => {
  const { scenery, scene, nearby } = setup();
  scenery.setRadius(8000, { x: 100, y: 20, z: 100, dx: 1, dz: 0, halfAngle: .8 });
  await settle();
  const state = scenery as unknown as { footprint: string[]; unavailable: Set<string>; squares: { pixels: Uint8Array; texture: THREE.DataTexture };
    entries: Map<string, { model: THREE.Group; next: number; seen: number; complete: boolean }> };
  const covered = (id: string) => {
    const b = sceneryBounds(id), size = 256;
    return state.squares.pixels[(b.z / 250 + size / 2) * size + b.x / 250 + size / 2] === 255;
  };
  // The 1 km node t2_0_0 stays drawn as the fallback while its cells load; the loaded cells dissolve in over it.
  state.footprint = ['t0_0_0', 't0_1_0']; state.unavailable.clear();
  const parent = new THREE.Group(), left = new THREE.Group(), right = new THREE.Group();
  scene.add(parent, left, right);
  const entry = (model: THREE.Group) => ({ model, next: Infinity, seen: 0, complete: true });
  state.entries.set('t2_0_0', entry(parent)); state.entries.set('t0_0_0', entry(left));
  scenery.update(100, 100, 1000, true);
  expect([parent.visible, left.visible]).toEqual([true, false]);
  expect(covered('t0_0_0')).toBe(false); // a loaded but hidden cell does not hold the shell back
  right.visible = false;
  state.entries.set('t0_1_0', entry(right));
  scenery.update(100, 100, 1100, true);
  expect([parent.visible, left.visible, right.visible]).toEqual([true, true, true]);
  expect(covered('t0_0_0') && covered('t0_1_0')).toBe(true);
  // A full tile drawing a square of the node (its batch can still hold the node's shell for a few frames).
  nearby.set('t0_2_1', { model: new THREE.Group(), state: 'draft', nextPoll: 0, collision: new Map() });
  scenery.nearbyChanged('t0_2_1');
  expect(covered('t0_2_1')).toBe(true);
  // The node's other squares keep the shell: only the squares something finer draws are masked.
  for (const id of ['t0_2_0', 't0_3_3', 't0_0_2']) expect(covered(id)).toBe(false);

  // The node's canopy samples exactly this texture, beyond the full-tile window; a 250 m cell's canopy does not.
  const shell = () => {
    const model = new THREE.Group(), geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 0, 1], 3));
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial()); mesh.userData.layer = 'canopy';
    model.add(mesh);
    return { model, mesh };
  };
  const coverage = new THREE.DataTexture(new Uint8Array(4), 2, 2);
  const node = shell(), cell = shell();
  styleScenery(node.model, undefined, coverage, state.squares.texture);
  styleScenery(cell.model, undefined, coverage);
  const nodeMask = graphInputs((node.mesh.material as THREE.MeshStandardNodeMaterial).maskNode);
  const cellMask = graphInputs((cell.mesh.material as THREE.MeshStandardNodeMaterial).maskNode);
  expect([...nodeMask.textures]).toEqual([state.squares.texture]);
  expect(nodeMask.values.has(TILE_DETAIL.distance)).toBe(true);
  expect(cellMask.textures.size).toBe(0);
  expect(cellMask.values.has(TILE_DETAIL.distance)).toBe(true);
});
