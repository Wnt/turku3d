// Bench lab scenarios (qa/lab/params.ts for the URL contract, tools/perf/README.md for the output). Each runs on the
// real engine: the same tile streamer (CoarseTiles with its scheduler paused, tiles installed through its own preview
// and detail paths), materials, preparation queue, renderer and post pipeline as play. The lab only records frames,
// wraps subsystem entry points with timers and drives the camera.
import * as THREE from 'three/webgpu';
import { SLICES, streamTrace } from '../../telemetry/streamTrace';
import { E0, N0 } from '../../core/origin';
import { byDistance, labLabel, tileCenter, type LabParams } from './params';
import {
  SUBSYSTEMS, bandSummary, dist, frameSummary, heapStats, loafSummary, passSummary, stageSplit, stageWindows, subsystemSplit,
  type LabEvent, type LabFrame, type LoafRow, type Subsystem,
} from './stats';

type Install = { state: string; preview: boolean; full: boolean; error: string | null; retryInMs: number };
interface Sample { gpu: number | null; passes?: Record<string, number | undefined> }
export interface LabRecorder { readonly id: string; stop(): Promise<void> }

export interface LabHost {
  lab: LabParams; quality: string; aa: string;
  camera: THREE.PerspectiveCamera;
  coarse: { scheduling: boolean; benchInstall(id: string, stage: 'preview' | 'full', x: number, z: number): Promise<Install> };
  renderer: { info: { frame: number } };
  perf: { lastCpu: number; calls: number; triangles: number; sample(frame: number): Sample | undefined; flush(): Promise<void>; reset(): void };
  /** r186 node programs built so far inside live frames (render/census.ts), when the renderer logs them. */
  inFrameBuilds: () => number;
  /** Loading state that must stop changing before a measurement (resident geometries, textures, pipelines). */
  signature: () => string;
  /** Streaming still running: scenery not ready, a long download in flight, ground textures pending. */
  busy: () => boolean;
  /** Reset the frame clock and the seeded population to the run's start. */
  resetWorld: () => Promise<void>;
  /** Entry points timed per frame: [object, method] for each subsystem present. */
  subsystems: Partial<Record<Subsystem, [object, string]>>;
  pause: (paused: boolean) => void;
  /** Start a capture recording (telemetry/capture.ts) carrying `meta`; null when recording is off or unavailable. */
  record: (meta: Record<string, unknown>) => LabRecorder | null;
  /** Append the result to the saved capture's meta.json. */
  saveResult: (id: string, result: Record<string, unknown>) => Promise<void>;
  info: () => Record<string, unknown>;
}

export interface LabStatus {
  mode: string; phase: string; done: boolean; error: string | null; progress: string;
  capture: string | null; result: Record<string, unknown> | null;
}

const BAND = /^(band\.|cut\.commit)/;
const wait = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));
const r2 = (v: number) => Math.round(v * 100) / 100;

/** The fly path: a closed loop over the tile set, a low pass, a climb, a high pass back and a descent. */
export function flyPath(tiles: string[], baseY: number, height: number) {
  const centers = tiles.map(tileCenter);
  const xs = centers.map(c => c.x), zs = centers.map(c => c.z);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cz = (Math.min(...zs) + Math.max(...zs)) / 2;
  const rx = Math.max(125, (Math.max(...xs) - Math.min(...xs)) / 2 + 125), rz = Math.max(125, (Math.max(...zs) - Math.min(...zs)) / 2 + 125);
  const low = baseY + height, high = low + 300;
  const curve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(cx - rx * 0.8, low, cz + rz * 0.3), new THREE.Vector3(cx, low, cz + rz * 0.35),
    new THREE.Vector3(cx + rx * 0.8, low, cz + rz * 0.3), new THREE.Vector3(cx + rx * 0.6, high, cz - rz * 0.5),
    new THREE.Vector3(cx - rx * 0.6, high, cz - rz * 0.5),
  ], true, 'centripetal');
  const position = new THREE.Vector3(), target = new THREE.Vector3();
  return {
    curve,
    /** Camera pose at path fraction `u` (0..1): looking along the path, 15 m below the point 3 % ahead. */
    apply(camera: THREE.PerspectiveCamera, u: number) {
      curve.getPointAt(((u % 1) + 1) % 1, position);
      curve.getPointAt((((u + 0.03) % 1) + 1) % 1, target);
      // The spline overshoots below the low leg on the descent: never under the cruise height.
      position.y = Math.max(position.y, low);
      target.y = Math.max(target.y, low) - 15;
      camera.position.copy(position);
      camera.lookAt(target);
      camera.updateMatrixWorld();
    },
  };
}

export class BenchLab {
  readonly status: LabStatus;
  private recording = false;
  private frames: LabFrame[] = [];
  private pendingGpu: LabFrame[] = [];
  private events: LabEvent[] = [];
  private loaf: LoafRow[] = [];
  private sub = new Float64Array(SUBSYSTEMS.length);
  private slices = new Float64Array(SLICES.length);
  private bands = 0;
  private start = 0;
  private previousStart: number | null = null;
  private rendererFrame = 0;
  private builds = 0;
  private waiters: (() => void)[] = [];
  private fly: { path: ReturnType<typeof flyPath>; start: number; seconds: number } | null = null;
  private recorder: LabRecorder | null = null;
  private marks: Record<string, number> = {};
  private observer?: PerformanceObserver;
  private settleInfo: { ms: number; timedOut: boolean } | null = null;

  constructor(private host: LabHost) {
    this.status = { mode: host.lab.mode, phase: 'loading', done: false, error: null, progress: '', capture: null, result: null };
    host.coarse.scheduling = false;
    SUBSYSTEMS.forEach((name, i) => {
      const target = host.subsystems[name];
      if (!target) return;
      const [object, key] = target as [Record<string, (...args: unknown[]) => unknown>, string];
      const original = object[key], sub = this.sub;
      object[key] = function (this: unknown, ...args: unknown[]) {
        const t = performance.now();
        try { return original.apply(this, args); } finally { sub[i] += performance.now() - t; }
      };
    });
    // Tee the streaming trace: the capture recorder drains it, the lab keeps its own copy for per-tile tables.
    const event = streamTrace.event.bind(streamTrace), slice = streamTrace.slice.bind(streamTrace);
    streamTrace.event = (k, id, fields) => {
      event(k, id, fields);
      if (!streamTrace.on) return;
      if (BAND.test(k)) this.bands++;
      if (k === 'coverage') return;
      this.events.push({ t: performance.now(), k, ...(id === undefined ? {} : { id }), ...fields });
      if (this.events.length > 200_000) this.events.splice(0, 50_000);
    };
    streamTrace.slice = (kind, ms) => { slice(kind, ms); if (streamTrace.on) this.slices[SLICES.indexOf(kind)] += ms; };
    streamTrace.on = true;
    if (PerformanceObserver.supportedEntryTypes?.includes('long-animation-frame')) {
      this.observer = new PerformanceObserver(list => {
        for (const e of list.getEntries() as unknown as { startTime: number; duration: number; blockingDuration: number;
          scripts: { duration: number; sourceFunctionName: string; sourceURL: string; invokerType: string }[] }[]) {
          this.loaf.push({ t: e.startTime, d: e.duration, block: e.blockingDuration,
            scripts: e.scripts.map(s => ({ d: s.duration, fn: s.sourceFunctionName, url: s.sourceURL.split('/').slice(-2).join('/'), type: s.invokerType })) });
        }
      });
      this.observer.observe({ type: 'long-animation-frame', buffered: false });
    }
  }

  /** Before the frame's work: place the fly camera and remember where the frame starts. */
  before(now: number) {
    this.start = now;
    this.rendererFrame = this.host.renderer.info.frame;
    this.sub.fill(0);
    if (this.fly) this.fly.path.apply(this.host.camera, Math.min(1, (now - this.fly.start) / (this.fly.seconds * 1000)));
  }

  /** After the frame's work (perf.end has run). */
  after() {
    const builds = this.host.inFrameBuilds();
    if (this.recording) {
      const perf = this.host.perf;
      const row: LabFrame = { t: this.start, interval: this.previousStart === null ? null : this.start - this.previousStart,
        frame: this.rendererFrame, cpu: perf.lastCpu, gpu: null, passes: null, draws: perf.calls, tris: perf.triangles,
        heap: (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize ?? null,
        sub: Array.from(this.sub, v => Math.round(v * 1000) / 1000), slices: Array.from(this.slices, v => Math.round(v * 1000) / 1000),
        builds: builds - this.builds, bands: this.bands };
      this.frames.push(row); this.pendingGpu.push(row);
    }
    this.builds = builds; this.bands = 0; this.slices.fill(0);
    this.previousStart = this.start;
    if (!this.recorder) streamTrace.drain(); // nobody else drains the shared trace buffer
    this.harvestGpu(false);
    const waiters = this.waiters; this.waiters = [];
    for (const resolve of waiters) resolve();
  }

  private harvestGpu(final: boolean) {
    const keep: LabFrame[] = [];
    for (const row of this.pendingGpu) {
      const s = this.host.perf.sample(row.frame);
      if (s && s.gpu !== null) {
        row.gpu = Math.round(s.gpu * 1000) / 1000;
        row.passes = s.passes ? Object.fromEntries(Object.entries(s.passes).filter((e): e is [string, number] => typeof e[1] === 'number')) : null;
      } else if (!final && this.rendererFrame - row.frame < 240) keep.push(row);
    }
    this.pendingGpu = keep;
  }

  private nextFrame() { return new Promise<void>(resolve => this.waiters.push(resolve)); }
  private async frames_(n: number) { for (let i = 0; i < n; i++) await this.nextFrame(); }

  private mark(name: string) {
    const t = performance.now();
    this.marks[name] = Math.round(t * 10) / 10;
    try { performance.mark(`bench:${name}`); } catch { /* user timing unavailable */ }
    return t;
  }

  private phase(phase: string, progress = '') { this.status.phase = phase; this.status.progress = progress; }

  async run() {
    try {
      const lab = this.host.lab;
      if (lab.mode === 'install') await this.runInstall();
      else if (lab.mode === 'static') await this.runStatic();
      else await this.runFly();
      this.status.phase = 'done'; this.status.done = true;
    } catch (error) {
      this.status.phase = 'error'; this.status.error = String((error as Error)?.stack ?? error);
      if (this.recorder) await this.recorder.stop().catch(() => {});
    } finally {
      this.recording = false; this.fly = null;
    }
    return this.status;
  }

  /** Stream one tile stage through the real installer, retrying 202/429 answers, until committed. */
  private async ensure(id: string, stage: 'preview' | 'full') {
    const { x, z } = this.host.camera.position, limit = performance.now() + this.host.lab.settleLimitMs;
    for (let attempt = 0; ; attempt++) {
      const r = await this.host.coarse.benchInstall(id, stage, x, z);
      if (stage === 'preview' ? r.state === 'draft' : r.full) return r;
      if (stage === 'full' && r.state === 'draft' && !r.preview && !r.full) throw new Error(`${id}: no fragment to install`);
      if (performance.now() > limit) throw new Error(`${id} ${stage}: not installed after ${this.host.lab.settleLimitMs} ms (${r.state}${r.error ? `: ${r.error}` : ''})`);
      await wait(Math.max(250, Math.min(5000, r.retryInMs || 500)));
    }
  }

  /** Load the tile set (previews, then full models, a few at a time as the streamer's slots do). */
  private async loadAll(tiles: string[]) {
    const pool = async (stage: 'preview' | 'full', slots: number) => {
      const queue = [...tiles];
      let done = 0;
      await Promise.all(Array.from({ length: slots }, async () => {
        for (let id = queue.shift(); id; id = queue.shift()) { await this.ensure(id, stage); this.status.progress = `${stage} ${++done}/${tiles.length}`; }
      }));
    };
    this.phase('load');
    await pool('preview', 4);
    await pool('full', 3);
  }

  /** Wait until nothing streams and the resident set has not changed for `ms` (at most `limit`). */
  private async quiet(ms: number, limit: number) {
    const start = performance.now();
    let since = start, signature = this.host.signature();
    for (;;) {
      await this.nextFrame();
      const now = performance.now(), s = this.host.signature();
      if (s !== signature || this.host.busy()) { signature = s; since = now; }
      if (now - since >= ms) return { ms: Math.round(now - start), timedOut: false };
      if (now - start >= limit) return { ms: Math.round(now - start), timedOut: true };
    }
  }

  private async settle() {
    this.phase('settle');
    const lab = this.host.lab;
    this.settleInfo = await this.quiet(lab.settleMs, lab.settleLimitMs);
  }

  private startRecording(meta: Record<string, unknown>) {
    this.recorder = this.host.record({ bench: { mode: this.host.lab.mode, label: this.label(), ...meta } });
    this.status.capture = this.recorder?.id ?? null;
  }

  private label() { return labLabel(this.host.lab, this.host.quality); }

  /** Measure `n` frames into a fresh window; GPU readbacks drain with rendering paused. */
  private async measure(n: number, onFrame?: (i: number) => void) {
    this.frames = []; this.pendingGpu = []; this.previousStart = null;
    this.host.perf.reset();
    this.recording = true;
    const t0 = this.mark(`${this.host.lab.mode}:measure:start`);
    for (let i = 0; i < n; i++) { await this.nextFrame(); onFrame?.(i); }
    this.recording = false;
    const t1 = this.mark(`${this.host.lab.mode}:measure:end`);
    await this.drainGpu();
    return { frames: this.frames, elapsedMs: t1 - t0 };
  }

  private async drainGpu() {
    this.host.pause(true);
    try { await this.host.perf.flush(); } finally { this.host.pause(false); }
    this.harvestGpu(true);
  }

  private common(frames: LabFrame[], elapsedMs?: number) {
    const lab = this.host.lab, cam = this.host.camera;
    return {
      mode: lab.mode, label: this.label(), url: location.search, quality: this.host.quality, aa: this.host.aa,
      off: [...lab.off].sort(), tiles: lab.tiles, seed: lab.seed,
      camera: { x: r2(cam.position.x), y: r2(cam.position.y), z: r2(cam.position.z), E: r2(cam.position.x + E0), N: r2(N0 - cam.position.z), fov: cam.fov },
      settle: this.settleInfo, capture: this.status.capture,
      summary: frameSummary(frames, elapsedMs), passes: passSummary(frames), split: subsystemSplit(frames),
      heap: heapStats(frames.map(f => f.heap)), marks: this.marks,
      ...this.host.info(),
    };
  }

  private async finish(result: Record<string, unknown>) {
    this.phase('save');
    const recorder = this.recorder;
    this.recorder = null;
    if (recorder) {
      await recorder.stop();
      result.capture = recorder.id;
      await this.host.saveResult(recorder.id, result).catch(error => { result.saveError = String(error); });
    }
    this.status.result = result;
    console.info(`BENCH LAB ${JSON.stringify({ label: result.label, capture: result.capture, summary: result.summary })}`);
  }

  private async runStatic() {
    const lab = this.host.lab;
    await this.loadAll(lab.tiles);
    await this.host.resetWorld();
    await this.settle();
    this.startRecording({ frames: lab.frames, warm: lab.warm });
    this.phase('warm');
    await this.frames_(lab.warm);
    this.phase('measure');
    const { frames, elapsedMs } = await this.measure(lab.frames);
    await this.finish(this.common(frames, elapsedMs));
  }

  private async runFly() {
    const lab = this.host.lab;
    await this.loadAll(lab.tiles);
    const path = flyPath(lab.tiles, this.host.camera.position.y, lab.height);
    // Settle at the path's start so the first frames do not stream the start view.
    path.apply(this.host.camera, 0);
    await this.host.resetWorld();
    await this.settle();
    this.startRecording({ seconds: lab.seconds, height: lab.height, stream: lab.stream });
    this.phase('warm');
    await this.frames_(Math.min(lab.warm, 60));
    this.phase('fly');
    this.host.coarse.scheduling = lab.stream;
    this.frames = []; this.pendingGpu = []; this.previousStart = null; this.events = [];
    this.host.perf.reset();
    this.recording = true;
    const t0 = this.mark('fly:measure:start');
    this.fly = { path, start: t0, seconds: lab.seconds };
    while (performance.now() - t0 < lab.seconds * 1000) await this.nextFrame();
    this.recording = false; this.fly = null;
    this.host.coarse.scheduling = false;
    const t1 = this.mark('fly:measure:end');
    await this.drainGpu();
    const frames = this.frames;
    await this.finish({ ...this.common(frames, t1 - t0), seconds: lab.seconds, height: lab.height, stream: lab.stream,
      bands: bandSummary(frames, this.events.filter(e => e.t >= t0 && e.t <= t1)) });
  }

  private async runInstall() {
    const lab = this.host.lab, cam = this.host.camera.position;
    const tiles = lab.tilesGiven ? lab.tiles : byDistance(lab.tiles, cam.x, cam.z);
    await this.host.resetWorld();
    await this.settle();
    this.startRecording({ tiles, quietMs: lab.quietMs });
    this.phase('baseline');
    const base = await this.measure(Math.min(lab.frames, 120));
    const baseline = frameSummary(base.frames, base.elapsedMs);
    const baselineInterval = baseline.intervalMs.p50 ?? 1000 / 60;
    const rows: Record<string, unknown>[] = [];
    const all: LabFrame[] = [];
    this.frames = []; this.pendingGpu = []; this.previousStart = null; this.events = [];
    this.host.perf.reset();
    this.recording = true;
    const installStart = performance.now();
    for (const [n, id] of tiles.entries()) {
      this.phase('install', `${n + 1}/${tiles.length} ${id}`);
      const first = this.frames.length;
      const t0 = this.mark(`tile:${id}:start`);
      let error: string | null = null, previewMs: number | null = null, fullMs: number | null = null;
      let quietPreview: { ms: number; timedOut: boolean } | null = null;
      let quietFull: { ms: number; timedOut: boolean } | null = null;
      try {
        await this.ensure(id, 'preview'); previewMs = Math.round(this.mark(`tile:${id}:preview`) - t0);
        quietPreview = await this.quiet(lab.quietMs, 30_000);
        const tStart = this.mark(`tile:${id}:full-start`);
        await this.ensure(id, 'full'); fullMs = Math.round(this.mark(`tile:${id}:full`) - tStart);
        quietFull = await this.quiet(lab.quietMs, 30_000);
      } catch (e) { error = String((e as Error)?.message ?? e); }
      const t1 = this.mark(`tile:${id}:end`);
      const frames = this.frames.slice(first);
      all.push(...frames);
      // Quiet frames belong to the tile but are not its work: report the stall over the settled interval.
      rows.push({ id, error, previewMs, fullMs,
        totalMs: Math.round(t1 - t0), quiet: { preview: quietPreview, full: quietFull },
        frames: frames.length, longestMs: dist(frames.map(f => f.interval)).max,
        stallMs: r2(frames.reduce((a, f) => a + Math.max(0, (f.interval ?? 0) - baselineInterval), 0)),
        cpuMs: r2(frames.reduce((a, f) => a + f.cpu, 0)), inFrameBuilds: frames.reduce((a, f) => a + f.builds, 0),
        stages: stageSplit(frames, stageWindows(this.events, id, t0, t1), baselineInterval),
        slices: Object.fromEntries(SLICES.map((k, i) => [k, r2(frames.reduce((a, f) => a + (f.slices[i] ?? 0), 0))])),
        loaf: loafSummary(this.loaf, t0, t1), heap: heapStats(frames.map(f => f.heap)) });
      this.status.progress = `${n + 1}/${tiles.length} ${id} done`;
    }
    this.recording = false;
    await this.drainGpu();
    const worst = [...all].sort((a, b) => (b.interval ?? 0) - (a.interval ?? 0)).slice(0, 10).map(f => ({
      t: Math.round(f.t), intervalMs: r2(f.interval ?? 0), cpuMs: r2(f.cpu), gpuMs: f.gpu,
      tile: (rows.find(r => (this.marks[`tile:${r.id}:start`] ?? Infinity) <= f.t && f.t <= (this.marks[`tile:${r.id}:end`] ?? -Infinity)) as { id?: string } | undefined)?.id ?? null,
      sub: Object.fromEntries(SUBSYSTEMS.map((k, i) => [k, r2(f.sub[i] ?? 0)])),
      slices: Object.fromEntries(SLICES.map((k, i) => [k, r2(f.slices[i] ?? 0)])), builds: f.builds,
    }));
    await this.finish({ ...this.common(all), baseline: { intervalMs: baseline.intervalMs, cpuMs: baseline.cpuMs, gpuMs: baseline.gpuMs },
      order: tiles, tiles: rows, worstFrames: worst, loaf: loafSummary(this.loaf, installStart, Infinity) });
  }
}
