// Ambient walkers (street-richness Phase 4): shared deterministic population (population.ts) on the resident tiles'
// FOOT network (network.ts), drawn locally around the camera (ring.ts) with baked vertex animation (visuals.ts).
import * as THREE from 'three/webgpu';
import type { RoadGraphJson } from '../../gameplay/roadGraph';
import type { QualityTier } from '../../render/quality';
import type { RayFilter, RayHit } from '../../physics/protocol';
import type { V3 } from '../../core/origin';
import { loadWalkerAsset, type WalkerAsset } from './asset';
import { GroundHeights } from './heights';
import { buildWalkNetwork, hash32, type WalkNetwork } from './network';
import { Population, type Walker } from './population';
import { RING, Ring, type RingView } from './ring';
import { WalkerMeshes, packColour, type WalkerInstance } from './visuals';

export { walkersNear } from './population';
export { buildWalkNetwork } from './network';

/** Drawn walkers per quality tier (street-richness plan §7); `?walkers=N` overrides. Phones stay at zero. */
export const WALKER_CAPS: Record<QualityTier, number> = { mobile: 0, low: 10, medium: 30, high: 50 };
const MAX_WALKERS = 200;
export function walkerCap(quality: QualityTier, override: number | null): number {
  return Math.max(0, Math.min(MAX_WALKERS, override ?? WALKER_CAPS[quality]));
}

/** sRGB palettes; each walker's picks are hashes of its id, so every client dresses it alike. */
const PALETTE = {
  skin: [0xf3d0b5, 0xe8b996, 0xd29f7c, 0xb57b55, 0x8d5a3b, 0x5e3a24],
  top: [0x1f2a44, 0x262626, 0x4f5358, 0x6e2a2c, 0x34503a, 0x9a7a52, 0x2c4a6b, 0x8b9099, 0xa8432f, 0x4a3a2c, 0xb48f2e, 0x1c3328],
  bottom: [0x2a3a56, 0x3c5679, 0x1b1b1d, 0x46474b, 0x6f624f, 0x232f45],
  hair: [0x16120f, 0x2f2218, 0x4d3220, 0x7a5232, 0xb89a68, 0x8c8a86, 0x5e2b1a],
  shoes: [0x161616, 0x3a281c, 0xdedcd6, 0x4c3a2c],
  inner: [0xe6e4de, 0xa9bfd6, 0x38393d, 0xd3c3a3, 0x6e2230],
  legwear: [0x18181a, 0x2e2c30, -1],
};
const pick = (id: number, salt: number, list: readonly number[]) => list[hash32(id, salt) % list.length];

type Raycast = (origin: V3, dir: V3, maxToi: number, filter: RayFilter) => Promise<RayHit | null>;
interface WalkerOptions {
  cap: number;
  raycast: Raycast;
  /** Shared UTC milliseconds (the day clock synchronised to the multiplayer server). */
  now: () => number;
  /** Resident road graphs (CoarseTiles.roadGraphs). */
  graphs: () => readonly RoadGraphJson[];
}

const systems = new Set<Walkers>();
/** Walkers drawn by this client right now (Phase 0 richness scorecard). */
export function liveWalkerCount(): number { let n = 0; for (const s of systems) n += s.count; return n; }

const SCAN_MS = 400, GRAPH_POLL_MS = 1000, RAYS_PER_FRAME = 24, YAW_RATE = 8, IDLE_SECONDS = 2.5;

export class Walkers {
  readonly group = new THREE.Group();
  private meshes: WalkerMeshes | null = null;
  private asset: WalkerAsset | null = null;
  private network: WalkNetwork | null = null;
  private population: Population | null = null;
  private graphs: readonly RoadGraphJson[] = [];
  private heights: GroundHeights;
  private ring = new Ring<Walker>();
  private wide: Walker[] = [];
  private lastScan = -Infinity;
  private lastGraphPoll = -Infinity;
  private request = 0;
  private worker: Worker | null = null;
  private camera = new THREE.Vector3();
  private forward = new THREE.Vector3();
  count = 0;
  buildMs = 0;
  /** Last frame's update and last wide scan (every SCAN_MS), main thread. */
  updateMs = 0;
  scanMs = 0;

  constructor(parent: THREE.Object3D, private options: WalkerOptions) {
    this.group.name = 'walkers';
    parent.add(this.group);
    this.heights = new GroundHeights(options.raycast);
    systems.add(this);
  }

  async load(url: string) {
    if (this.options.cap <= 0) return;
    this.asset = await loadWalkerAsset(url);
    this.meshes = new WalkerMeshes(this.asset, this.options.cap + RING.margin);
    this.group.add(this.meshes.group);
  }

  /** Rebuilds the network when the set of resident graphs changed (identity, not load order), in a worker when the
   *  page has one; the previous network keeps walking until the new one arrives. */
  private syncGraphs() {
    const graphs = this.options.graphs();
    if (graphs.length === this.graphs.length && graphs.every(g => this.graphs.includes(g))) return;
    this.graphs = [...graphs];
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
    this.population = new Population(network);
    this.heights.retain(new Set(network.chains.map(c => c.id)));
    this.buildMs = ms;
    this.lastScan = -Infinity;
  }

  /** Once per frame after the camera moved. */
  update(dt: number, camera: THREE.PerspectiveCamera, frameNow: number) {
    if (!this.meshes || !this.asset) return;
    const start = performance.now();
    if (frameNow - this.lastGraphPoll >= GRAPH_POLL_MS) { this.lastGraphPoll = frameNow; this.syncGraphs(); }
    const population = this.population;
    if (!population) { this.meshes.update([]); this.count = 0; return; }
    const utc = this.options.now();
    camera.getWorldPosition(this.camera);
    camera.getWorldDirection(this.forward);
    const flat = Math.hypot(this.forward.x, this.forward.z) || 1;
    const halfH = Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * camera.aspect);
    const view: RingView = { x: this.camera.x, z: this.camera.z, fx: this.forward.x / flat, fz: this.forward.z / flat,
      cosHalf: Math.cos(Math.min(Math.PI, halfH + THREE.MathUtils.degToRad(12))) };
    if (frameNow - this.lastScan >= SCAN_MS) {
      this.lastScan = frameNow;
      const scan = performance.now();
      this.wide = population.near(view.x, view.z, RING.inView + RING.keep + 15, utc);
      this.scanMs = performance.now() - scan;
    }
    const candidates: Walker[] = [];
    for (const old of this.wide) {
      const walker = population.at(old, utc);
      if (!walker) continue;
      if (!Number.isFinite(walker.y)) {
        const chain = population.network.chains[walker.chain];
        const y = this.heights.height(chain, walker.s);
        if (y === null) { this.heights.request(chain, frameNow); continue; }
        walker.y = y;
      }
      candidates.push(walker);
    }
    const drawn = this.ring.select(candidates, view, this.options.cap, dt);
    const instances: WalkerInstance[] = [];
    const turn = 1 - Math.exp(-dt * YAW_RATE);
    for (const entry of drawn) {
      const w = entry.walker, variant = this.asset.variants[w.variant];
      if (entry.first) entry.yaw = w.yaw;
      else entry.yaw += Math.atan2(Math.sin(w.yaw - entry.yaw), Math.cos(w.yaw - entry.yaw)) * turn;
      const seed = (hash32(w.id, 13) % 1000) / 1000;
      const clip = w.moving ? variant.walk : variant.idle;
      const phase = w.moving ? w.distance / (variant.metresPerLoop * w.scale) + seed : w.idleTime / IDLE_SECONDS + seed;
      const rowStart = variant.rowBase + clip.row;
      const previousPhase = entry.first || entry.clip !== rowStart ? phase : entry.phase;
      entry.phase = phase; entry.clip = rowStart; entry.first = false;
      const skin = pick(w.id, 20, PALETTE.skin), legwear = pick(w.id, 26, PALETTE.legwear);
      instances.push({
        variant: w.variant, x: w.x, y: w.y, z: w.z, yaw: entry.yaw, scale: w.scale,
        rowStart, frames: clip.frames, phase: phase % 1, previousPhase: previousPhase % 1,
        skin: packColour(skin), top: packColour(pick(w.id, 21, PALETTE.top)), bottom: packColour(pick(w.id, 22, PALETTE.bottom)),
        hair: packColour(pick(w.id, 23, PALETTE.hair)), shoes: packColour(pick(w.id, 24, PALETTE.shoes)),
        inner: packColour(pick(w.id, 25, PALETTE.inner)), legwear: packColour(legwear < 0 ? skin : legwear),
        fade: Math.max(0.001, entry.fade * w.fade),
      });
    }
    this.meshes.update(instances);
    this.count = instances.length;
    this.heights.pump(RAYS_PER_FRAME, frameNow);
    this.updateMs = performance.now() - start;
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
      variants: this.meshes?.meshes.map(m => m.count) ?? [] };
  }

  dispose() {
    this.worker?.terminate();
    this.meshes?.dispose();
    this.group.removeFromParent();
    this.ring.clear();
    systems.delete(this);
  }
}
