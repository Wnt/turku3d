import { localFeature } from '../core/localOnly';

// The recorder boundary also supports the QA replay host, which exposes the same state shape as play.
type Host = Record<string, any>;
type Rows = Record<'frames' | 'poses' | 'loaf' | 'stats' | 'marks' | 'js' | 'network', any[]>;
interface SelfProfiler { stop(): Promise<unknown>; addEventListener(type: string, fn: () => void): void }
const empty = (): Rows => ({ frames: [], poses: [], loaf: [], stats: [], marks: [], js: [], network: [] });
const R = (x: number | null | undefined, d = 3) => x == null ? null : Math.round(x * 10 ** d) / 10 ** d;
export const captureAllowed = () => import.meta.env.VITE_PUBLIC_RELEASE !== '1' && localFeature(location, 'capture');
const KEY = 'turku3d.performanceCapture';
export class DriveCapture {
  readonly id = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '').replace(/^(\d{8})(\d+)/, '$1-$2') + '-' + Math.random().toString(36).slice(2, 8);
  private rows = empty();
  private batch = 0;
  private pending: string[] = [];
  private sending = false;
  private inFlight: string | null = null;
  private active = true;
  private previous = window.__render!.afterFrame;
  private observer?: PerformanceObserver;
  private resources?: PerformanceObserver;
  private profiler?: SelfProfiler;
  private profilerDrain: Promise<void> = Promise.resolve();
  private profilerStart = 0;
  private profilerTimer?: ReturnType<typeof setTimeout>;
  private timer: ReturnType<typeof setInterval>;
  private lastPose = -Infinity;
  private lastStat = -Infinity;
  private paused: boolean;
  private meta: Host;
  private metaDirty = true;
  error: string | null = null;
  onChange?: () => void;
  private tick = () => { this.previous?.(); if (this.active) this.frame(); };
  private hide = () => { void this.rollProfiler(false).finally(() => this.flush(true)); this.flush(true); };
  private show = () => { void this.profilerDrain.then(() => { if (this.active && !this.profiler) this.startProfiler(); }); };
  constructor(private game: Host, extra: Host = {}) {
    const r = window.__render!, s = game.state();
    this.paused = s.paused;
    this.meta = { name: this.id, url: location.href, commit: import.meta.env.VITE_COMMIT ?? null,
      start: { performanceNow: performance.now(), wall: Date.now() }, replay: game.replay?.snapshot?.() ?? null,
      hour: game.dayClock?.hour ?? null, params: game.params, backend: game.backend, ua: navigator.userAgent, three: game.three,
      clockMode: game.dayClock?.captureMode?.() ?? null,
      quality: game.params?.quality, pixelRatio: r.renderer.getPixelRatio(), viewport: [innerWidth, innerHeight],
      gpu: (() => { const info = (r.renderer.backend as Host)?.device?.adapterInfo; return info ? Object.fromEntries(['vendor', 'architecture', 'device', 'description'].map(k => [k, info[k] ?? null])) : null; })(), ...extra };
    this.mark(this.paused ? 'pause' : 'resume');
    window.__render!.afterFrame = this.tick;
    if (PerformanceObserver.supportedEntryTypes?.includes('long-animation-frame')) {
      this.observer = new PerformanceObserver(list => {
        for (const e of list.getEntries() as any) this.rows.loaf.push({ t: R(e.startTime, 1), d: R(e.duration, 1), block: R(e.blockingDuration, 1),
          render: R(e.renderStart, 1), style: R(e.styleAndLayoutStart, 1), scripts: e.scripts.map((s: any) => ({ d: R(s.duration, 1), fn: s.sourceFunctionName,
            url: s.sourceURL.split('/').slice(-3).join('/'), pos: s.sourceCharPosition, inv: s.invoker, type: s.invokerType, forced: R(s.forcedStyleAndLayoutDuration, 1) })) });
      });
      this.observer.observe({ type: 'long-animation-frame', buffered: false });
    }
    if (PerformanceObserver.supportedEntryTypes?.includes('resource')) {
      this.resources = new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          const e = entry as PerformanceResourceTiming;
          if (e.name.includes('/__capture/')) continue;
          this.rows.network.push({ t: R(e.startTime, 1), d: R(e.duration, 1), url: e.name, type: e.initiatorType,
            status: e.responseStatus, transfer: e.transferSize, encoded: e.encodedBodySize, decoded: e.decodedBodySize,
            responseStart: R(e.responseStart, 1), responseEnd: R(e.responseEnd, 1) });
        }
      });
      this.resources.observe({ type: 'resource', buffered: false });
    }
    this.startProfiler();
    this.timer = setInterval(() => this.flush(), 5000);
    addEventListener('pagehide', this.hide);
    addEventListener('pageshow', this.show);
    this.flush();
  }
  mark(k: 'pause' | 'resume') { this.rows.marks.push({ k, t: performance.now() }); this.paused = k === 'pause'; }
  private frame() {
    const r = window.__render!, perf = r.perf, cam = r.camera, t = performance.now(), g = this.game;
    this.rows.frames.push([R(t, 2), r.renderer.info.frame, R(perf.lastCpu, 2), R(perf.lastGpu, 2), perf.lastGpuFrame, perf.calls, perf.triangles]);
    if (t - this.lastPose >= 50) {
      this.lastPose = t;
      const s = g.state(), lat = g.physics?.latest, a = g.input?.actions ?? {}, v = s.vehicle;
      if (s.paused !== this.paused) this.mark(s.paused ? 'pause' : 'resume');
      this.rows.poses.push({ t: R(t, 1), f: r.renderer.info.frame, mode: s.mode, paused: s.paused, id: s.drivenId ?? null,
        E: R(s.E), N: R(s.N), H: R(s.H), feet: s.feet.map((x: number) => R(x)), vel: s.vel.map((x: number) => R(x)),
        car: v ? [...v.pos, v.rot.x, v.rot.y, v.rot.z, v.rot.w, v.linvel.x, v.linvel.y, v.linvel.z].map(x => R(x, 4)) : null,
        speed: v ? R(v.speed, 2) : null, lights: v?.lights ?? null,
        cam: [...cam.position.toArray().map(x => R(x)), ...cam.quaternion.toArray().map(x => R(x, 5)), R(cam.fov, 2)],
        chaseYaw: R(s.camera?.yaw, 4), orbit: [R(g.orbit?.yaw, 4), R(g.orbit?.pitch, 4)],
        input: { throttle: a.throttle, brake: a.brake, steer: R(a.steer, 3), handbrake: a.handbrake, horn: a.horn, lookX: R(g.input?.lookX, 3), lookY: R(g.input?.lookY, 3), source: s.inputSource },
        phys: { stepMs: R(lat?.stepMs, 2), substeps: lat?.substeps ?? null, traffic: lat?.vehicles?.length ?? null } });
    }
    if (t - this.lastStat >= 1000) {
      this.lastStat = t;
      const s = g.state(), m = r.renderer.info.memory, coarse = g.coarse;
      this.rows.stats.push({ t: R(t, 1), heap: (performance as any).memory?.usedJSHeapSize ?? null, geometries: m.geometries, textures: m.textures,
        visModels: s.visModels, visTris: s.visTris, tilesSettled: s.tilesSettled, frameEma: R(s.frameMs, 2),
        coarse: coarse?.entries ? [...coarse.entries.values()].reduce((o: Host, e: any) => (o[e.state] = (o[e.state] ?? 0) + 1, o), {}) : null,
        pixelRatio: r.renderer.getPixelRatio(), viewport: this.meta.viewport });
    }
  }
  private startProfiler() {
    if (!this.active) return;
    try {
      const Profiler = (window as any).Profiler;
      if (!Profiler) throw Error('JS Self-Profiling API unavailable');
      this.profilerStart = performance.now();
      this.profiler = new Profiler({ sampleInterval: 10, maxBufferSize: 10000 });
      this.meta.jsProfiler = { available: true, sampleInterval: 10 }; this.metaDirty = true;
      this.profiler!.addEventListener('samplebufferfull', () => { void this.rollProfiler(true); });
      this.profilerTimer = setTimeout(() => { void this.rollProfiler(true); }, 15000);
    } catch (e) { this.meta.jsProfiler = { available: false, error: String(e) }; this.metaDirty = true; }
  }
  private rollProfiler(restart: boolean): Promise<void> {
    clearTimeout(this.profilerTimer);
    const profiler = this.profiler, start = this.profilerStart;
    this.profiler = undefined;
    if (!profiler) return this.profilerDrain;
    const previous = this.profilerDrain;
    const draining = (async () => {
      try { const trace = await profiler.stop(); this.rows.js.push({ t: start, end: performance.now(), trace }); }
      catch (e) { this.rows.marks.push({ k: 'profiler-error', t: performance.now(), error: String(e) }); }
      if (restart && this.active) this.startProfiler();
      this.flush();
    })();
    // Switching Off during a rollover must await its trace before reporting Saved.
    this.profilerDrain = Promise.all([previous, draining]).then(() => {});
    return this.profilerDrain;
  }
  flush(beacon = false) {
    const rows = this.rows; this.rows = empty();
    // Keep normal batches within the beacon quota; a large profiler trace uses ordinary fetch.
    let chunk: Host = { batch: this.batch++ };
    if (this.metaDirty) { chunk.meta = this.meta; this.metaDirty = false; }
    let bytes = JSON.stringify(chunk).length;
    const push = () => { this.pending.push(JSON.stringify(chunk)); chunk = { batch: this.batch++ }; bytes = 0; };
    for (const [file, values] of Object.entries(rows)) for (const row of values) {
      const n = JSON.stringify(row).length;
      if (bytes + n > 48000 && Object.keys(chunk).length > 1) push();
      (chunk[file] ??= []).push(row); bytes += n;
    }
    if (Object.keys(chunk).length > 1) push();
    if (beacon) {
      // Navigation may cancel ordinary fetch. Re-submit its batch too; the server deduplicates by batch id.
      const send = (body: string) => navigator.sendBeacon(`/__capture/${this.id}`, new Blob([body], { type: 'application/json' }));
      if (this.inFlight) send(this.inFlight);
      this.pending = this.pending.filter(body => !send(body));
    }
    this.onChange?.();
    void this.send();
  }
  private async send() {
    if (this.sending) return;
    this.sending = true;
    try {
      while (this.pending.length) {
        const body = this.pending.shift()!; this.inFlight = body;
        const response = await fetch(`/__capture/${this.id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body }).catch(e => { this.pending.unshift(body); throw e; });
        if (!response.ok) { this.pending.unshift(body); throw Error(`Capture storage: HTTP ${response.status}`); }
        this.inFlight = null; this.error = null;
      }
    } catch (e) { this.error = String(e); console.error(this.error); }
    finally { this.inFlight = null; this.sending = false; this.onChange?.(); }
  }
  async stop() {
    this.active = false; clearInterval(this.timer); clearTimeout(this.profilerTimer);
    this.observer?.disconnect(); this.resources?.disconnect(); removeEventListener('pagehide', this.hide); removeEventListener('pageshow', this.show);
    if (window.__render?.afterFrame === this.tick) window.__render.afterFrame = this.previous;
    await this.rollProfiler(false); this.flush();
    while (this.sending) await new Promise(resolve => setTimeout(resolve, 25));
    if (this.pending.length) await this.send();
    if (this.pending.length) throw Error(this.error ?? 'Capture storage failed');
  }
}

export function captureControl(game: Host, parent: HTMLElement) {
  const marker = document.createElement('span'); marker.id = 'capture-rec';
  marker.setAttribute('role', 'status'); marker.setAttribute('aria-live', 'polite');
  marker.style.cssText = 'position:fixed;right:18px;top:14px;font:600 13px monospace;z-index:10;pointer-events:none';
  marker.hidden = true; parent.append(marker);
  let recorder: DriveCapture | null = null;
  let lastRecorder: DriveCapture | null = null;
  let lastState: 'saving' | 'saved' | 'error' | null = null;
  let disposed = false;
  const listeners = new Set<() => void>();
  const changed = () => {
    if (disposed) return;
    const error = recorder?.error || (lastState === 'error' ? lastRecorder?.error : null);
    marker.hidden = !recorder && lastState !== 'saving' && !error;
    marker.textContent = error ? `${recorder ? '● REC · ' : ''}SAVE FAILED` : recorder ? '● REC' : 'SAVING…';
    marker.style.color = error || !recorder ? '#ffd27a' : '#ff6868';
    marker.title = error ? 'Open the pause menu to retry saving your drive.' : recorder?.id ?? lastRecorder?.id ?? '';
    for (const listener of listeners) listener();
  };
  const save = (capture: DriveCapture) => {
    lastState = 'saving'; changed();
    void capture.stop().then(() => { lastState = 'saved'; changed(); })
      .catch(error => { lastState = 'error'; console.error(error); changed(); });
  };
  const control = {
    get recorder() { return recorder; },
    get on() { return !!recorder; },
    get lastRecorder() { return lastRecorder; },
    get enabled() { return !!recorder || (lastState !== 'saving' && lastState !== 'error'); },
    get detail() {
      if (recorder?.error) return 'Saving failed. Your drive is still buffered; switch Off to retry.';
      if (recorder) return `Recording ${recorder.id}`;
      if (lastState === 'saving') return `Saving ${lastRecorder!.id}…`;
      if (lastState === 'error') return `Save failed for ${lastRecorder!.id}. Retry below; your drive is still buffered.`;
      if (lastState === 'saved') return `Saved as ${lastRecorder!.id}`;
      return 'Save drive and profiling data on this computer';
    },
    get retry(): (() => void) | undefined {
      return lastState === 'error' ? () => save(lastRecorder!) : undefined;
    },
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    toggle() {
      if (!control.enabled) return;
      if (recorder) {
        lastRecorder = recorder; recorder = null;
        save(lastRecorder);
      } else {
        recorder = new DriveCapture(game); recorder.onChange = changed;
        changed();
      }
      try { sessionStorage.setItem(KEY, recorder ? '1' : '0'); } catch {}
    },
    mark(k: 'pause' | 'resume') { recorder?.mark(k); },
    dispose() {
      disposed = true; listeners.clear();
      if (recorder) void recorder.stop().catch(console.error);
      marker.remove();
    },
  };
  try { if (sessionStorage.getItem(KEY) === '1') control.toggle(); } catch {}
  return control;
}
