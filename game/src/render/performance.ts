import { mainThreadIdle, streamingAllowance } from './preparationBudget';
import type { WebGPURenderer } from 'three/webgpu';
import { GpuFrameTimer } from './gpuTimer';
import { GPU_PASSES, type GpuPassTimes } from './gpuPasses';

export function percentiles(values: number[]) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  const at = (p: number) => sorted.length ? sorted[Math.max(0, Math.ceil(sorted.length * p) - 1)] : null;
  return { samples: sorted.length, p50: at(0.5), p95: at(0.95) };
}

interface FrameSample { frame: number; cpu: number; interval: number | null; gpu: number | null; passes?: GpuPassTimes }

export class PerformanceMonitor {
  private samples = new Map<number, FrameSample>();
  private start = 0;
  private previousStart: number | null = null;
  private frame = 0;
  calls = 0;
  triangles = 0;
  private totalFrames = 0;
  private timer: GpuFrameTimer;
  private minimumFrame = 0;
  private skippedAtReset = 0;
  private passesSkippedAtReset = 0;
  lastCpu = 0;
  lastGpu = 0;
  lastGpuFrame = -1;
  lastGpuPasses: GpuPassTimes | null = null;
  lastGpuPassFrame = -1;

  /** CPU and GPU overlap; use the limiting stage, not their sum or the capped RAF interval. */
  get frameCostMs() { return Math.max(this.lastCpu, this.lastGpu); }

  constructor(private renderer: WebGPURenderer, private backend: string) {
    this.timer = new GpuFrameTimer(renderer, (frame, gpu, passes) => {
      const sample = this.samples.get(frame);
      if (sample && frame >= this.minimumFrame) { sample.gpu = gpu; sample.passes = passes; }
      if (sample && frame >= this.minimumFrame && frame > this.lastGpuFrame) {
        this.lastGpu = gpu; this.lastGpuFrame = frame;
        this.lastGpuPasses = passes ?? null; this.lastGpuPassFrame = passes ? frame : -1;
      }
    });
  }

  begin() {
    this.start = performance.now();
    this.frame = this.renderer.info.frame;
    this.renderer.info.reset();
    this.timer.begin(this.frame);
  }

  end() {
    const cpu = performance.now() - this.start;
    this.lastCpu = cpu;
    this.calls = this.renderer.info.render.drawCalls;
    this.triangles = this.renderer.info.render.triangles;
    const interval = this.previousStart === null ? null : this.start - this.previousStart;
    this.samples.set(this.frame, { frame: this.frame, cpu, interval, gpu: null });
    // Streaming spends this frame's measured headroom (render/preparationBudget.ts).
    mainThreadIdle.start();
    streamingAllowance.frame(cpu, interval, mainThreadIdle.take(), this.lastGpu);
    this.previousStart = this.start;
    this.totalFrames++;
    if (this.samples.size > 600) this.samples.delete(this.samples.keys().next().value!);
    this.timer.end();
  }

  /** One frame's sample while it is in the window: the bench lab (qa/lab) reads GPU times as their readbacks land. */
  sample(frame: number): Readonly<FrameSample> | undefined { return this.samples.get(frame); }

  async flush() {
    await this.timer.flush();
  }

  async completed() { await this.timer.completed(); }

  reset() {
    this.samples.clear();
    this.totalFrames = 0;
    this.previousStart = null;
    this.lastCpu = this.lastGpu = 0; this.lastGpuFrame = -1;
    this.lastGpuPasses = null; this.lastGpuPassFrame = -1;
    this.minimumFrame = this.renderer.info.frame;
    this.skippedAtReset = this.timer.skipped;
    this.passesSkippedAtReset = this.timer.passSkipped;
  }

  snapshot() {
    const rows = [...this.samples.values()];
    return {
      backend: this.backend, totalFrames: this.totalFrames, windowFrames: rows.length,
      resolution: [this.renderer.domElement.width, this.renderer.domElement.height],
      cpuMs: percentiles(rows.map(s => s.cpu)),
      gpuMs: percentiles(rows.flatMap(s => s.gpu === null ? [] : [s.gpu])),
      // Conservative CPU + whole-frame GPU interval, paired by frame; not the RAF interval.
      workMs: percentiles(rows.flatMap(s => s.gpu === null ? [] : [s.cpu + s.gpu])),
      frameMs: percentiles(rows.flatMap(s => s.interval === null ? [] : [s.interval])),
      gpuStatus: this.timer.status, gpuSkipped: this.timer.skipped - this.skippedAtReset,
      gpuPassStatus: this.timer.passStatus,
      drawCalls: this.calls, triangles: this.triangles,
    };
  }

  /** Diagnostic-only percentiles: do not add per-pass sorts to each HUD refresh.
   * Repeated passes (cascades/bloom levels) sum per frame; absent passes count as zero.
   * Copy/compute work and GPU idle gaps remain in whole-frame gpuMs, not these totals. */
  passSnapshot() {
    const rows = [...this.samples.values()].filter(s => s.passes !== undefined);
    return { status: this.timer.passStatus, frames: rows.length,
      skipped: this.timer.passSkipped - this.passesSkippedAtReset,
      gpuMs: Object.fromEntries(GPU_PASSES.map(name => [name, percentiles(rows.map(s => s.passes![name] ?? 0))])) };
  }
}

export type PerfSnapshot = ReturnType<PerformanceMonitor['snapshot']>;
