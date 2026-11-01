// Fixed-camera bench probe (street-richness plan, Phase 0): ?bench=camera&id=<id>[&at=HH:MM][&quality=<tier>].
// The pose comes from street-cameras.json. The page runs the QA render path, but at the play-mode pixel ratio with
// the adaptive scale fixed at 1. Once the tiles settle it warms BENCH_WARM frames and measures BENCH_FRAMES, then
// publishes window.__bench (also printed into #bench-result for a manual run). Rendering pauses only while the GPU
// timer readbacks drain.
import streetCameras from './street-cameras.json';
import { E0, N0 } from '../core/origin';

export const BENCH_WARM = 120;
export const BENCH_FRAMES = 240;
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

export type BenchPass = 'prepass' | 'beauty' | 'csm' | 'other';
interface Target { depthTexture?: { name?: string } | null; texture?: { name?: string } | null }

/** Which pass a draw belongs to, by its render target: the post pipeline's prepass and scene pass, the CSM
 *  shadow maps (three's ShadowNode names them), or anything else (sky, PMREM, post quads, canvas). */
export function classifyTarget(target: unknown, passes: { prepass?: unknown; beauty?: unknown }): BenchPass {
  if (!target) return 'other';
  if (target === passes.prepass) return 'prepass';
  if (target === passes.beauty) return 'beauty';
  const t = target as Target;
  if (t.depthTexture?.name === 'ShadowDepthTexture' || t.texture?.name === 'ShadowMap') return 'csm';
  return 'other';
}

interface DrawBackend { draw(renderObject: RenderObjectLike, info: unknown): unknown }
interface RenderObjectLike { context?: { renderTarget?: unknown }; object?: { isInstancedMesh?: boolean; count?: number } }
interface PerfLike {
  reset(): void; flush(): Promise<void>;
  snapshot(): { gpuMs: unknown; cpuMs: unknown; workMs: unknown; frameMs: unknown; gpuStatus: string; gpuSkipped: number;
    drawCalls: number; triangles: number; windowFrames: number; resolution: number[]; backend: string };
}
export interface BenchHost {
  backend: unknown; pipelines: () => number | null; passes: () => { prepass?: unknown; beauty?: unknown };
  perf: PerfLike; pause: (paused: boolean) => void; info: () => Record<string, unknown>;
}

/** Counts submitted draws per pass and drives warm-up, measurement and the result. */
export class CameraBench {
  private phase: 'warm' | 'measure' | 'drain' | 'done' = 'warm';
  private frames = 0;
  private counting = false;
  private draws: Record<BenchPass, number> = { prepass: 0, beauty: 0, csm: 0, other: 0 };
  private instances = 0;
  private pipelinesAtStart: number | null = null;
  result: Record<string, unknown> | null = null;

  constructor(private host: BenchHost, private pose: BenchPose, private quality: string,
    private warm = BENCH_WARM, private measure = BENCH_FRAMES) {
    const backend = host.backend as DrawBackend;
    const draw = backend.draw.bind(backend);
    backend.draw = (renderObject, info) => {
      if (this.counting) {
        this.draws[classifyTarget(renderObject.context?.renderTarget, host.passes())]++;
        const object = renderObject.object;
        this.instances += object?.isInstancedMesh ? object.count ?? 1 : 1;
      }
      return draw(renderObject, info);
    };
  }

  /** Call after every rendered frame once the view has settled. Resolves when the result is published. */
  async frame(): Promise<Record<string, unknown> | null> {
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
      warmFrames: this.warm, frames: this.measure, windowFrames: s.windowFrames,
      submittedDraws: {
        total: per(this.draws.prepass + this.draws.beauty + this.draws.csm + this.draws.other),
        prepass: per(this.draws.prepass), beauty: per(this.draws.beauty), csm: per(this.draws.csm), other: per(this.draws.other),
      },
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
