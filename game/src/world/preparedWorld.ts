import * as THREE from 'three/webgpu';
import { readPreparedScene, buildPreparedModel, type PreparedScene } from './preparedScene';
import type { CoarseHost, Entry } from './coarseContracts';
import { StreamStats } from './streamStats';
import type { RoadGraphJson } from '../gameplay/roadGraph';
import type { TileLightsField } from '../render/lights/tileLights';

/** Prepared context is part of this release, already resident with its pilot. No scenery service or second world is consulted. */
class PreparedContext {
  radius = 0;
  heading: number | null = null;
  progress = { ready: true, loaded: 0, total: 0, pending: 0, errors: 0, missing: 0, cataloguePending: false };
  setRadius(_radius: number, _view?: unknown) {}
  follow(_camera: THREE.Camera) {}
  setView(_view: unknown, _now: number) {}
}

/** A complete compiled release, installed before gameplay starts. This bounded pilot has no background generator. */
export class PreparedWorld {
  readonly entries = new Map<string, Entry>();
  readonly screenshotScenery = new PreparedContext();
  readonly stats = new StreamStats();
  onMapChange: (() => void) | null = null;
  retainedStarts: readonly { x: number; z: number }[] = [];
  scheduling = true;
  readonly visualPass = 'full';
  readonly comparisonStatus = 'Prepared scene';
  readonly releasedBytes = 0;
  status = '';
  private disposed = false;
  private constructor(private host: CoarseHost, readonly data: PreparedScene, private url: string, private model: THREE.Group) {}

  static async start(host: CoarseHost): Promise<PreparedWorld> {
    const page = host.pageUrl ?? location.href;
    const url = new URL(`${import.meta.env.BASE_URL}scenes/office.scene.json`, page).href;
    const fetcher = host.fetch ?? fetch;
    const response = await fetcher(url, { signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`Prepared scene: HTTP ${response.status}`);
    const data = readPreparedScene(await response.json());
    if (data.origin.some((n, i) => n !== host.manifest.origin_en[i])) throw new Error('Prepared scene origin differs from the active world');
    const cells = await Promise.all(Object.entries(data.collision).map(async ([id, entry]) => {
      const cell = await fetcher(new URL(entry.url, url), { signal: AbortSignal.timeout(15000) });
      if (!cell.ok) throw new Error(`Prepared collision ${id}: HTTP ${cell.status}`);
      const buffer = await cell.arrayBuffer();
      if (buffer.byteLength !== entry.bytes) throw new Error(`Prepared collision ${id}: wrong byte count`);
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', buffer)), b => b.toString(16).padStart(2, '0')).join('');
      if (digest !== entry.sha256) throw new Error(`Prepared collision ${id}: hash mismatch`);
      await host.checkCollision(buffer.slice(0));
      return { id, entry: { ...entry, url: new URL(entry.url, url).href }, buffer };
    }));
    const model = buildPreparedModel(data), world = new PreparedWorld(host, data, url, model);
    try {
      await host.prepareGraphics?.(model);
      const id = `t0_${Math.floor(data.spawn.position[0] / 250)}_${Math.floor(data.spawn.position[2] / 250)}`;
      const collision = new Map(cells.map(c => [c.id, c.entry]));
      const h = [Math.min(...cells.map(c => c.entry.h[0])), Math.max(...cells.map(c => c.entry.h[1]))] as [number, number];
      world.entries.set(id, { state: 'draft', model, collision, nextPoll: Infinity, pass: 'full', visualHash: data.release, h, lights: preparedLights(data) });
      for (const c of cells) { host.manifest.collision[c.id] = c.entry; host.retainCollision?.(c.id, c.entry, c.buffer); }
      host.manifest.spawn = data.spawn; host.manifest.code = data.release;
      host.scene.add(model); host.trackModel?.(model, id);
      world.status = `${data.id} · ${data.meshes.length} prepared meshes`;
      return world;
    } catch (error) { world.leaveCity(); throw error; }
  }

  preload(_points: readonly { x: number; z: number }[], _radius: number, _boot = false, _view?: unknown) { return () => {}; }
  update(_x: number, _z: number, _now?: number, _motion?: { x: number; z: number }, _scenery = true, _y?: number) {}
  setRouteHint(_points: readonly { x: number; z: number }[] | null) {}
  setVisualPass(_pass: 'auto' | 'first' | 'full') {}
  progressAt(_x: number, _z: number) { return { loaded: this.disposed ? 0 : 1, total: 1, errors: [] as string[] }; }
  resolvedAt(_x: number, _z: number) { return !this.disposed; }
  streetsAt(_x: number, _z: number) { return !this.disposed; }
  detailedAt(_x: number, _z: number) { return !this.disposed; }
  collisionComing(_id: string) { return false; }
  loadedTiles() { return [...this.entries.keys()]; }
  drawn() { return this.loadedTiles(); }
  roadGraphs(): RoadGraphJson[] { return []; }
  manifestUrl(_id: string) { return this.url; }
  groundBelow(x: number, z: number) {
    const b = this.data.bounds;
    return x >= b[0] && x <= b[2] && z >= b[1] && z <= b[3] ? Math.min(...[...this.entries.values()].map(e => e.h![0])) : NaN;
  }
  viewStatus(_x: number, _z: number) { return { ready: !this.disposed, near: [...this.entries].map(([id, e]) => ({ id, hash: e.visualHash, ready: true })), scenery: this.screenshotScenery.progress }; }
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
    return { full: this.disposed ? [] : [this.model], cached: [], previews: [], previewFragments: 0, roads: 0,
      releasedBytes: 0, releases: 0, gpuResidency: { measuredMiB: 0, limitMiB: null, over: false },
      previewJsonBytes: () => 0, roadsJsonBytes: () => 0, fragmentCache: { bytes: 0, count: 0 }, buildingTemplates: async () => [] };
  }
  leaveCity() {
    if (this.disposed) return;
    this.disposed = true; this.model.removeFromParent();
    for (const [id, e] of this.entries) {
      this.host.untrackModel?.(this.model, id);
      for (const cell of e.collision.keys()) { delete this.host.manifest.collision[cell]; this.host.unloadCollision(cell); }
    }
    this.entries.clear();
    const release = () => this.model.traverse(o => {
      if (!(o instanceof THREE.Mesh)) return;
      o.geometry.dispose();
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) m.dispose();
    });
    if (this.host.retire) this.host.retire(release); else release();
  }
}

function preparedLights(scene: PreparedScene): TileLightsField {
  const bytes = new Uint8Array(scene.emitters.length * 16), view = new DataView(bytes.buffer);
  scene.emitters.forEach((e, i) => {
    const at = i * 16;
    e.position.forEach((n, axis) => view.setFloat32(at + axis * 4, n, true));
    e.color.forEach((n, axis) => view.setUint8(at + 12 + axis, Math.round(Math.min(1, n * e.intensity / 8) * 255)));
    view.setUint8(at + 15, 128);
  });
  return { format: 'turku3d-lights', version: 1, count: scene.emitters.length, posts: 0, street: scene.emitters.length,
    data: btoa(Array.from(bytes, b => String.fromCharCode(b)).join('')) };
}
