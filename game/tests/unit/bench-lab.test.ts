import { afterEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { ABLATIONS, byDistance, parseAblations, parseLab, window3x3 } from '../../src/qa/lab/params';
import { bandSummary, dist, frameSummary, heapStats, stageAt, stageSplit, stageWindows, subsystemSplit, type LabFrame } from '../../src/qa/lab/stats';
import { BenchLab, flyPath, type LabHost } from '../../src/qa/lab/lab';
import { saveLabResult } from '../../src/qa/lab/record';
import { ablateSettings } from '../../src/qa/lab/ablate';
import { parseParams } from '../../src/core/params';
import { QUALITY } from '../../src/render/quality';
import { facadeModuleSwitch } from '../../src/render/facadeModules';
import { streamTrace } from '../../src/telemetry/streamTrace';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const frame = (t: number, interval: number | null, cpu: number, extra: Partial<LabFrame> = {}): LabFrame => ({
  t, interval, frame: t, cpu, gpu: null, passes: null, draws: 100, tris: 1000, heap: null,
  sub: [1, 0, 0, 0, 0, 0, cpu / 2], slices: [0, 0, 0, 0, 0, 0], builds: 0, bands: 0, ...extra });

describe('bench lab URL contract', () => {
  it('defaults to the 3x3 around t0_0_0 and the riverside camera, measuring play cadences', () => {
    const lab = parseLab('?bench=static')!;
    expect(lab.tiles).toEqual(window3x3('t0_0_0'));
    expect(lab.tiles[0]).toBe('t0_-1_-1');
    expect([lab.frames, lab.warm, lab.record, lab.off.size]).toEqual([600, 120, true, 0]);
    const p = parseParams('?bench=static');
    expect(p.lab?.mode).toBe('static');
    expect(p.qa).toMatchObject({ E: 23459674.21, N: 6704241.83, H: 5.39 });
    expect(p.motion).toBe(true);
    expect(p.time.toISOString()).toBe('2026-10-15T11:00:00.000Z');
  });

  it('parses ablations, scenario defaults and their re-enabling', () => {
    expect(parseAblations('shadows, post,shadows')).toEqual(['shadows', 'post']);
    expect(() => parseAblations('shadow')).toThrow(/bad ablation shadow/);
    expect([...parseLab('?bench=install')!.off].sort()).toEqual(['traffic', 'walkers']);
    expect([...parseLab('?bench=fly&on=traffic&off=bloom')!.off].sort()).toEqual(['bloom', 'walkers']);
    expect([...parseLab(`?bench=static&off=${ABLATIONS.join(',')}`)!.off]).toHaveLength(ABLATIONS.length);
  });

  it('takes explicit tiles, poses and sizes and rejects malformed values', () => {
    const lab = parseLab('?bench=install&tiles=t0_2_3,t0_2_4,t0_2_3&size=1280x720&dpr=1.5&record=0&quiet=500')!;
    expect(lab).toMatchObject({ tiles: ['t0_2_3', 't0_2_4'], tilesGiven: true, size: [1280, 720], dpr: 1.5, record: false, quietMs: 500 });
    expect(parseParams('?bench=fly&cam=23459600,6704400,30,90,-10,60').qa).toMatchObject({ E: 23459600, H: 30, yawDeg: 90 });
    expect(parseParams('?bench=static&id=kupittaa').qa?.E).toBeGreaterThan(23460000);
    expect(() => parseLab('?bench=static&tiles=t1_0_0')).toThrow(/tile id/);
    expect(() => parseLab('?bench=static&frames=5')).toThrow(/frames/);
    expect(() => parseLab('?bench=static&size=big')).toThrow(/size/);
    expect(parseLab('?bench=camera&id=riverside')).toBeNull();
    expect(() => parseParams('?bench=nope')).toThrow(/static\|install\|fly/);
  });

  it('orders an install by distance from the camera', () => {
    expect(byDistance(window3x3('t0_0_0'), 125, 125)[0]).toBe('t0_0_0');
    expect(byDistance(['t0_5_5', 't0_1_0', 't0_0_0'], 300, 10)).toEqual(['t0_1_0', 't0_0_0', 't0_5_5']);
  });

  it('switches subsystems off through the quality tier, AA, population caps and facade modules', () => {
    const q = QUALITY.medium as unknown as { reflectionScale: number; bloomStrength: number; lights: { point: number } };
    const saved = { reflectionScale: q.reflectionScale, bloomStrength: q.bloomStrength, point: q.lights.point };
    try {
      const params = { aa: 'taa', traffic: null as number | null, walkers: null as number | null };
      ablateSettings(new Set(['reflection', 'bloom', 'lights', 'traa', 'traffic', 'facades'] as const), 'medium', params, false);
      expect([q.reflectionScale, q.bloomStrength, q.lights.point]).toEqual([0, 0, 0]);
      expect(params).toEqual({ aa: 'off', traffic: 0, walkers: null });
      expect(facadeModuleSwitch.on).toBe(false);
      const fxaa = { aa: 'fxaa', traffic: null, walkers: null };
      ablateSettings(new Set(['traa'] as const), 'medium', fxaa, true);
      expect(fxaa.aa).toBe('fxaa');
      expect(facadeModuleSwitch.on).toBe(true);
    } finally {
      Object.assign(q, { reflectionScale: saved.reflectionScale, bloomStrength: saved.bloomStrength });
      q.lights.point = saved.point; facadeModuleSwitch.on = true;
    }
  });
});

describe('bench lab result math', () => {
  it('uses nearest-rank percentiles with mean and max', () => {
    const d = dist([...Array.from({ length: 100 }, (_, i) => i + 1), null, NaN]);
    expect(d).toEqual({ n: 100, mean: 50.5, p50: 50, p95: 95, p99: 99, max: 100 });
    expect(dist([])).toEqual({ n: 0, mean: null, p50: null, p95: null, p99: null, max: null });
  });

  it('summarises frames: rate, long frames and the longest frame', () => {
    const frames = [frame(0, null, 4), frame(16, 16, 4), frame(32, 16, 5), frame(132, 100, 90), frame(148, 16, 4)];
    const s = frameSummary(frames, 148);
    expect(s.fps).toBeCloseTo(27.03, 2);
    expect(s.longFrames).toEqual({ over33: 1, over50: 1, over100: 0, over250: 0 });
    expect(s.longest).toEqual({ t: 132, intervalMs: 100, cpuMs: 90 });
    expect(s.intervalMs.p99).toBe(100);
  });

  it('counts heap falls as collections only when the samples are precise', () => {
    const precise = heapStats([10_000_123, 12_000_456, 14_000_789, 9_000_001, 11_000_333, 13_000_777, 8_000_111, 9_500_000, 9_900_000, 10_100_000, 10_200_001, 10_300_002]);
    expect(precise.drops).toBe(2);
    expect(precise.precise).toBe(true);
    expect(precise.allocatedMB).toBeCloseTo(9.82, 1);
    const bucketed = heapStats(Array.from({ length: 50 }, (_, i) => i < 25 ? 40 * 1048576 : 41 * 1048576));
    expect(bucketed.precise).toBe(false);
    expect(heapStats([null, null]).start).toBeNull();
  });

  it('splits callback time by subsystem and keeps the unattributed rest', () => {
    const split = subsystemSplit([frame(0, null, 10), frame(16, 16, 20)]);
    expect(split.callback.streaming).toMatchObject({ mean: 1, max: 1 });
    expect(split.callback.render).toMatchObject({ mean: 7.5, max: 10 });
    expect(split.callback.other.mean).toBe(6.5);
    expect(split.callback.render.share).toBe(0.5);
  });

  it('credits frames to the tile stage running when they started, with the stall over the settled interval', () => {
    const events = [
      { t: 100, k: 'tile.manifest', id: 't0_0_0', ms: 50 },
      { t: 400, k: 'tile.glb', id: 't0_0_0', ms: 250 },
      { t: 380, k: 'tile.collision', id: 't0_0_0', ms: 200 },
      { t: 450, k: 'gfx.upload', ms: 20 },
      { t: 300, k: 'tile.glb', id: 't0_9_9', ms: 100 },
    ];
    const windows = stageWindows(events, 't0_0_0', 0, 1000);
    expect(windows.map(w => w.stage)).toEqual(['manifest', 'glb', 'collision', 'gfx.upload']);
    expect(stageAt(windows, 200)).toBe('collision');
    expect(stageAt(windows, 390)).toBe('glb');
    expect(stageAt(windows, 600)).toBe('idle');
    const split = stageSplit([frame(60, 16, 3), frame(200, 80, 60, { builds: 2 }), frame(390, 16, 2), frame(700, 16, 2)], windows, 16);
    expect(split.manifest).toMatchObject({ wallMs: 50, frames: 1, stallMs: 0 });
    expect(split.collision).toMatchObject({ wallMs: 200, frames: 1, cpuMs: 60, stallMs: 64, longestMs: 80, builds: 2 });
    expect(split.glb).toMatchObject({ wallMs: 250, frames: 1 });
    expect(split.idle.frames).toBe(1);
  });

  it('separates frames that carried band switches in a fly run', () => {
    const s = bandSummary([frame(0, 16, 2, { bands: 2 }), frame(16, 40, 30), frame(32, 16, 2)],
      [{ t: 0, k: 'band.tile' }, { t: 1, k: 'band.fade' }, { t: 2, k: 'tile.glb' }]);
    expect(s.events).toEqual({ 'band.tile': 1, 'band.fade': 1 });
    expect(s.framesWithSwaps).toBe(1);
    expect(s.otherFrameMs.max).toBe(40);
  });

  it('flies a closed, deterministic loop with a high leg', () => {
    const a = flyPath(window3x3('t0_0_0'), 5, 40), b = flyPath(window3x3('t0_0_0'), 5, 40);
    const c1 = new THREE.PerspectiveCamera(), c2 = new THREE.PerspectiveCamera();
    a.apply(c1, 0.37); b.apply(c2, 0.37);
    expect(c1.position.toArray()).toEqual(c2.position.toArray());
    a.apply(c1, 0); a.apply(c2, 1);
    expect(c1.position.distanceTo(c2.position)).toBeLessThan(1e-6);
    const heights = Array.from({ length: 50 }, (_, i) => { a.apply(c1, i / 50); return c1.position.y; });
    expect(Math.min(...heights)).toBeGreaterThanOrEqual(45);
    expect(Math.max(...heights)).toBeGreaterThan(300);
  });
});

describe('bench lab capture result', () => {
  it('appends the result to the saved meta as a separate batch', async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return init?.method === 'POST' ? new Response('{}') : new Response(JSON.stringify({ name: 'x', bench: { mode: 'install' } }));
    });
    await saveLabResult('20261103-1-abc', { fps: 40 }, fetcher as unknown as typeof fetch);
    const body = JSON.parse(String(calls[1][1]!.body));
    expect(calls[1][0]).toBe('/__capture/20261103-1-abc');
    expect(body).toEqual({ batch: 1_000_000, meta: { name: 'x', bench: { mode: 'install', result: { fps: 40 } } } });
  });
});

function fakeHost(query: string) {
  let now = 1000;
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.stubGlobal('location', { search: `?${query}` });
  const calls: string[] = [];
  const coarse = {
    scheduling: true, schedulingSeen: [] as boolean[],
    update: vi.fn(),
    async benchInstall(id: string, stage: 'preview' | 'full') {
      calls.push(`${stage}:${id}`);
      const end = streamTrace.begin(stage === 'preview' ? 'tile.manifest' : 'tile.glb', id);
      now += 5; end();
      return { state: 'draft', preview: stage === 'preview', full: stage === 'full', error: null, retryInMs: 0 };
    },
  };
  const perf = { lastCpu: 3, calls: 10, triangles: 20, sample: () => ({ gpu: 4, passes: { beauty: 3, shadows: 1 } }), flush: async () => {}, reset: vi.fn() };
  const saved: Record<string, unknown>[] = [];
  const resets = vi.fn(async () => {});
  const host: LabHost = {
    lab: parseLab(query)!, quality: 'medium', aa: 'taa',
    camera: new THREE.PerspectiveCamera(), coarse, renderer: { info: { frame: 0 } }, perf,
    inFrameBuilds: () => 0, signature: () => 'stable', busy: () => false, resetWorld: resets,
    subsystems: { streaming: [coarse, 'update'] }, pause: () => {},
    record: () => ({ id: 'cap-1', stop: async () => {} }), saveResult: async (_id, result) => { saved.push(result); },
    info: () => ({ backend: 'test' }),
  };
  const lab = new BenchLab(host);
  let frameNo = 0;
  const step = () => {
    now += 16; host.renderer.info.frame = ++frameNo; lab.before(now);
    coarse.schedulingSeen.push(coarse.scheduling); coarse.update();
    if (frameNo % 7 === 0) streamTrace.event('band.tile', 't0_0_0', { to: 'cell' });
    lab.after();
  };
  const run = async () => {
    const pump = setInterval(step, 0);
    try { return await lab.run(); } finally { clearInterval(pump); }
  };
  return { host, lab, coarse, calls, saved, resets, run };
}

describe('bench lab static and fly scenarios', () => {
  it('static loads every tile preview-first, settles, warms and measures exactly N frames', async () => {
    const f = fakeHost('bench=static&tiles=t0_0_0,t0_1_0,t0_0_1&settle=0&frames=40&warm=5');
    const status = await f.run();
    expect(status.error).toBeNull();
    expect(f.calls.slice(0, 3).every(c => c.startsWith('preview:'))).toBe(true);
    expect(f.calls.slice(3).every(c => c.startsWith('full:'))).toBe(true);
    expect(f.resets).toHaveBeenCalledOnce();
    const result = status.result as Record<string, any>;
    expect(result.summary.frames).toBe(40);
    expect(result.summary.cpuMs.p50).toBe(3);
    expect(result.passes.gpuMs.beauty.p50).toBe(3);
    expect(result.split.callback).toHaveProperty('streaming');
    expect(result.label).toBe('static/medium');
    expect(f.saved).toHaveLength(1);
  });

  it('fly follows the path for its duration with the scheduler paused and counts band switches', async () => {
    const f = fakeHost('bench=fly&tiles=t0_0_0&settle=0&seconds=5&warm=0&record=0');
    const status = await f.run();
    expect(status.error).toBeNull();
    const result = status.result as Record<string, any>;
    expect(result.capture).toBeNull();
    expect(result.summary.frames).toBeGreaterThan(250);
    expect(result.bands.events['band.tile']).toBeGreaterThan(30);
    expect(result.bands.framesWithSwaps).toBeGreaterThan(30);
    expect(f.coarse.schedulingSeen.every(s => s === false)).toBe(true);
    expect(f.host.camera.position.y).toBeGreaterThanOrEqual(40);
  });
});

describe('bench lab install scenario', () => {
  it('installs each tile preview then full, timing stages and frames per tile', async () => {
    let now = 1000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    vi.stubGlobal('location', { search: '?bench=install' });
    const calls: string[] = [];
    const state = new Map<string, 'preview' | 'full'>();
    const coarse = {
      scheduling: true,
      update: vi.fn(),
      async benchInstall(id: string, stage: 'preview' | 'full') {
        calls.push(`${stage}:${id}`);
        const end = streamTrace.begin(stage === 'preview' ? 'tile.manifest' : 'tile.glb', id);
        now += 5; end();
        state.set(id, stage);
        return { state: 'draft', preview: stage === 'preview', full: stage === 'full', error: null, retryInMs: 0 };
      },
    };
    const perf = { lastCpu: 3, calls: 10, triangles: 20, sample: () => ({ gpu: 4 }), flush: async () => {}, reset: vi.fn() };
    const saved: Record<string, unknown>[] = [];
    const host: LabHost = {
      lab: parseLab('?bench=install&tiles=t0_0_0,t0_1_0&settle=0&quiet=0&frames=30&warm=0')!, quality: 'medium', aa: 'taa',
      camera: new THREE.PerspectiveCamera(), coarse, renderer: { info: { frame: 0 } }, perf,
      inFrameBuilds: () => 0, signature: () => 'stable', busy: () => false, resetWorld: async () => {},
      subsystems: { streaming: [coarse, 'update'] }, pause: () => {},
      record: () => ({ id: 'cap-1', stop: async () => {} }), saveResult: async (_id, result) => { saved.push(result); },
      info: () => ({ backend: 'test' }),
    };
    const lab = new BenchLab(host);
    expect(coarse.scheduling).toBe(false);
    let frameNo = 0;
    const pump = setInterval(() => { now += 16; host.renderer.info.frame = ++frameNo; lab.before(now); coarse.update(); lab.after(); }, 0);
    const status = await lab.run().finally(() => clearInterval(pump));
    expect(status.error).toBeNull();
    expect(status.done).toBe(true);
    expect(calls).toEqual(['preview:t0_0_0', 'full:t0_0_0', 'preview:t0_1_0', 'full:t0_1_0']);
    const result = status.result as Record<string, any>;
    expect(result.capture).toBe('cap-1');
    expect(saved).toHaveLength(1);
    expect(result.tiles.map((t: any) => t.id)).toEqual(['t0_0_0', 't0_1_0']);
    expect(result.tiles[0].stages).toHaveProperty('manifest');
    expect(result.tiles[0].stages).toHaveProperty('glb');
    expect(result.tiles[0].error).toBeNull();
    expect(result.summary.gpuMs.p50).toBe(4);
    expect(result.baseline.intervalMs.p50).toBe(16);
    expect(Object.keys(result.marks)).toContain('tile:t0_1_0:end');
  });
});
