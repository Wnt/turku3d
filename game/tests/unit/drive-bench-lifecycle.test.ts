import { test, expect, vi, afterEach } from 'vitest';
import * as THREE from 'three/webgpu';
import { DriveBench, type LoadedDrive, type DrivePose } from '../../src/qa/drive';
const captures = vi.hoisted(() => ({ stops: [] as ReturnType<typeof vi.fn>[] }));
vi.mock('../../src/telemetry/capture', () => ({ captureAllowed: () => true, DriveCapture: class {
  id = 'test-capture'; stop = vi.fn().mockResolvedValue(undefined);
  constructor() { captures.stops.push(this.stop); }
} }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); captures.stops.length = 0; });
const pose = (t: number): DrivePose => ({ t, paused: false, mode: 'foot', id: null, E: 23459500, N: 6704500, H: 0,
  feet: [0, 0, 0], vel: [0, 0, 0], cam: [0, 2, 3, 0, 0, 0, 1, 60], car: null, speed: null, lights: null });
const source: LoadedDrive = { id: 'source', segment: 1, start: 0, end: 100, poses: [pose(0), pose(100)], meta: {}, settings: { pixelRatio: 1, viewport: [800, 600] } };
function fixture() {
  let now = 0;
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.stubGlobal('window', {}); vi.stubGlobal('innerWidth', 800); vi.stubGlobal('innerHeight', 600);
  const host = { scene: new THREE.Scene(), camera: new THREE.PerspectiveCamera(), game: {}, signature: () => 'stable', busy: () => false,
    configure: vi.fn(), reset: vi.fn().mockResolvedValue(undefined), seek: vi.fn(), begin: vi.fn() };
  const bench = new DriveBench(host);
  return { bench, host, at: (t: number) => { now = t; }, status: () => window.__bench as Record<string, any> };
}

test('bench reports settle/prepare/play, resets measurement and excludes final disk flush from duration', async () => {
  const f = fixture(); await f.bench.load(source);
  expect(f.status().phase).toBe('settle');
  f.bench.after(); f.at(10001); f.bench.after(); expect(f.status().phase).toBe('prepare');
  await Promise.resolve(); expect(f.status().phase).toBe('play'); expect(f.host.begin).toHaveBeenCalledOnce();
  await expect(f.status().run('source', 1)).rejects.toThrow('Wait for the current drive');
  let saved!: () => void;
  captures.stops[0].mockImplementation(() => new Promise<void>(resolve => { saved = resolve; }));
  f.at(10101); f.bench.before(); f.bench.after(); expect(f.status().phase).toBe('drain');
  f.at(15101); saved(); await Promise.resolve();
  expect(f.status().phase).toBe('done'); expect(f.status().done).toBe(true); expect(f.status().measureMs).toBe(100);
});

test('a scene error during preparation cannot resurrect the run when async population reset finishes', async () => {
  const f = fixture(); let prepared!: () => void;
  f.host.reset.mockResolvedValueOnce(undefined).mockImplementationOnce(() => new Promise<void>(resolve => { prepared = resolve; }));
  await f.bench.load(source); f.bench.after(); f.at(10001); f.bench.after();
  window.__qaError = 'tile failed'; f.bench.after();
  expect(f.status().phase).toBe('error');
  prepared(); await Promise.resolve();
  expect(f.status().phase).toBe('error'); expect(captures.stops).toHaveLength(0); expect(f.host.begin).not.toHaveBeenCalled();
});

test('scene error detaches and flushes the active recorder rather than leaving profiling enabled', async () => {
  const f = fixture(); await f.bench.load(source); f.bench.after(); f.at(10001); f.bench.after(); await Promise.resolve();
  window.__qaError = 'tile failed'; f.bench.after();
  expect(f.status().phase).toBe('error'); expect(f.status().capture).toBe('test-capture');
  expect(captures.stops[0]).toHaveBeenCalledOnce(); await Promise.resolve();
});

test('startup failure is published on window.__bench instead of leaving it stuck at loading', async () => {
  const f = fixture(); f.host.configure.mockImplementation(() => { throw Error('bad viewport'); });
  await expect(f.bench.load(source)).rejects.toThrow('bad viewport');
  expect(f.status().phase).toBe('error'); expect(f.status().error).toContain('bad viewport'); expect(f.status().done).toBe(false);
});
