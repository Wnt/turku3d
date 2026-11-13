// The loader of the world pipeline's location manifest (docs/design/world-pipeline.md, sections 5 and 8).
//
// Entities are drawn in chunks: the entities of one renderer layer whose origins fall in the same 250 m square are
// styled, prepared and drawn as one model, as the game does with a tile, so the cost of a location follows its
// content and not its entity count (2,000 entities as 2,000 models did not finish loading in ten minutes). Instance
// rows are drawn as merged geometry per chunk and material: an InstancedMesh per entity and asset mesh cost about
// twenty shader builds each (3,000 of them at one location), while merged meshes share the builds of their material.
// A changed entity redraws its chunk in the running client while the camera, the lighting and every other chunk
// stay. An object the player moves will leave the merged mesh as its own object (the manifest row has its id,
// behaviour, collision and mass).
//
// A session that moves through the location (play, look-around: `host.view`) pays per frame for what it looks at,
// not for what the location holds. Measured at Portsa on the M1 Max (2026-11-13): 26 million triangles a frame, 21
// of them trees and 14.5 of those in the shadow maps, and a main thread that spent a third of its frame walking
// 9,500 scene nodes once per pass.
// - Trees and planting are drawn by distance from the camera, per 62.5 m cell, at the two levels the pipeline makes
//   from one source: within `view.treeNear()` every tree and shrub is an instance of its library model, and a
//   planting cell's own hedges an instance of its asset (instancePool.ts: one draw per library mesh for the whole
//   location), beyond it the cell is its one far mesh. Hedge sprays are grown from the bodies (families/sprays.ts). Only the cells around the camera
//   cast their instances' shadows, and only in the sun's nearest cascade; everywhere else a cell casts as its far mesh.
// - The chunks of ground, buildings and structures cast shadows through their shadow batch alone: the batches are
//   drawn beside them, and the sun's passes do not walk the chunks (`beforePass`). In the camera's own passes the
//   chunks and entities outside its view leave the scene graph too. Any other camera (a reflection, a probe) sees
//   everything.
// - A drawn chunk never moves: its matrices are composed once, not walked by every frame's scene update.
// Stills (no `host.view`) draw every entity whole at the level of its role, as before.
import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { disposeDraw } from '../../render/sharedMaterials';
import { enToEngine } from '../../core/origin';
import { releaseGeometry } from '../modelGeometry';
import { modelGpuBytes, rendererResidency } from '../gpuBytes';
import { StreamStats } from '../streamStats';
import type { Entry } from '../coarseContracts';
import type { ManifestEntry } from '../manifest';
import type { PreparedWorld, PreparedWorldHost } from '../preparedWorld';
import type { RoadGraphJson } from '../../gameplay/roadGraph';
import type { PreparedLighting } from '../../render/lights/preparedLighting';
import { TILE_SHADOW_CHUNK_LAYER, TILE_SHADOW_WHOLE_LAYER } from '../shadowBatch';
import { InstancePool, type Part, type Rows } from './instancePool';
import { readLocation, type LocationEntity, type LocationInstances, type LocationManifest } from './manifest';
import { chunkDistance, isRegion, readChunk, readRegion, regionManifest, REGION_RANGE, REGION_SLACK, REGION_STEP, type RegionIndex } from './region';
import { dress, familyDrawn, markVertices, storeRoot, type Drawn as Dressing } from './materials';
import { growHedges } from './families/sprays';
import './families/ground';
import './families/building';
import './families/objects';

/** The store root as the dev server and a static host serve it. */
const STORE = 'world/';
/** Renderer layers by kind: which batching, shadow and reflection rules apply (world/staticBatch.ts, shadowBatch.ts). */
// Trees and planting are vegetation: millions of instanced leaf-card triangles that must not be copied into an
// entity's merged shadow batch (world/shadowBatch.ts skips the layer); they cast their own shadows instead.
const LAYERS: Record<string, string> = { ground: 'ground', building: 'buildings', trees: 'vegetation', planting: 'vegetation' };
/** Kinds whose cells are instance rows near and one merged mesh far, drawn by distance in a moving session. */
const LEVELED = new Set(['trees', 'planting']);
const GROUND_BINS = 5;
const LOADS = 8;
/** The side of the square whose entities are prepared as one model, in metres. */
const CHUNK = 250;

/** Trees this near (m) cast their instances' shadows in the sun's nearest cascade, which covers the first metres in
 *  front of the camera; a cell farther away casts there as its far mesh, as it does in the outer cascades. */
const SHADOW_NEAR = 40;
/** A cell changes level this far (m) beyond the distance at which it changed the other way. */
const LEVEL_SLACK = 8;
/** The layers only the sun's cascade cameras see (render/environment.ts cascadeLayerMask). */
const SUN_LAYERS = 1 << TILE_SHADOW_CHUNK_LAYER | 1 << TILE_SHADOW_WHOLE_LAYER;

/** One asset mesh at one instance row, in world space. */
interface Placed { geometry: THREE.BufferGeometry; material: THREE.Material; matrix: THREE.Matrix4; vertex?: Dressing['vertex'] }
/** An entity's two levels in a moving session: the rows of its near instances, its far mesh and that mesh's shadow
 *  twin. `level`: 0 instances that cast their own shadows, 1 instances, 2 the far mesh; -1 before the first frame. */
interface Levels { rows: Rows[]; far: THREE.Object3D; farShadow: THREE.Group | null; level: number }
interface Drawn { row: LocationEntity; root: THREE.Group; placed: Placed[]; heights: { x0: number; z0: number; nx: number; nz: number; y: Float32Array } | null;
  /** World box of the entity (manifest bounds). */
  box: THREE.Box3; levels: Levels | null }
interface Chunk { id: string; root: THREE.Group; members: Drawn[]; box: THREE.Box3; layer: string }
/** The parent of chunks that never move once drawn: `show` composes their matrices, no frame's scene update does. */
class Still extends THREE.Group { override updateMatrixWorld() {} }
/** The near instance rows and the far mesh of a row that has both (trees and planting), whichever its role draws. */
function levelPair(row: LocationEntity): { near: LocationInstances[]; far: string } | null {
  const all = [{ url: row.url, instances: row.instances }, ...row.levels ?? []];
  const near = all.find(level => level.instances.length && !level.url), far = all.find(level => level.url && !level.instances.length);
  return near && far && LEVELED.has(row.kind) ? { near: near.instances, far: far.url! } : null;
}
const layerOf = (row: LocationEntity) => LAYERS[row.kind] ?? 'structures';
const chunkOf = (row: LocationEntity) => `${layerOf(row)}:${Math.floor(row.origin[0] / CHUNK)}_${Math.floor(row.origin[2] / CHUNK)}`;
function byChunk(rows: readonly LocationEntity[]) {
  const chunks = new Map<string, LocationEntity[]>();
  for (const row of rows) chunks.set(chunkOf(row), [...chunks.get(chunkOf(row)) ?? [], row]);
  return chunks;
}
export interface RefreshReport { revision: number; changed: string[]; added: string[]; removed: string[]; ms: number }

class NoScenery {
  radius = 0;
  heading: number | null = null;
  progress = { ready: true, loaded: 0, total: 0, pending: 0, errors: 0, missing: 0, cataloguePending: false };
  setRadius(_radius: number, _view?: unknown) {}
  follow(_camera: THREE.Camera) {}
  setView(_view: unknown, _now: number) {}
}

/** What the rest of the client asks of the drawn world, apart from the prepared scene's appearance patches. */
type WorldSurface = Omit<Pick<PreparedWorld, keyof PreparedWorld>, 'data' | 'region' | 'sessionScope' | 'comparisonStatus' | 'applyAppearance' | 'reloadAppearance' | 'restoreAppearance'>;

export class LocationWorld implements WorldSurface {
  readonly entries = new Map<string, Entry>();
  readonly screenshotScenery = new NoScenery();
  readonly stats = new StreamStats();
  readonly lighting: PreparedLighting;
  onMapChange: (() => void) | null = null;
  retainedStarts: readonly { x: number; z: number }[] = [];
  private roads: RoadGraphJson[] = [];
  scheduling = true;
  readonly visualPass = 'full';
  readonly comparisonStatus = 'Location manifest';
  readonly releasedBytes = 0;
  status = '';
  /** What capture tools read as the drawn scene: the location and its manifest revision. */
  get data() { return { id: `world:${this.manifest.name}`, release: `r${this.manifest.revision}` }; }
  private shown = new Map<string, Drawn>();
  /** A region (region.ts): its index, the rows of the chunk objects fetched so far, where the drawn set was chosen
   *  for, and whether a choice is being drawn now. */
  private region: { index: RegionIndex; rows: Map<string, LocationEntity[]>; at: { x: number; z: number } | null; busy: boolean } | null = null;
  private chunks = new Map<string, Chunk>();
  private assets = new Map<string, Promise<THREE.Group>>();
  private materials = new Map<string, Dressing>();
  private loader = new GLTFLoader();
  private group = new THREE.Group();
  /** A moving session's chunks of ground, buildings and structures (the sun's passes skip them). */
  private still = new Still();
  /** A moving session's near trees: the ones that cast their own shadows in the nearest cascade, and the others. */
  private pools: [InstancePool, InstancePool] | null = null;
  private leveled = new Set<Drawn>();
  private errors: string[] = [];
  private updating: Promise<unknown> = Promise.resolve();
  private disposed = false;

  private constructor(private host: PreparedWorldHost, public manifest: LocationManifest, private url: string) {
    this.lighting = { id: `world:${manifest.name}`, lamps: [], footprint: null };
    this.group.name = `world:${manifest.name}`;
    if (host.view) {
      this.still.name = 'still';
      this.pools = [new InstancePool('trees:shadowing', 'vegetation', TILE_SHADOW_CHUNK_LAYER), new InstancePool('trees:near', 'vegetation', null)];
      this.group.add(this.still, ...this.pools.map(pool => pool.group));
      const scene = host.scene, previous = scene.onBeforeRender as unknown as (...args: unknown[]) => void;
      // r186 calls a scene's onBeforeRender in every render of it (the sun's cascades and reflections included),
      // after its matrices and camera are updated and before it collects what the camera draws.
      this.unhook = () => { scene.onBeforeRender = previous as unknown as THREE.Scene['onBeforeRender']; };
      scene.onBeforeRender = ((...args: unknown[]) => {
        previous.apply(scene, args);
        this.beforePass(args[2] as THREE.Camera);
      }) as unknown as THREE.Scene['onBeforeRender'];
    }
    storeRoot(new URL(STORE, host.pageUrl ?? location.href).href);
  }

  /** `at`: engine x, z of the first camera; a region draws the chunks around it before it resolves (its start
   *  or focus when none is given). */
  static async start(host: PreparedWorldHost, name: string, at?: { x: number; z: number }): Promise<LocationWorld> {
    const url = new URL(`${STORE}locations/${name}.json`, host.pageUrl ?? location.href).href;
    const raw = await LocationWorld.fetchJson(host, url);
    const index = isRegion(raw) ? readRegion(raw) : null;
    const manifest = index ? regionManifest(index) : readLocation(raw);
    if (manifest.origin.some((n, i) => n !== host.manifest.origin_en[i])) throw new Error('Location origin differs from the active world');
    const world = new LocationWorld(host, manifest, url);
    if (index) world.region = { index, rows: new Map(), at: null, busy: false };
    host.scene.add(world.group);
    host.manifest.code = world.data.release;
    const first = at ?? (manifest.start ? (() => { const p = enToEngine(manifest.start!.E, manifest.start!.N); return { x: p.x, z: p.z }; })()
      : { x: manifest.focus[0], z: manifest.focus[2] });
    const draw = index ? world.stream(first.x, first.z) : world.install(manifest.entities);
    // Play needs the collision before the drawing is worth waiting for: both load at once.
    const [, collision] = await Promise.all([draw.then(() => world.pool()), world.loadCollision(), world.loadRoads()]);
    // One entry stands for the location where the tile streamer's consumers look for loaded tiles.
    const [x, , z] = manifest.focus;
    world.entries.set(`t0_${Math.floor(x / 250)}_${Math.floor(z / 250)}`,
      { state: 'draft', model: world.group, collision, nextPoll: Infinity, pass: 'full', visualHash: world.data.release });
    world.describe();
    return world;
  }

  private static async fetchJson(host: PreparedWorldHost, url: string, cache: RequestCache = 'no-store'): Promise<unknown> {
    const response = await (host.fetch ?? fetch)(url, { cache, signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`Location manifest: HTTP ${response.status} (${url})`);
    return response.json();
  }

  /**
   * A region's drawn set for a camera at (x, z): every layer's chunks within its range (REGION_RANGE) are fetched
   * nearest first and drawn; drawn chunks beyond range + slack are released, and so are the rows of chunk objects
   * far beyond every range. The manifest's `entities` are the rows drawn now. A chunk that fails is reported in
   * `errors`, never stops the others.
   */
  private async stream(x: number, z: number) {
    const region = this.region!, { index } = region, side = index.chunk;
    region.at = { x, z };
    const reach = Math.max(...Object.values(REGION_RANGE));
    const keys = Object.keys(index.chunks).map(key => [key, chunkDistance(key, side, x, z)] as const)
      .filter(([, d]) => d <= reach).sort((a, b) => a[1] - b[1]);
    const fetch = keys.filter(([key]) => !region.rows.has(key));
    const failures: string[] = [];
    await Promise.all(Array.from({ length: Math.min(LOADS, fetch.length) }, async () => {
      for (let next = fetch.shift(); next; next = fetch.shift()) {
        const key = next[0];
        try { region.rows.set(key, readChunk(index, key, await LocationWorld.fetchJson(this.host, this.storeUrl(index.chunks[key].url), 'default'))); }
        catch (error) { failures.push(`chunk ${key}: ${error instanceof Error ? error.message : error}`); }
      }
    }));
    // The drawn chunk ids (layer:i_j) wanted now, with their rows, nearest first.
    const wanted = new Map<string, LocationEntity[]>();
    for (const [key, d] of keys) for (const row of region.rows.get(key) ?? []) {
      const id = chunkOf(row);
      if (d <= REGION_RANGE[layerOf(row)] + (this.chunks.has(id) ? REGION_SLACK : 0)) wanted.set(id, [...wanted.get(id) ?? [], row]);
    }
    for (const chunk of [...this.chunks.values()]) if (!wanted.has(chunk.id)) this.hide(chunk);
    for (const key of [...region.rows.keys()]) if (chunkDistance(key, side, x, z) > reach + 2 * REGION_SLACK) region.rows.delete(key);
    this.manifest.entities = [...wanted.values()].flat();
    const queue = [...wanted].filter(([id]) => !this.chunks.has(id));
    await Promise.all(Array.from({ length: Math.min(LOADS, queue.length) }, async () => {
      for (let next = queue.shift(); next; next = queue.shift()) {
        try { await this.show(next[0], await this.loadChunk(next[1], failures)); }
        catch (error) { failures.push(`chunk ${next[0]}: ${error instanceof Error ? error.message : error}`); }
      }
    }));
    if (failures.length) { this.errors = failures; console.warn(`Region ${index.name}: ${failures.length} failures: ${failures.slice(0, 3).join('; ')}`); }
    this.describe();
  }

  /** Chooses a region's drawn set again once the camera has moved REGION_STEP since the last choice; one at a time. */
  private follow(x: number, z: number) {
    const region = this.region!;
    if (region.busy || (region.at && Math.hypot(x - region.at.x, z - region.at.z) < REGION_STEP)) return;
    region.busy = true;
    const run = this.updating.then(() => this.stream(x, z)).then(() => this.pool());
    this.updating = run.catch(error => { console.warn('Region streaming failed', error); }).finally(() => { region.busy = false; });
  }

  /**
   * The location's collision cells, handed to the game exactly as a prepared scene's are (world/preparedWorld.ts):
   * checked, listed in the active manifest and retained for the collision streamer, which loads the cells near the
   * player into the physics worker and closes the edges of the loaded world. Only a host that plays asks for them.
   */
  private async loadCollision(): Promise<Map<string, ManifestEntry>> {
    const host = this.host, cells = new Map<string, ManifestEntry>();
    if (!host.retainCollision) return cells;
    const queue = Object.entries(this.manifest.collision ?? {});
    if (!queue.length) throw new Error(`Location ${this.manifest.name} was built without collision: rebuild it to play`);
    if (this.region) {
      // A region's cells are listed by url and fetched by the collision streamer when the player comes near.
      for (const [id, cell] of queue) {
        const entry = { ...cell, url: this.storeUrl(cell.url) };
        cells.set(id, entry);
        host.manifest.collision[id] = entry;
      }
      return cells;
    }
    await Promise.all(Array.from({ length: Math.min(LOADS, queue.length) }, async () => {
      for (let next = queue.shift(); next; next = queue.shift()) {
        const [id, cell] = next, url = this.storeUrl(cell.url);
        const response = await (host.fetch ?? fetch)(url, { signal: AbortSignal.timeout(30000) });
        if (!response.ok) throw new Error(`Collision ${id}: HTTP ${response.status}`);
        const buffer = await response.arrayBuffer();
        if (buffer.byteLength !== cell.bytes) throw new Error(`Collision ${id}: wrong byte count`);
        await host.checkCollision(buffer.slice(0));
        const entry = { ...cell, url };
        cells.set(id, entry);
        host.manifest.collision[id] = entry;
        host.retainCollision!(id, entry, buffer);
      }
    }));
    return cells;
  }

  /** The location's street graphs (manifest `roads`): where the start pose puts the car, where walkers walk. A graph
   *  that fails to load costs the location its streets, never its loading. */
  private async loadRoads() {
    const tiles = Object.entries(this.manifest.roads ?? {});
    const graphs = await Promise.all(tiles.map(async ([id, tile]) => {
      try {
        const response = await (this.host.fetch ?? fetch)(this.storeUrl(tile.url), { signal: AbortSignal.timeout(30000) });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json() as RoadGraphJson;
      } catch (error) {
        console.warn(`Location ${this.manifest.name}: road graph ${id} not loaded`, error);
        return null;
      }
    }));
    this.roads = graphs.filter((g): g is RoadGraphJson => !!g);
    if (this.roads.length) this.onMapChange?.();
  }

  private describe() {
    const triangles = [...this.shown.values()].reduce((sum, d) => sum + (d.row.triangles ?? 0), 0);
    this.status = `${this.manifest.name} r${this.manifest.revision} · ${this.shown.size} entities · ${triangles} mesh triangles`;
  }

  private storeUrl(path: string) {
    return new URL(path.startsWith('objects/') ? `${STORE}${path}` : path.replace(/^\//, ''), this.host.pageUrl ?? location.href).href;
  }

  private asset(path: string) {
    let asset = this.assets.get(path);
    if (!asset) {
      asset = this.loader.loadAsync(this.storeUrl(path)).then(gltf => { growHedges(gltf.scene); gltf.scene.updateMatrixWorld(true); return gltf.scene; });
      this.assets.set(path, asset);
    }
    return asset;
  }

  /** Loads one entity's meshes and instances into a detached group at its origin. */
  private async load(row: LocationEntity): Promise<Drawn> {
    const root = new THREE.Group(), layer = layerOf(row);
    root.name = `entity:${row.id}`; root.position.fromArray(row.origin);
    root.userData = { entityId: row.id, kind: row.kind, role: row.role, hash: row.hash, layer };
    const pair = this.host.view ? levelPair(row) : null;
    const url = pair ? pair.far : row.url;
    const own = url ? (await this.loader.loadAsync(this.storeUrl(url))).scene : null;
    if (own) { growHedges(own); root.add(own); }
    const placed: Placed[] = [], [ox, oy, oz] = row.origin;
    const turn = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0);
    const rows: Rows[] = [];
    for (const item of pair ? pair.near : row.instances) {
      const asset = await this.asset(item.asset);
      let part = 0;
      asset.traverse(node => {
        const source = node as THREE.Mesh;
        if (!source.isMesh) return;
        const { material, vertex } = familyDrawn(Array.isArray(source.material) ? source.material[0] : source.material, this.materials, row.kind);
        const matrices = item.transforms.map(([x, y, z, yaw, sx, sy, sz]) =>
          new THREE.Matrix4().compose(new THREE.Vector3(ox + x, oy + y, oz + z), turn.setFromAxisAngle(up, yaw), new THREE.Vector3(sx, sy, sz)).multiply(source.matrixWorld));
        const key = `${item.asset}#${part++}`;
        if (!pair) { for (const matrix of matrices) placed.push({ geometry: source.geometry, material, matrix, vertex }); return; }
        // A library mesh has one material: its vertex marks are the same wherever it is placed.
        markVertices(source.geometry, vertex);
        const packed = new Float32Array(matrices.length * 16);
        matrices.forEach((matrix, index) => matrix.toArray(packed, index * 16));
        rows.push({ key, geometry: source.geometry, material, matrices: packed });
      });
    }
    if (pair && own) own.name = 'far';
    root.traverse(node => {
      const mesh = node as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.userData.layer = layer; mesh.userData.entityId = row.id;
      dress(mesh, this.materials, row.kind);
    });
    root.updateMatrixWorld(true);
    // A moving session draws the buildings of a chunk as one mesh per material, like the instance rows: their
    // surfaces share a material per pattern (buildingMaterials.ts), so a chunk of houses is a few dozen draws.
    if (this.host.view && own && row.kind === 'building') {
      own.traverse(node => {
        const mesh = node as THREE.Mesh;
        if (mesh.isMesh) placed.push({ geometry: mesh.geometry, material: mesh.material as THREE.Material, matrix: mesh.matrixWorld.clone() });
      });
      root.remove(own);
    }
    const [minX, minY, minZ, maxX, maxY, maxZ] = row.bounds;
    const box = new THREE.Box3(new THREE.Vector3(ox + minX, oy + minY, oz + minZ), new THREE.Vector3(ox + maxX, oy + maxY, oz + maxZ));
    return { row, root, placed, heights: row.kind === 'ground' ? groundHeights(root, row) : null, box,
      levels: pair && own ? { rows, far: own, farShadow: null, level: -1 } : null };
  }

  /** The far mesh's shadow twin, once the chunk is prepared: the same geometry and material on the layers of the
   *  sun's cascades (render/environment.ts cascadeLayerMask), so the far mesh casts whether or not it is drawn. */
  private farTwin(drawn: Drawn) {
    const levels = drawn.levels!, group = new THREE.Group();
    group.name = `far-shadow:${drawn.row.id}`;
    levels.far.updateWorldMatrix(true, true);
    levels.far.traverse(node => {
      const mesh = node as THREE.Mesh;
      if (!mesh.isMesh) return;
      const twin = new THREE.Mesh(mesh.geometry, mesh.material);
      twin.name = `${mesh.name}-shadow`; twin.userData = { layer: 'vegetation', entityId: drawn.row.id, shadowBatch: true };
      twin.layers.set(TILE_SHADOW_WHOLE_LAYER); twin.layers.enable(TILE_SHADOW_CHUNK_LAYER);
      twin.castShadow = true; twin.receiveShadow = false;
      twin.matrixAutoUpdate = false; twin.matrix.copy(mesh.matrixWorld); twin.matrixWorld.copy(mesh.matrixWorld);
      group.add(twin);
      mesh.castShadow = false;
    });
    // The twins carry world matrices: their group stays at the world's origin whatever the entity's is.
    group.matrixAutoUpdate = false;
    this.group.add(group);
    levels.farShadow = group;
    this.leveled.add(drawn);
  }

  /** The pools of the near trees, sized for every tree the location holds, filled for the camera (or, before the
   *  first frame, the location's focus), styled and prepared like a chunk. Again after the location changed. */
  private async pool() {
    const host = this.host, pools = this.pools;
    if (!pools) return;
    const parts = new Map<string, Part>();
    for (const drawn of this.leveled) for (const row of drawn.levels!.rows) {
      const part = parts.get(row.key) ?? { key: row.key, geometry: row.geometry, material: row.material, count: 0 };
      part.count += row.matrices.length / 16;
      parts.set(row.key, part);
    }
    for (const pool of pools) pool.build(parts.values());
    const [x, y, z] = this.manifest.focus;
    this.place(this.started ? host.view!.camera.position : new THREE.Vector3(x, y, z));
    for (const pool of pools) {
      await host.style?.(pool.group);
      await host.prepareGraphics?.(pool.group);
      pool.finish();
    }
  }

  /** Every entity with two levels draws the one for its distance from `at`. */
  private place(at: THREE.Vector3) {
    const [shadowing, near] = this.pools!, reach = this.host.view!.treeNear();
    for (const drawn of this.leveled) {
      const levels = drawn.levels!, box = drawn.box, was = levels.level;
      const away = Math.hypot(Math.max(box.min.x - at.x, 0, at.x - box.max.x), Math.max(box.min.z - at.z, 0, at.z - box.max.z));
      const level = away < SHADOW_NEAR + (was === 0 ? LEVEL_SLACK : 0) ? 0 : away < reach + (was === 0 || was === 1 ? LEVEL_SLACK : 0) ? 1 : 2;
      if (level === was) continue;
      levels.level = level;
      shadowing.set(drawn.row.id, level === 0 ? levels.rows : null);
      near.set(drawn.row.id, level === 1 ? levels.rows : null);
      levels.far.visible = level === 2;
      // The far mesh casts in the nearest cascade unless the instances do.
      levels.farShadow?.traverse(twin => { if (level === 0) twin.layers.disable(TILE_SHADOW_CHUNK_LAYER); else twin.layers.enable(TILE_SHADOW_CHUNK_LAYER); });
    }
    shadowing.update(); near.update();
  }

  private readonly frustum = new THREE.Frustum();
  private readonly viewMatrix = new THREE.Matrix4();
  /** Right, left and near: a street camera sees nearly everything between its top and bottom planes. */
  private inView(box: THREE.Box3) {
    const planes = this.frustum.planes;
    for (const index of [0, 1, 5]) {
      const plane = planes[index], n = plane.normal;
      const x = n.x > 0 ? box.max.x : box.min.x, y = n.y > 0 ? box.max.y : box.min.y, z = n.z > 0 ? box.max.z : box.min.z;
      if (n.x * x + n.y * y + n.z * z + plane.constant < 0) return false;
    }
    return true;
  }

  private unhook: (() => void) | null = null;
  private started = false;
  /** The still chunks and entities outside the camera's view this frame, and whether they are hidden right now. */
  private unseen: THREE.Object3D[] = [];
  private hidden = false;
  private stale = true;
  private reveal(on: boolean) {
    if (this.hidden === !on) return;
    for (const object of this.unseen) object.visible = on;
    this.hidden = !on;
  }

  /** Before each render of the scene: what this pass's camera has no use for leaves the scene graph, so the pass does
   *  not walk it. The session's camera: the still chunks and entities outside its view (found once a frame). A
   *  cascade of the sun: every still chunk, whose shadows are the batches beside them. Any other camera: nothing. */
  private beforePass(camera: THREE.Camera) {
    if (this.disposed) return;
    if (camera !== this.host.view!.camera) {
      this.still.visible = (camera.layers.mask & SUN_LAYERS) === 0;
      if (this.still.visible) this.reveal(true);
      return;
    }
    this.still.visible = true;
    if (!this.stale) { this.reveal(false); return; }
    this.stale = false;
    this.reveal(true);
    this.unseen.length = 0;
    this.frustum.setFromProjectionMatrix(this.viewMatrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse), camera.coordinateSystem, camera.reversedDepth);
    for (const chunk of this.chunks.values()) {
      if (chunk.root.parent !== this.still) continue;
      if (!this.inView(chunk.box)) { this.unseen.push(chunk.root); continue; }
      for (const drawn of chunk.members) if (drawn.root.parent && !this.inView(drawn.box)) this.unseen.push(drawn.root);
    }
    this.reveal(false);
  }

  /** Loads the rows of one chunk, a few at a time; a row that fails is named in `failures` and left out. */
  private async loadChunk(rows: readonly LocationEntity[], failures: string[]) {
    const queue = [...rows], members: Drawn[] = [];
    await Promise.all(Array.from({ length: Math.min(LOADS, queue.length) }, async () => {
      for (let row = queue.shift(); row; row = queue.shift()) {
        try { members.push(await this.load(row)); }
        catch (error) { failures.push(`${row.id}: ${error instanceof Error ? error.message : error}`); }
      }
    }));
    return members;
  }

  /** Styles, prepares and draws the entities of one chunk as one model, as the tile streamer does with a tile. */
  private async show(id: string, members: Drawn[]) {
    const host = this.host, root = new THREE.Group(), layer = id.split(':')[0];
    root.name = `chunk:${id}`; root.userData = { layer };
    // An entity that is instance rows only draws in the chunk's merged meshes: in a moving session its empty group
    // stays out of the scene graph, which every pass of every frame walks.
    for (const drawn of members) if (!host.view || drawn.root.children.length) root.add(drawn.root);
    // Instance rows of every member, merged by material and vertex layout.
    const merges = new Map<string, { material: THREE.Material; geometries: THREE.BufferGeometry[] }>();
    for (const drawn of members) for (const item of drawn.placed) {
      const layout = Object.entries(item.geometry.attributes).map(([name, a]) => `${name}:${a.itemSize}:${a.normalized}:${a.array.constructor.name}`).sort();
      const key = `${item.material.uuid}|${!!item.geometry.index}|${layout}`;
      const merge = merges.get(key) ?? { material: item.material, geometries: [] };
      const part = item.geometry.clone().applyMatrix4(item.matrix);
      markVertices(part, item.vertex);
      merge.geometries.push(part);
      merges.set(key, merge);
    }
    for (const { material, geometries } of merges.values()) {
      const geometry = mergeGeometries(geometries, false);
      for (const part of geometries) part.dispose();
      if (!geometry) throw new Error(`instance meshes of ${material.name || material.uuid} do not merge`);
      const mesh = new THREE.Mesh(geometry, material);
      mesh.name = `placed:${material.name || material.uuid}`; mesh.userData = { layer, placed: geometries.length };
      root.add(mesh);
    }
    root.updateMatrixWorld(true);
    await host.style?.(root);
    await host.prepareGraphics?.(root);
    if (root.userData.layer === 'vegetation') root.traverse(node => { if ((node as THREE.Mesh).isMesh) node.castShadow = true; });
    const still = !!host.view && layer !== 'vegetation';
    (still ? this.still : this.group).add(root);
    host.trackModel?.(root, root.name);
    if (still) {
      // The chunk's shadow batch is drawn beside it (the chunk is at the origin of the world, so nothing moves): the
      // sun's passes then skip the chunk. Nothing walks the chunk's matrices after this.
      for (const child of [...root.children]) if (child.userData.shadowBatch) this.group.add(child);
      root.updateMatrixWorld(true);
    }
    const box = new THREE.Box3();
    for (const drawn of members) box.union(drawn.box);
    this.chunks.set(id, { id, root, members, box, layer });
    for (const drawn of members) {
      this.shown.set(drawn.row.id, drawn);
      if (drawn.levels) this.farTwin(drawn);
    }
  }

  private hide(chunk: Chunk) {
    const host = this.host, { root } = chunk;
    // Hidden objects are found again by the next frame's look; this chunk's are gone.
    this.reveal(true); this.unseen.length = 0; this.stale = true;
    host.untrackModel?.(root, root.name);
    host.unstyle?.(root);
    root.removeFromParent();
    this.chunks.delete(chunk.id);
    for (const drawn of chunk.members) {
      this.shown.delete(drawn.row.id);
      this.leveled.delete(drawn);
      for (const pool of this.pools ?? []) pool.set(drawn.row.id, null);
      // The shadow twins share the far mesh's geometry and materials: only their draws are theirs.
      const twins = drawn.levels?.farShadow;
      if (twins) {
        twins.removeFromParent();
        const drop = () => twins.traverse(node => { if ((node as THREE.Mesh).isMesh) disposeDraw(node as THREE.Mesh); });
        if (host.retire) host.retire(drop); else drop();
      }
    }
    const release = () => root.traverse(node => {
      const mesh = node as THREE.Mesh;
      if (!mesh.isMesh) return;
      disposeDraw(mesh);
      releaseGeometry(mesh.geometry);
    });
    if (host.retire) host.retire(release); else release();
  }

  private async install(rows: readonly LocationEntity[]) {
    const queue = [...byChunk(rows)], failures: string[] = [];
    await Promise.all(Array.from({ length: Math.min(LOADS, queue.length) }, async () => {
      for (let next = queue.shift(); next; next = queue.shift()) {
        try { await this.show(next[0], await this.loadChunk(next[1], failures)); }
        catch (error) { failures.push(`chunk ${next[0]}: ${error instanceof Error ? error.message : error}`); }
      }
    }));
    if (failures.length) { this.errors = failures; throw new Error(`Location ${this.manifest.name}: ${failures.length} entities failed: ${failures.slice(0, 3).join('; ')}`); }
  }

  /**
   * Re-reads the manifest and redraws exactly the chunks holding an entity whose content hash changed, that was
   * added or that is gone: the chunk's files load first, then the old chunk leaves and the new one is drawn.
   * Everything else, the camera and the lighting stay. Resolves once the changed chunks are drawn.
   */
  refresh(): Promise<RefreshReport> {
    const run = this.updating.then(() => this.replace());
    this.updating = run.catch(() => {});
    return run;
  }

  private async replace(): Promise<RefreshReport> {
    if (this.disposed) throw new Error('The location has been released');
    const started = performance.now();
    if (this.region) return this.replaceRegion(started);
    const next = readLocation(await LocationWorld.fetchJson(this.host, this.url));
    if (next.name !== this.manifest.name) throw new Error(`Manifest is ${next.name}, not the loaded ${this.manifest.name}`);
    const wanted = new Map(next.entities.map(row => [row.id, row]));
    const changed: string[] = [], added: string[] = [];
    const removed = [...this.shown.keys()].filter(id => !wanted.has(id));
    const stale = new Set(removed.map(id => chunkOf(this.shown.get(id)!.row)));
    for (const row of next.entities) {
      const old = this.shown.get(row.id)?.row;
      if (old?.hash === row.hash && old.role === row.role) continue;
      (old ? changed : added).push(row.id);
      stale.add(chunkOf(row));
      if (old) stale.add(chunkOf(old));
    }
    const rows = byChunk(next.entities), failures: string[] = [];
    for (const id of stale) {
      const members = await this.loadChunk(rows.get(id) ?? [], failures);
      const old = this.chunks.get(id);
      if (old) this.hide(old);
      if (members.length) await this.show(id, members);
    }
    if (failures.length) throw new Error(`Location ${next.name}: ${failures.length} entities failed: ${failures.slice(0, 3).join('; ')}`);
    if (stale.size) await this.pool();
    this.manifest = next;
    this.host.manifest.code = this.data.release;
    for (const entry of this.entries.values()) entry.visualHash = this.data.release;
    this.describe();
    return { revision: next.revision, changed, added, removed, ms: Math.round(performance.now() - started) };
  }

  /** A new revision of a region's index: the chunk objects whose url changed are fetched again and their drawn
   *  chunks redrawn; the drawn set is then chosen again for the camera. */
  private async replaceRegion(started: number): Promise<RefreshReport> {
    const region = this.region!, next = readRegion(await LocationWorld.fetchJson(this.host, this.url));
    const before = new Map([...this.shown].map(([id, drawn]) => [id, drawn.row.hash]));
    for (const [key, chunk] of Object.entries(region.index.chunks)) {
      if (next.chunks[key]?.url === chunk.url || !region.rows.has(key)) continue;
      region.rows.delete(key);
      const [i, j] = key.split('_');
      for (const drawn of [...this.chunks.values()]) if (drawn.id.endsWith(`:${i}_${j}`)) this.hide(drawn);
    }
    region.index = next;
    Object.assign(this.manifest, { revision: next.revision, built: next.built, collision: next.collision, roads: next.roads, stats: next.stats });
    const at = region.at ?? { x: this.manifest.focus[0], z: this.manifest.focus[2] };
    await this.stream(at.x, at.z);
    await this.pool();
    this.host.manifest.code = this.data.release;
    for (const entry of this.entries.values()) entry.visualHash = this.data.release;
    const changed: string[] = [], added: string[] = [];
    for (const [id, drawn] of this.shown) if (!before.has(id)) added.push(id); else if (before.get(id) !== drawn.row.hash) changed.push(id);
    const removed = [...before.keys()].filter(id => !this.shown.has(id));
    return { revision: next.revision, changed, added, removed, ms: Math.round(performance.now() - started) };
  }

  /** One drawn entity: its manifest row and three.js object (dev tools and the workbench). */
  entity(id: string) {
    const drawn = this.shown.get(id);
    return drawn ? { row: drawn.row, object: drawn.root } : null;
  }

  /** Drawn content by kind: entities, mesh and instance triangles, bytes of mesh files. */
  census() {
    const kinds: Record<string, { entities: number; triangles: number; instances: number; bytes: number }> = {};
    const triangles = (geometry: THREE.BufferGeometry) => (geometry.index?.count ?? geometry.attributes.position.count) / 3;
    for (const { row, root, placed, levels } of this.shown.values()) {
      const kind = kinds[row.kind] ??= { entities: 0, triangles: 0, instances: 0, bytes: 0 };
      kind.entities++; kind.bytes += row.bytes ?? 0;
      kind.instances += row.instances.reduce((sum, item) => sum + item.transforms.length, 0);
      for (const item of placed) kind.triangles += triangles(item.geometry);
      for (const item of levels?.rows ?? []) kind.triangles += triangles(item.geometry) * item.matrices.length / 16;
      root.traverse(node => {
        const mesh = node as THREE.Mesh;
        if (mesh.isMesh && !mesh.userData.shadowBatch) kind.triangles += triangles(mesh.geometry);
      });
    }
    return { name: this.manifest.name, revision: this.manifest.revision, entities: this.shown.size, kinds };
  }

  preload(_points: readonly { x: number; z: number }[], _radius: number, _boot = false, _view?: unknown) { return () => {}; }
  update(x: number, z: number, _now?: number, _motion?: { x: number; z: number }, _scenery = true, _y?: number) {
    if (this.region && !this.disposed && Number.isFinite(x) && Number.isFinite(z)) this.follow(x, z);
    if (!this.pools || this.disposed) return;
    this.started = true; this.stale = true;
    this.place(this.host.view!.camera.position);
  }
  setRouteHint(_points: readonly { x: number; z: number }[] | null) {}
  setVisualPass(_pass: 'auto' | 'first' | 'full') {}
  /** A location is a query, not a boundary: any camera may look at it. */
  contains(_x: number, _z: number) { return !this.disposed; }
  progressAt(x: number, z: number) {
    // A region whose drawn set was chosen elsewhere, or is being drawn, is not loaded here yet.
    const pending = this.region && !this.settledAt(x, z) ? 1 : 0;
    return { loaded: this.shown.size, total: this.manifest.entities.length + pending, errors: this.errors };
  }
  /** Whether a region's drawn set is the one for a camera at (x, z) and fully drawn (always true for a location). */
  private settledAt(x: number, z: number) {
    const region = this.region;
    return !region || (!region.busy && !!region.at && Math.hypot(x - region.at.x, z - region.at.z) < REGION_STEP);
  }
  resolvedAt(_x: number, _z: number) { return !this.disposed; }
  streetsAt(x: number, z: number) { return Number.isFinite(this.groundBelow(x, z)); }
  detailedAt(x: number, z: number) { return this.settledAt(x, z) && this.shown.size === this.manifest.entities.length; }
  collisionComing(_id: string) { return false; }
  loadedTiles() { return [...this.entries.keys()]; }
  drawn() { return this.loadedTiles(); }
  roadGraphs(): RoadGraphJson[] { return this.roads; }
  manifestUrl(_id: string) { return this.url; }
  /** The ground cell's surface height near a point (binned mesh heights: stills need it for shadows and placement). */
  groundBelow(x: number, z: number, _referenceY?: number) {
    const cell = this.shown.get(`ground/c_${Math.floor(x / 62.5)}_${Math.floor(z / 62.5)}`)?.heights;
    if (!cell) return NaN;
    const i = Math.min(cell.nx - 1, Math.max(0, Math.floor((x - cell.x0) / GROUND_BINS)));
    const j = Math.min(cell.nz - 1, Math.max(0, Math.floor((z - cell.z0) / GROUND_BINS)));
    return cell.y[j * cell.nx + i];
  }
  viewStatus(_x: number, _z: number) {
    const ready = this.detailedAt(0, 0);
    return { ready, near: ready ? [...this.entries].map(([id, e]) => ({ id, hash: e.visualHash, ready })) : [], scenery: this.screenshotScenery.progress };
  }
  streamSnapshot() { return { ...this.stats.snapshot(performance.now(), [], 0), previews: 0, outage: false }; }
  coverageSnapshot(x: number, z: number, r = 4) {
    const i0 = Math.floor(x / 250) - r, j0 = Math.floor(z / 250) - r, n = 2 * r + 1;
    let cells = '';
    for (let j = j0; j < j0 + n; j++) for (let i = i0; i < i0 + n; i++) cells += this.entries.has(`t0_${i}_${j}`) ? 'F' : '.';
    return { i0, j0, n, cells };
  }
  async benchInstall(_id: string, _stage: 'preview' | 'full', _x: number, _z: number) {
    return { state: 'draft', preview: false, full: !this.disposed, error: null, retryInMs: 0 };
  }
  memoryParts() {
    return { full: this.disposed ? [] : [this.group], cached: [], previews: [], previewFragments: 0, roads: 0,
      releasedBytes: 0, releases: 0, gpuResidency: { measuredMiB: Math.round(modelGpuBytes(this.group, rendererResidency(this.host.renderer)) / 2 ** 20), limitMiB: null, over: false },
      previewJsonBytes: () => 0, roadsJsonBytes: () => 0, fragmentCache: { bytes: 0, count: 0 }, buildingTemplates: async () => [] };
  }
  leaveCity() {
    if (this.disposed) return;
    this.disposed = true;
    for (const chunk of [...this.chunks.values()]) this.hide(chunk);
    this.unhook?.();
    for (const pool of this.pools ?? []) pool.release();
    for (const entry of this.entries.values()) for (const cell of entry.collision.keys()) { delete this.host.manifest.collision[cell]; this.host.unloadCollision(cell); }
    this.group.removeFromParent();
    this.entries.clear();
  }
}

/** Mean mesh height per 5 m bin of a ground cell, in world metres. */
function groundHeights(root: THREE.Group, row: LocationEntity) {
  const [minX, , minZ, maxX, , maxZ] = row.bounds;
  const nx = Math.max(1, Math.ceil((maxX - minX) / GROUND_BINS)), nz = Math.max(1, Math.ceil((maxZ - minZ) / GROUND_BINS));
  const sum = new Float32Array(nx * nz), count = new Uint32Array(nx * nz), point = new THREE.Vector3();
  let total = 0, seen = 0;
  root.traverse(node => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh || (mesh as THREE.InstancedMesh).isInstancedMesh) return;
    const position = mesh.geometry.getAttribute('position');
    for (let k = 0; k < position.count; k++) {
      point.fromBufferAttribute(position, k).applyMatrix4(mesh.matrixWorld);
      const i = Math.min(nx - 1, Math.max(0, Math.floor((point.x - row.origin[0] - minX) / GROUND_BINS)));
      const j = Math.min(nz - 1, Math.max(0, Math.floor((point.z - row.origin[2] - minZ) / GROUND_BINS)));
      sum[j * nx + i] += point.y; count[j * nx + i]++; total += point.y; seen++;
    }
  });
  if (!seen) return null;
  const y = Float32Array.from(sum, (value, k) => count[k] ? value / count[k] : total / seen);
  return { x0: row.origin[0] + minX, z0: row.origin[2] + minZ, nx, nz, y };
}
