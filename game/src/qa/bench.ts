// Fixed-camera bench probe (street-richness plan, Phase 0): ?bench=camera&id=<id>[&at=HH:MM][&quality=<tier>].
// The pose comes from street-cameras.json. The page runs the QA render path, but at the play-mode pixel ratio with
// the adaptive scale fixed at 1. Once the scene is quiescent it warms BENCH_WARM frames and measures BENCH_FRAMES, then
// publishes window.__bench (also printed into #bench-result for a manual run). Rendering pauses only while the GPU
// timer readbacks drain.
import streetCameras from './street-cameras.json';
import { E0, N0 } from '../core/origin';

export const BENCH_WARM = 120;
export const BENCH_FRAMES = 240;
/** QA's 32 settled frames pass in a fraction of a second with the frame rate unlocked, before textures, shadow batches
 *  and pipelines finish. The bench also waits until the frame's draws, triangles, pipelines, textures and network
 *  requests have not changed for this long, giving up after BENCH_SETTLE_LIMIT_MS. */
export const BENCH_SETTLE_MS = 3000;
const BENCH_SETTLE_LIMIT_MS = 120_000;
/** The default render date (core/params.ts), in Helsinki summer time. */
const BENCH_DATE = '2026-10-15';
const BENCH_OFFSET = '+03:00';

export interface StreetCamera {
  id: string; pos: number[]; yaw_deg: number; pitch_deg: number; fov: number; time_local: string[]; quality: string[];
  tiles: string[];
}
export interface BenchPose {
  id: string; at: string; time: Date;
  qa: { E: number; N: number; H: number; yawDeg: number; pitchDeg: number; fovDeg: number; rollDeg: number };
}

/** The QA pose and render time of one street camera; `at` defaults to the camera's first listed local time. */
export function benchPose(id: string, at: string | null, cameras: StreetCamera[] = streetCameras.cameras): BenchPose {
  const camera = cameras.find(c => c.id === id);
  if (!camera) throw new Error(`bad ?id= (street camera: ${cameras.map(c => c.id).join(', ')}): ${id}`);
  const time = at ?? camera.time_local[0];
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) throw new Error(`bad ?at= (HH:MM local time): ${time}`);
  const [x, y, z] = camera.pos;
  return { id, at: time, time: new Date(`${BENCH_DATE}T${time}:00${BENCH_OFFSET}`),
    qa: { E: x + E0, N: N0 - z, H: y, yawDeg: camera.yaw_deg, pitchDeg: camera.pitch_deg, fovDeg: camera.fov, rollDeg: 0 } };
}

export type BenchPass = 'prepass' | 'beauty' | 'csm' | 'reflection' | 'post';
interface Target { depthTexture?: { name?: string } | null; texture?: { name?: string } | null }

/** Which pass a draw belongs to: the post pipeline's prepass and scene pass (by render target), the CSM shadow
 *  maps (three's ShadowNode names them), other scene renders through a perspective camera (the river reflector's
 *  virtual camera), and the rest: full-screen post quads (GTAO, TRAA, bloom, output) and PMREM. */
export function classifyTarget(target: unknown, passes: { prepass?: unknown; beauty?: unknown }, camera?: { type?: string }): BenchPass {
  if (target && target === passes.prepass) return 'prepass';
  if (target && target === passes.beauty) return 'beauty';
  const t = target as Target | null | undefined;
  if (t?.depthTexture?.name === 'ShadowDepthTexture' || t?.texture?.name === 'ShadowMap') return 'csm';
  return target && camera?.type === 'PerspectiveCamera' ? 'reflection' : 'post';
}

interface DrawBackend { draw(renderObject: RenderObjectLike, info: unknown): unknown }
interface RenderObjectLike {
  context?: { renderTarget?: unknown }; object?: { isInstancedMesh?: boolean; count?: number; name?: string };
  camera?: { type?: string };
}

/** A short description of a render target for the bench's breakdown of unattributed draws. */
export function describeTarget(target: unknown, camera?: { type?: string }): string {
  const t = target as (Target & { width?: number; height?: number }) | null | undefined;
  if (!t) return `canvas|${camera?.type ?? '?'}`;
  return `${t.texture?.name || 'unnamed'}|${t.depthTexture?.name || '-'}|${t.width}x${t.height}|${camera?.type ?? '?'}`;
}
interface PerfLike {
  reset(): void; flush(): Promise<void>;
  snapshot(): { gpuMs: unknown; cpuMs: unknown; workMs: unknown; frameMs: unknown; gpuStatus: string; gpuSkipped: number;
    drawCalls: number; triangles: number; windowFrames: number; resolution: number[]; backend: string };
}
export interface BenchHost {
  backend: unknown; pipelines: () => number | null; passes: () => { prepass?: unknown; beauty?: unknown };
  perf: PerfLike; pause: (paused: boolean) => void; info: () => Record<string, unknown>;
  /** Anything that changes while the scene is still loading (draws, triangles, pipelines, textures, requests). */
  signature: () => string; now: () => number;
}

/** Counts submitted draws per pass and drives warm-up, measurement and the result. */
export class CameraBench {
  private phase: 'settle' | 'warm' | 'measure' | 'drain' | 'done' = 'settle';
  private settleStart: number | null = null;
  private stableSince = 0;
  private signature = '';
  private settle = { ms: 0, timedOut: false };
  private frames = 0;
  private counting = false;
  private draws: Record<BenchPass, number> = { prepass: 0, beauty: 0, csm: 0, reflection: 0, post: 0 };
  private instances = 0;
  private others = new Map<string, number>();
  private pipelinesAtStart: number | null = null;
  result: Record<string, unknown> | null = null;

  constructor(private host: BenchHost, private pose: BenchPose, private quality: string,
    private warm = BENCH_WARM, private measure = BENCH_FRAMES, private settleMs = BENCH_SETTLE_MS) {
    const backend = host.backend as DrawBackend;
    const draw = backend.draw.bind(backend);
    backend.draw = (renderObject, info) => {
      if (this.counting) {
        const target = renderObject.context?.renderTarget;
        const pass = classifyTarget(target, host.passes(), renderObject.camera);
        this.draws[pass]++;
        if (pass === 'post' || pass === 'reflection') {
          const key = describeTarget(target, renderObject.camera);
          this.others.set(key, (this.others.get(key) ?? 0) + 1);
        }
        const object = renderObject.object;
        this.instances += object?.isInstancedMesh ? object.count ?? 1 : 1;
      }
      return draw(renderObject, info);
    };
  }

  /** Call after every rendered frame once the view has settled. Resolves when the result is published. */
  async frame(): Promise<Record<string, unknown> | null> {
    if (this.phase === 'settle') {
      const now = this.host.now(), signature = this.host.signature();
      this.settleStart ??= now;
      if (signature !== this.signature) { this.signature = signature; this.stableSince = now; }
      const waited = now - this.settleStart;
      if (now - this.stableSince < this.settleMs && waited < BENCH_SETTLE_LIMIT_MS) return null;
      this.settle = { ms: Math.round(waited), timedOut: now - this.stableSince < this.settleMs };
      this.phase = 'warm';
      return null;
    }
    if (this.phase === 'warm') {
      if (++this.frames < this.warm) return null;
      this.phase = 'measure';
      this.frames = 0;
      this.host.perf.reset();
      this.pipelinesAtStart = this.host.pipelines();
      this.counting = true;
      return null;
    }
    if (this.phase !== 'measure' || ++this.frames < this.measure) return null;
    this.counting = false;
    this.phase = 'drain';
    this.host.pause(true);
    try { await this.host.perf.flush(); } finally { this.host.pause(false); }
    const s = this.host.perf.snapshot();
    const per = (n: number) => Math.round((n / this.measure) * 10) / 10;
    const pipelines = this.host.pipelines();
    this.result = {
      id: this.pose.id, at: this.pose.at, quality: this.quality, backend: s.backend,
      settleMs: this.settle.ms, settleTimedOut: this.settle.timedOut,
      warmFrames: this.warm, frames: this.measure, windowFrames: s.windowFrames,
      submittedDraws: {
        total: per(Object.values(this.draws).reduce((a, b) => a + b, 0)),
        prepass: per(this.draws.prepass), beauty: per(this.draws.beauty), csm: per(this.draws.csm),
        reflection: per(this.draws.reflection), post: per(this.draws.post),
      },
      otherDraws: Object.fromEntries([...this.others].sort((a, b) => b[1] - a[1]).map(([k, v]) => [k, per(v)])),
      instances: per(this.instances), drawCallsLastFrame: s.drawCalls, triangles: s.triangles,
      gpuMs: s.gpuMs, cpuMs: s.cpuMs, workMs: s.workMs, frameMs: s.frameMs, gpuStatus: s.gpuStatus, gpuSkipped: s.gpuSkipped,
      pipelinesAfterWarmup: this.pipelinesAtStart, pipelinesAfterMeasure: pipelines,
      drawingBuffer: s.resolution, adaptiveScale: 1,
      ...this.host.info(),
    };
    this.phase = 'done';
    return this.result;
  }
}
