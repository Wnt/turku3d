// Ambient walkers (street-richness Phase 4): shared deterministic population (population.ts) on the resident tiles'
// FOOT network (network.ts), drawn locally around the camera (ring.ts) with baked vertex animation (visuals.ts).
import * as THREE from 'three/webgpu';
import { EVENT, parseRoadGraph, type RoadGraphJson } from '../../gameplay/roadGraph';
import type { TrafficPedestrian } from '../../traffic/pedestrians';
import type { QualityTier } from '../../render/quality';
import type { RayFilter, RayHit } from '../../physics/protocol';
import type { V3 } from '../../core/origin';
import { loadWalkerAsset, type WalkerAsset } from './asset';
import { GroundHeights } from './heights';
import { buildWalkNetwork, hash32, type WalkNetwork } from './network';
import { Population, type BirthClock, type Walker } from './population';
import { RING, Ring, inView, type RingView } from './ring';
import { WalkerMeshes, type WalkerInstance } from './visuals';
import { humanAppearance } from './appearance';
import { REACT, Reactions, type Threat } from './reactions';
import { populationFocus } from '../populationFocus';
import { CYCLE_EPOCH } from '../../core/dayCycle';

export { walkersNear } from './population';
export { buildWalkNetwork } from './network';
export type { Threat as WalkerThreat } from './reactions';

/** Drawn walkers per quality tier (street-richness plan §7); `?walkers=N` overrides. Phones stay at zero. */
export const WALKER_CAPS: Record<QualityTier, number> = { mobile: 0, low: 10, medium: 60, high: 100 };
const MAX_WALKERS = 200;
export function walkerCap(quality: QualityTier, override: number | null): number {
  return Math.max(0, Math.min(MAX_WALKERS, override ?? WALKER_CAPS[quality]));
}

type Raycast = (origin: V3, dir: V3, maxToi: number, filter: RayFilter) => Promise<RayHit | null>;
interface WalkerOptions {
  cap: number;
  raycast: Raycast;
  /** Shared UTC milliseconds (the day clock synchronised to the multiplayer server). */
  now: () => number;
  /** The clock hour births sample (population.ts BirthClock); the shared cycle when absent. */
  birthHour?: BirthClock;
  /** Resident road graphs (CoarseTiles.roadGraphs). */
  graphs: () => readonly RoadGraphJson[];
  /** Every player this client knows of (local and remote, on foot or driving): drawn walkers react to them. */
  threats?: () => readonly Threat[];
  /** Traffic clock seconds now (the drivers' signal plan; 0 before the first worker state): walkers wait for the
   *  walk phase on that clock. */
  trafficTime?: () => number;
}

const systems: Set<Walkers> = import.meta.hot?.data.systems ?? new Set<Walkers>();
/** Walkers drawn by this client right now (Phase 0 richness scorecard). */
export function liveWalkerCount(): number { let n = 0; for (const s of systems) n += s.count; return n; }

const SCAN_MS = 400, GRAPH_POLL_MS = 1000, RAYS_PER_FRAME = 24, YAW_RATE = 8, IDLE_SECONDS = 2.5;
/** A birth closer than RING.popFree in view stays hidden until it is out of view; a drawn walker whose life ends in
 *  view keeps walking its continued route until it is out of view or this far (m). */
const BIRTH_SEEN = 1, LINGER_UNTIL = 80;
/** Moving walkers keep this clearance from other drawn walkers (m, centre to centre) and look this far for them. */
const CROWD_PERSONAL = 0.6, CROWD_REACH = 2.5;

export class Walkers {
  readonly group = new THREE.Group();
  private meshes: WalkerMeshes | null = null;
  private asset: WalkerAsset | null = null;
  private network: WalkNetwork | null = null;
  private population: Population | null = null;
  private graphs: readonly RoadGraphJson[] = [];
  private trafficCrossings: { x: number; z: number }[] = [];
  private crossingUsers: Walker[] = [];
  private heights: GroundHeights;
  private ring = new Ring<Walker>();
  private reactions = new Reactions();
  private wide: Walker[] = [];
  /** Births hidden from view, and drawn walkers past the end of their life (rendering only). */
  private hidden = new Set<number>();
  private lingering = new Map<number, Walker>();
  private lastScan = -Infinity;
  private lastGraphPoll = -Infinity;
  private request = 0;
  private assetRequest = 0;
  private disposed = false;
  private worker: Worker | null = null;
  private camera = new THREE.Vector3();
  private forward = new THREE.Vector3();
  count = 0;
  buildMs = 0;
  /** Last frame's update and last wide scan (every SCAN_MS), main thread. */
  updateMs = 0;
  scanMs = 0;
  private poses: { id: number; x: number; y: number; z: number; phase: number; fade: number }[] = [];

  constructor(parent: THREE.Object3D, private options: WalkerOptions) {
    this.group.name = 'walkers';
    parent.add(this.group);
    this.heights = new GroundHeights(options.raycast);
    systems.add(this);
  }

  async load(url: string) {
    if (this.options.cap <= 0 || this.disposed) return;
    const token = ++this.assetRequest;
    const asset = await loadWalkerAsset(url);
    if (this.disposed || token !== this.assetRequest) { asset.albedo?.dispose(); return; }
    const meshes = new WalkerMeshes(asset, this.options.cap + RING.margin);
    this.meshes?.dispose();
    this.asset = asset; this.meshes = meshes;
    this.group.add(meshes.group);
    this.ring.clear(); this.lastScan = -Infinity;
  }

  /** Rebuilds the network when the set of resident graphs changed (identity, not load order), in a worker when the
   *  page has one; the previous network keeps walking until the new one arrives. */
  private syncGraphs() {
    const graphs = this.options.graphs();
    if (graphs.length === this.graphs.length && graphs.every(g => this.graphs.includes(g))) return;
    this.graphs = [...graphs];
    const crossings = new Map<string, { x: number; z: number }>();
    for (const json of graphs) for (const edge of parseRoadGraph(json).edges) for (const event of edge.events) {
      if ((event.code & 15) !== EVENT.CROSSING) continue;
      let i = 1;
      while (i < edge.cumulative.length - 1 && edge.cumulative[i] < event.s) i++;
      const f = Math.max(0, Math.min(1, (event.s - edge.cumulative[i - 1]) / Math.max(0.01, edge.cumulative[i] - edge.cumulative[i - 1])));
      const x = edge.points[2 * i - 2] + (edge.points[2 * i] - edge.points[2 * i - 2]) * f;
      const z = edge.points[2 * i - 1] + (edge.points[2 * i + 1] - edge.points[2 * i - 1]) * f;
      crossings.set(`${Math.round(x)},${Math.round(z)}`, { x, z });
    }
    this.trafficCrossings = [...crossings.values()];
    const id = ++this.request;
    if (typeof Worker === 'undefined') { this.install(buildWalkNetwork(this.graphs), 0); return; }
    this.worker ??= this.startWorker();
    this.worker.postMessage({ id, parts: this.graphs });
  }

  private startWorker() {
    const worker = new Worker(new URL('./network.worker.ts', import.meta.url), { type: 'module', name: 'walker-network' });
    worker.onmessage = ({ data }: MessageEvent<{ id: number; network?: WalkNetwork; ms?: number; error?: string }>) => {
      if (data.error) console.warn(`[walkers] network: ${data.error}`);
      else if (data.id === this.request && data.network) this.install(data.network, data.ms ?? 0);
    };
    return worker;
  }

  private install(network: WalkNetwork, ms: number) {
    this.network = network;
    this.population = new Population(network, this.options.birthHour);
    this.heights.retain(new Set(network.chains.map(c => c.id)));
    this.buildMs = ms;
    this.lastScan = -Infinity;
  }

  /** Start a reproducible replay on the current road generation. Invalidate outstanding worker results so
   * network arrival order cannot change the population after the reset. Height samples remain reusable. */
  reset() {
    this.syncGraphs(); ++this.request;
    this.install(buildWalkNetwork(this.graphs), 0);
    this.ring.clear(); this.wide = []; this.poses = []; this.reactions.clear(); this.hidden.clear(); this.lingering.clear();
    this.lastScan = this.lastGraphPoll = -Infinity;
  }

  /** Once per frame after the camera moved. */
  update(dt: number, camera: THREE.PerspectiveCamera, frameNow: number) {
    if (!this.meshes || !this.asset) return;
    const start = performance.now();
    if (frameNow - this.lastGraphPoll >= GRAPH_POLL_MS) { this.lastGraphPoll = frameNow; this.syncGraphs(); }
    const population = this.population;
    if (!population) { this.meshes.update([]); this.count = 0; return; }
    const utc = this.options.now();
    const traffic = this.options.trafficTime?.() ?? 0;
    if (traffic > 0) population.setSignalOffset(traffic - (utc - CYCLE_EPOCH) / 1000);
    camera.getWorldPosition(this.camera);
    camera.getWorldDirection(this.forward);
    const flat = Math.hypot(this.forward.x, this.forward.z) || 1;
    const halfH = Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * camera.aspect);
    const focus = populationFocus(this.camera, this.forward, this.graphs);
    const view: RingView = { ...focus, fx: this.forward.x / flat, fz: this.forward.z / flat,
      cosHalf: Math.cos(Math.min(Math.PI, halfH + THREE.MathUtils.degToRad(12))) };
    if (frameNow - this.lastScan >= SCAN_MS) {
      this.lastScan = frameNow;
      const scan = performance.now();
      this.wide = population.near(view.x, view.z, RING.inView + RING.keep + 15, utc);
      this.scanMs = performance.now() - scan;
    }
    const candidates: Walker[] = [];
    const sight = (w: Walker, reach: number) => inView(view, w.x, w.z) && Math.hypot(w.x - view.x, w.z - view.z) < reach;
    for (const old of this.lingering.values()) if (!this.wide.includes(old)) this.wide.push(old);
    const hidden = new Set<number>();
    for (const old of this.wide) {
      // A drawn walker never fades out in sight at the end of its life: it walks on, out of view, then goes.
      const drawnNow = this.ring.has(old.id);
      const walker = population.at(old, utc, drawnNow);
      if (!walker) { this.lingering.delete(old.id); continue; }
      if (walker.age >= walker.life) {
        if (!drawnNow || !sight(walker, LINGER_UNTIL)) { this.lingering.delete(old.id); continue; }
        this.lingering.set(old.id, old);
      }
      // A birth in plain sight is not drawn until the walker is out of view.
      if ((walker.age < BIRTH_SEEN && !drawnNow && sight(walker, RING.popFree)) || this.hidden.has(walker.id) && sight(walker, RING.popFree + 10)) {
        hidden.add(walker.id); continue;
      }
      if (!Number.isFinite(walker.y)) {
        const chain = population.network.chains[walker.chain];
        const y = this.heights.height(chain, walker.s);
        if (y === null) { this.heights.request(chain, frameNow); continue; }
        walker.y = y;
      }
      candidates.push(walker);
    }
    this.hidden = hidden;
    const drawn = this.ring.select(candidates, view, this.options.cap, dt);
    const instances: WalkerInstance[] = [];
    this.poses = [];
    const turn = 1 - Math.exp(-dt * YAW_RATE);
    const threats = this.options.threats?.() ?? [], seconds = frameNow / 1000;
    // Other drawn walkers, by 2.5 m cell: a moving walker steps round people standing or coming the other way.
    const crowd = new Map<string, Walker[]>();
    for (const { walker } of drawn) {
      const key = `${Math.floor(walker.x / CROWD_REACH)},${Math.floor(walker.z / CROWD_REACH)}`;
      const list = crowd.get(key); if (list) list.push(walker); else crowd.set(key, [walker]);
    }
    const local: Threat[] = [];
    for (const entry of drawn) {
      const w = entry.walker, look = humanAppearance(w.id);
      const variant = this.asset.variants[look.variant];
      const scale = w.scale;
      local.length = 0; local.push(...threats);
      if (w.moving) {
        const cx = Math.floor(w.x / CROWD_REACH), cz = Math.floor(w.z / CROWD_REACH);
        for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) for (const o of crowd.get(`${cx + i},${cz + j}`) ?? []) {
          if (o.lifeId === w.lifeId || Math.abs(o.x - w.x) > CROWD_REACH || Math.abs(o.z - w.z) > CROWD_REACH) continue;
          const v = o.moving ? o.speed : 0;
          local.push({ x: o.x, y: o.y, z: o.z, vx: Math.sin(o.yaw) * v, vz: Math.cos(o.yaw) * v, car: null, personal: CROWD_PERSONAL });
        }
      }
      const react = this.reactions.apply({ id: w.id, x: w.x, y: w.y, z: w.z, yaw: w.yaw, speed: w.moving ? w.speed : 0 }, local, dt, seconds);
      const yaw = react.yaw ?? w.yaw;
      if (entry.first || react.down) entry.yaw = yaw;
      else entry.yaw += Math.atan2(Math.sin(yaw - entry.yaw), Math.cos(yaw - entry.yaw)) * turn;
      const seed = (hash32(w.id, 13) % 1000) / 1000;
      const walking = (w.moving || react.moving) && !react.down;
      const clip = walking ? variant.walk : variant.idle;
      const phase = walking ? (w.distance + react.extra) / (variant.metresPerLoop * scale) + seed : w.idleTime / IDLE_SECONDS + seed;
      const rowStart = variant.rowBase + clip.row;
      const previousPhase = entry.first || entry.clip !== rowStart ? phase : entry.phase;
      entry.phase = phase; entry.clip = rowStart; entry.first = false;
      const distance = Math.hypot(react.x - view.x, react.z - view.z);
      entry.lod = distance > (entry.lod === 1 ? 22 : 26) ? 1 : 0;
      const y = w.y + REACT.lift * react.tilt / REACT.lean;
      instances.push({
        variant: look.variant, x: react.x, y, z: react.z, yaw: entry.yaw, scale, tilt: react.tilt, tiltYaw: react.tiltYaw,
        lod: entry.lod, shape: look.model.shape,
        rowStart, frames: clip.frames, phase: phase % 1, previousPhase: previousPhase % 1,
        skin: look.skin, top: look.top, bottom: look.bottom, hair: look.hair,
        shoes: look.shoes, inner: look.inner, legwear: look.legwear,
        fade: Math.max(0.001, entry.fade * w.fade),
      });
      this.poses.push({ id: w.id, x: react.x, y, z: react.z, phase, fade: entry.fade * w.fade });
    }
    this.reactions.prune(seconds);
    this.meshes.update(instances);
    this.count = instances.length;
    this.heights.pump(RAYS_PER_FRAME, frameNow);
    this.updateMs = performance.now() - start;
  }

  /** Hot-swap only GPU meshes; the deterministic lives and ground cache survive. */
  replaceMeshes(Renderer: typeof WalkerMeshes) {
    if (!this.asset) return;
    const next = new Renderer(this.asset, this.options.cap + RING.margin);
    // Atlas belongs to the asset during a renderer-only swap.
    const atlas = this.asset.albedo;
    this.asset.albedo = undefined; this.meshes?.dispose(); this.asset.albedo = atlas;
    this.meshes = next; this.group.add(next.group);
  }

  /** Logical crossing users across the resident graph, independent of the camera, draw cap and fading ring.
   * Traffic samples this at4Hz; it never asks the GPU which pedestrians happen to be visible. */
  trafficObstacles(now = performance.now()): TrafficPedestrian[] {
    if (now - this.lastGraphPoll >= GRAPH_POLL_MS) { this.lastGraphPoll = now; this.syncGraphs(); }
    if (!this.population || !this.trafficCrossings.length) return [];
    const utc = this.options.now(), result: TrafficPedestrian[] = [];
    const lives = this.population.around(this.trafficCrossings, 15, utc, this.crossingUsers);
    for (const w of lives) {
      if (w.fade < 0.2) continue;
      const future = this.population.at(w, utc + 200);
      const y = Number.isFinite(w.y) ? w.y : this.heights.height(this.population.network.chains[w.chain], w.s) ?? NaN;
      result.push({ id: w.id, x: w.x, y, z: w.z, waiting: !w.moving,
        vx: future ? (future.x - w.x) * 5 : 0, vz: future ? (future.z - w.z) * 5 : 0 });
    }
    return result;
  }

  /** Walkers drawn within `radius` metres of the camera (crowd audio). */
  nearby(radius: number): number {
    if (!this.meshes) return 0;
    let n = 0;
    for (const mesh of this.meshes.meshes) {
      for (let i = 0; i < mesh.count; i++) {
        const e = mesh.instanceMatrix.array;
        if (Math.hypot(e[16 * i + 12] - this.camera.x, e[16 * i + 14] - this.camera.z) <= radius) n++;
      }
    }
    return n;
  }

  snapshot() {
    return { cap: this.options.cap, drawn: this.count, candidates: this.wide.length, chains: this.network?.chains.length ?? 0,
      cells: this.network?.cells.size ?? 0, graphs: this.graphs.length, heightsPending: this.heights.pending, rays: this.heights.rays,
      buildMs: Number(this.buildMs.toFixed(2)), updateMs: Number(this.updateMs.toFixed(3)), scanMs: Number(this.scanMs.toFixed(3)),
      reacting: this.reactions.active, hidden: this.hidden.size, lingering: this.lingering.size, variants: this.meshes?.meshes.map(m => m.count) ?? [] };
  }

  debugPoses() { return this.poses; }

  dispose() {
    this.disposed = true; ++this.assetRequest;
    this.worker?.terminate();
    this.meshes?.dispose();
    this.group.removeFromParent();
    this.ring.clear();
    systems.delete(this);
  }
}

// Preserve the live population, height cache and streamed city while editing human appearance/meshes.
const hot = import.meta.hot;
if (hot) {
  hot.data.systems = systems;
  hot.accept();
  for (const system of systems) Object.setPrototypeOf(system, Walkers.prototype);
  hot.accept('./visuals', module => {
    if (!module) return;
    for (const system of systems) system.replaceMeshes(module.WalkerMeshes);
  });
}
