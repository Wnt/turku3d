// Play telemetry (README.md): once a second, where the player is and which facade walls are on screen, how large and
// how close; batched as JSON lines and POSTed every 5 s to /telemetry on the serving origin (tools/dev/serve-dist.mjs).
import type * as THREE from 'three/webgpu';
import { engineToEN, yawToHeading } from '../core/origin';
import { extractWalls, measureWall, type FacadeWall, type WallView } from './walls';
import { tilesAhead, visualTileAt } from './grid';

export { telemetryEnabled } from './guard';

const SAMPLE_MS = 1000;
const FLUSH_MS = 5000;
const MAX_DISTANCE_M = 150;
const MIN_FRACTION = 0.0005;
const MAX_FACADES = 40;

interface PlayerPose {
  /** Player feet (engine frame). */
  x: number; y: number; z: number;
  /** Driven vehicle id, null on foot. */
  vehicle: string | null;
  /** Horizontal speed, m/s. */
  speed: number;
}

export interface Sample {
  session: string; t: number; E: number; N: number; H: number; yaw: number; pitch: number;
  vehicle: string | null; speed: number; tile: string; facades: WallView[];
  outside_tiles?: { stand: string; ahead: string[] };
}

/** Sends one batch of JSON lines; `final` on page hide (navigator.sendBeacon). */
type Transport = (body: string, final: boolean) => Promise<boolean> | boolean;

export function httpTransport(endpoint: string): Transport {
  return (body, final) => {
    if (final) return navigator.sendBeacon(endpoint, new Blob([body], { type: 'application/x-ndjson' }));
    return fetch(endpoint, { method: 'POST', body, keepalive: true, headers: { 'Content-Type': 'application/x-ndjson' } })
      .then(r => r.ok, () => false);
  };
}

const round = (x: number, digits: number) => { const k = 10 ** digits; return Math.round(x * k) / k; };

export class Telemetry {
  readonly session = Math.random().toString(36).slice(2, 10);
  readonly stats = { samples: 0, sent: 0, failed: 0, lastSampleMs: 0, maxSampleMs: 0, walls: 0 };
  private models = new Map<THREE.Object3D, FacadeWall[] | null>();
  private built: Set<string> | null = null;
  private queue: string[] = [];
  private lastSample = -Infinity;
  private lastFlush = 0;
  private direction = { x: 0, y: 0, z: 0 };

  constructor(private readonly transport: Transport, private readonly clock: () => number = Date.now) {}

  /** A loaded level-0 visual tile model; its walls are fitted the first time it is attached to the scene. */
  addModel(model: THREE.Object3D, tileId: string | null | undefined) {
    if (tileId && tileId.startsWith('t0_')) this.models.set(model, null);
  }
  removeModel(model: THREE.Object3D) {
    const walls = this.models.get(model);
    if (walls) this.stats.walls -= walls.length;
    this.models.delete(model);
  }
  /** Built level-0 tiles; outside them, samples name the tile the player stands in and the two ahead. */
  setBuiltTiles(ids: Iterable<string>) {
    this.built = new Set([...ids].filter(id => id.startsWith('t0_')));
  }

  /** Call every frame after rendering; samples once a second and flushes every five. */
  tick(nowMs: number, camera: THREE.PerspectiveCamera, pose: () => PlayerPose) {
    if (nowMs - this.lastSample < SAMPLE_MS) return;
    this.lastSample = nowMs;
    const t0 = performance.now();
    this.queue.push(JSON.stringify(this.sample(camera, pose())));
    const ms = performance.now() - t0;
    this.stats.samples++;
    this.stats.lastSampleMs = ms;
    this.stats.maxSampleMs = Math.max(this.stats.maxSampleMs, ms);
    if (nowMs - this.lastFlush >= FLUSH_MS) { this.lastFlush = nowMs; this.flush(false); }
  }

  sample(camera: THREE.PerspectiveCamera, pose: PlayerPose): Sample {
    const en = engineToEN(pose.x, pose.y, pose.z);
    const e = camera.matrixWorld.elements;
    const d = this.direction;
    d.x = -e[8]; d.y = -e[9]; d.z = -e[10];
    const dl = Math.hypot(d.x, d.y, d.z) || 1;
    const views: WallView[] = [];
    for (const [model, cached] of this.models) {
      if (!model.parent) continue; // not visible this frame
      let walls = cached;
      if (!walls) { walls = extractWalls(model); this.models.set(model, walls); this.stats.walls += walls.length; }
      for (const wall of walls) {
        const view = measureWall(wall, camera, MAX_DISTANCE_M);
        if (view && view.screen_fraction > MIN_FRACTION) views.push(view);
      }
    }
    views.sort((a, b) => b.screen_fraction - a.screen_fraction);
    const tile = visualTileAt(pose.x, pose.z);
    const sample: Sample = {
      session: this.session, t: round(this.clock() / 1000, 3), E: round(en.E, 2), N: round(en.N, 2), H: round(en.H, 2),
      yaw: round(yawToHeading(Math.atan2(-d.x, -d.z)), 1), pitch: round(Math.asin(Math.max(-1, Math.min(1, d.y / dl))) * 180 / Math.PI, 1),
      vehicle: pose.vehicle, speed: round(pose.speed, 2), tile,
      facades: views.slice(0, MAX_FACADES).map(w => ({ facade_id: w.facade_id, screen_fraction: round(w.screen_fraction, 5),
        distance_m: round(w.distance_m, 1), angle_deg: round(w.angle_deg, 1) })),
    };
    if (this.built && !this.built.has(tile)) {
      sample.outside_tiles = { stand: tile, ahead: tilesAhead(pose.x, pose.z, d.x, d.z) };
    }
    return sample;
  }

  /** Sends the queued samples; `final` uses the page-hide beacon. */
  flush(final: boolean) {
    if (!this.queue.length) return;
    const body = this.queue.join('\n') + '\n';
    this.queue = [];
    void Promise.resolve(this.transport(body, final)).then(ok => { if (ok) this.stats.sent++; else this.stats.failed++; });
  }
}
