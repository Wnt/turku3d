// The loader of the world pipeline's location manifest (docs/design/world-pipeline.md, sections 5 and 8).
//
// Entities are drawn in chunks: the entities of one renderer layer whose origins fall in the same 250 m square are
// styled, prepared and drawn as one model, as the game does with a tile, so the cost of a location follows its
// content and not its entity count (2,000 entities as 2,000 models did not finish loading in ten minutes). Instance
// rows are drawn as merged geometry per chunk and material: an InstancedMesh per entity and asset mesh cost about
// twenty shader builds each (3,000 of them at one location), while merged meshes share the builds of their material.
// A changed entity redraws its chunk in the running client while the camera, the lighting and every other chunk
// stay. Stills only for now: a location has no collision, so it draws in the QA camera mode (?world=NAME&cam=...),
// not in play; an object the player moves will leave the merged mesh as its own object then (the manifest row has
// its id, behaviour, collision and mass).
import * as THREE from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { disposeDraw } from '../../render/sharedMaterials';
import { releaseGeometry } from '../modelGeometry';
import { modelGpuBytes, rendererResidency } from '../gpuBytes';
import { StreamStats } from '../streamStats';
import type { Entry } from '../coarseContracts';
import type { PreparedWorld, PreparedWorldHost } from '../preparedWorld';
import type { RoadGraphJson } from '../../gameplay/roadGraph';
import type { PreparedLighting } from '../../render/lights/preparedLighting';
import { readLocation, type LocationEntity, type LocationManifest } from './manifest';
import { familyMaterial } from './materials';
import './families/ground';
import './families/building';

/** The store root as the dev server and a static host serve it. */
const STORE = 'world/';
/** Renderer layers by kind: which batching, shadow and reflection rules apply (world/staticBatch.ts, shadowBatch.ts). */
// Trees and planting are vegetation: millions of instanced leaf-card triangles that must not be copied into an
// entity's merged shadow batch (world/shadowBatch.ts skips the layer); they cast their own shadows instead.
const LAYERS: Record<string, string> = { ground: 'ground', building: 'buildings', trees: 'vegetation', planting: 'vegetation' };
const GROUND_BINS = 5;
const LOADS = 8;
/** The side of the square whose entities are prepared as one model, in metres. */
const CHUNK = 250;

/** One asset mesh at one instance row, in world space. */
interface Placed { geometry: THREE.BufferGeometry; material: THREE.Material; matrix: THREE.Matrix4 }
interface Drawn { row: LocationEntity; root: THREE.Group; placed: Placed[]; heights: { x0: number; z0: number; nx: number; nz: number; y: Float32Array } | null }
interface Chunk { id: string; root: THREE.Group; members: Drawn[] }
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
  scheduling = true;
  readonly visualPass = 'full';
  readonly comparisonStatus = 'Location manifest';
  readonly releasedBytes = 0;
  status = '';
  /** What capture tools read as the drawn scene: the location and its manifest revision. */
  get data() { return { id: `world:${this.manifest.name}`, release: `r${this.manifest.revision}` }; }
  private shown = new Map<string, Drawn>();
  private chunks = new Map<string, Chunk>();
  private assets = new Map<string, Promise<THREE.Group>>();
  private materials = new Map<string, THREE.Material>();
  private loader = new GLTFLoader();
  private group = new THREE.Group();
  private errors: string[] = [];
  private updating: Promise<unknown> = Promise.resolve();
  private disposed = false;

  private constructor(private host: PreparedWorldHost, public manifest: LocationManifest, private url: string) {
    this.lighting = { id: `world:${manifest.name}`, lamps: [], footprint: null };
    this.group.name = `world:${manifest.name}`;
  }

  static async start(host: PreparedWorldHost, name: string): Promise<LocationWorld> {
    const url = new URL(`${STORE}locations/${name}.json`, host.pageUrl ?? location.href).href;
    const manifest = await LocationWorld.fetchManifest(host, url);
    if (manifest.origin.some((n, i) => n !== host.manifest.origin_en[i])) throw new Error('Location origin differs from the active world');
    const world = new LocationWorld(host, manifest, url);
    host.scene.add(world.group);
    host.manifest.code = world.data.release;
    await world.install(manifest.entities);
    // One entry stands for the location where the tile streamer's consumers look for loaded tiles.
    const [x, , z] = manifest.focus;
    world.entries.set(`t0_${Math.floor(x / 250)}_${Math.floor(z / 250)}`,
      { state: 'draft', model: world.group, collision: new Map(), nextPoll: Infinity, pass: 'full', visualHash: world.data.release });
    world.describe();
    return world;
  }

  private static async fetchManifest(host: PreparedWorldHost, url: string) {
    const response = await (host.fetch ?? fetch)(url, { cache: 'no-store', signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`Location manifest: HTTP ${response.status} (${url})`);
    return readLocation(await response.json());
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
      asset = this.loader.loadAsync(this.storeUrl(path)).then(gltf => { gltf.scene.updateMatrixWorld(true); return gltf.scene; });
      this.assets.set(path, asset);
    }
    return asset;
  }

  /** Loads one entity's meshes and instances into a detached group at its origin. */
  private async load(row: LocationEntity): Promise<Drawn> {
    const root = new THREE.Group(), layer = layerOf(row);
    root.name = `entity:${row.id}`; root.position.fromArray(row.origin);
    root.userData = { entityId: row.id, kind: row.kind, role: row.role, hash: row.hash, layer };
    if (row.url) root.add((await this.loader.loadAsync(this.storeUrl(row.url))).scene);
    const placed: Placed[] = [], [ox, oy, oz] = row.origin;
    const turn = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0);
    for (const item of row.instances) {
      const asset = await this.asset(item.asset);
      asset.traverse(node => {
        const source = node as THREE.Mesh;
        if (!source.isMesh) return;
        const material = familyMaterial(Array.isArray(source.material) ? source.material[0] : source.material, this.materials, row.kind);
        for (const [x, y, z, yaw, sx, sy, sz] of item.transforms) placed.push({ geometry: source.geometry, material,
          matrix: new THREE.Matrix4().compose(new THREE.Vector3(ox + x, oy + y, oz + z), turn.setFromAxisAngle(up, yaw), new THREE.Vector3(sx, sy, sz)).multiply(source.matrixWorld) });
      });
    }
    root.traverse(node => {
      const mesh = node as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.userData.layer = layer; mesh.userData.entityId = row.id;
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map(m => familyMaterial(m, this.materials, row.kind)) : familyMaterial(mesh.material, this.materials, row.kind);
    });
    root.updateMatrixWorld(true);
    return { row, root, placed, heights: row.kind === 'ground' ? groundHeights(root, row) : null };
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
    for (const drawn of members) root.add(drawn.root);
    // Instance rows of every member, merged by material and vertex layout.
    const merges = new Map<string, { material: THREE.Material; geometries: THREE.BufferGeometry[] }>();
    for (const drawn of members) for (const item of drawn.placed) {
      const layout = Object.entries(item.geometry.attributes).map(([name, a]) => `${name}:${a.itemSize}:${a.normalized}:${a.array.constructor.name}`).sort();
      const key = `${item.material.uuid}|${!!item.geometry.index}|${layout}`;
      const merge = merges.get(key) ?? { material: item.material, geometries: [] };
      merge.geometries.push(item.geometry.clone().applyMatrix4(item.matrix));
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
    this.group.add(root);
    host.trackModel?.(root, root.name);
    this.chunks.set(id, { id, root, members });
    for (const drawn of members) this.shown.set(drawn.row.id, drawn);
  }

  private hide(chunk: Chunk) {
    const host = this.host, { root } = chunk;
    host.untrackModel?.(root, root.name);
    host.unstyle?.(root);
    root.removeFromParent();
    this.chunks.delete(chunk.id);
    for (const drawn of chunk.members) this.shown.delete(drawn.row.id);
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
    const next = await LocationWorld.fetchManifest(this.host, this.url);
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
    this.manifest = next;
    this.host.manifest.code = this.data.release;
    for (const entry of this.entries.values()) entry.visualHash = this.data.release;
    this.describe();
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
    for (const { row, root, placed } of this.shown.values()) {
      const kind = kinds[row.kind] ??= { entities: 0, triangles: 0, instances: 0, bytes: 0 };
      kind.entities++; kind.bytes += row.bytes ?? 0;
      kind.instances += row.instances.reduce((sum, item) => sum + item.transforms.length, 0);
      for (const item of placed) kind.triangles += triangles(item.geometry);
      root.traverse(node => {
        const mesh = node as THREE.Mesh;
        if (mesh.isMesh && !mesh.userData.shadowBatch) kind.triangles += triangles(mesh.geometry);
      });
    }
    return { name: this.manifest.name, revision: this.manifest.revision, entities: this.shown.size, kinds };
  }

  preload(_points: readonly { x: number; z: number }[], _radius: number, _boot = false, _view?: unknown) { return () => {}; }
  update(_x: number, _z: number, _now?: number, _motion?: { x: number; z: number }, _scenery = true, _y?: number) {}
  setRouteHint(_points: readonly { x: number; z: number }[] | null) {}
  setVisualPass(_pass: 'auto' | 'first' | 'full') {}
  /** A location is a query, not a boundary: any camera may look at it. */
  contains(_x: number, _z: number) { return !this.disposed; }
  progressAt(_x: number, _z: number) {
    return { loaded: this.shown.size, total: this.manifest.entities.length, errors: this.errors };
  }
  resolvedAt(_x: number, _z: number) { return !this.disposed; }
  streetsAt(x: number, z: number) { return Number.isFinite(this.groundBelow(x, z)); }
  detailedAt(_x: number, _z: number) { return this.shown.size === this.manifest.entities.length; }
  collisionComing(_id: string) { return false; }
  loadedTiles() { return [...this.entries.keys()]; }
  drawn() { return this.loadedTiles(); }
  roadGraphs(): RoadGraphJson[] { return []; }
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
