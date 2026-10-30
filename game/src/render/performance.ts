import type { WebGPURenderer } from 'three/webgpu';
import { GpuFrameTimer } from './gpuTimer';

export function percentiles(values: number[]) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  const at = (p: number) => sorted.length ? sorted[Math.max(0, Math.ceil(sorted.length * p) - 1)] : null;
  return { samples: sorted.length, p50: at(0.5), p95: at(0.95) };
}

interface FrameSample { frame: number; cpu: number; interval: number | null; gpu: number | null }

export class PerformanceMonitor {
  private samples = new Map<number, FrameSample>();
  private start = 0;
  private previousStart: number | null = null;
  private frame = 0;
  private calls = 0;
  private triangles = 0;
  private totalFrames = 0;
  private timer: GpuFrameTimer;
  private minimumFrame = 0;
  private skippedAtReset = 0;
  private lastCpu = 0;
  private lastGpu = 0;
  private lastGpuFrame = -1;

  /** CPU and GPU overlap; use the limiting stage, not their sum or the capped RAF interval. */
  get frameCostMs() { return Math.max(this.lastCpu, this.lastGpu); }

  constructor(private renderer: WebGPURenderer, private backend: string) {
    this.timer = new GpuFrameTimer(renderer, (frame, gpu) => {
      const sample = this.samples.get(frame);
      if (sample && frame >= this.minimumFrame) sample.gpu = gpu;
      if (sample && frame >= this.minimumFrame && frame > this.lastGpuFrame) {
        this.lastGpu = gpu; this.lastGpuFrame = frame;
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
    this.samples.set(this.frame, { frame: this.frame, cpu,
      interval: this.previousStart === null ? null : this.start - this.previousStart, gpu: null });
    this.previousStart = this.start;
    this.totalFrames++;
    if (this.samples.size > 600) this.samples.delete(this.samples.keys().next().value!);
    this.timer.end();
  }

  async flush() {
    await this.timer.flush();
  }

  async completed() { await this.timer.completed(); }

  reset() {
    this.samples.clear();
    this.totalFrames = 0;
    this.previousStart = null;
    this.lastCpu = this.lastGpu = 0; this.lastGpuFrame = -1;
    this.minimumFrame = this.renderer.info.frame;
    this.skippedAtReset = this.timer.skipped;
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
      drawCalls: this.calls, triangles: this.triangles,
    };
  }
}

export type PerfSnapshot = ReturnType<PerformanceMonitor['snapshot']>;
