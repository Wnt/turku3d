import { test, expect, vi, afterEach } from 'vitest';
import { DriveCapture } from '../../src/telemetry/capture';
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
test('capture retries failed delivery on stop, writes profiler trace, restores hook and releases observers', async () => {
  const previous = vi.fn(), disconnect = vi.fn();
  const render = { afterFrame: previous, renderer: { info: { frame: 1, memory: {} }, getPixelRatio: () => 1, backend: {} },
    camera: { position: { toArray: () => [1, 2, 3] }, quaternion: { toArray: () => [0, 0, 0, 1] }, fov: 60 }, perf: {} };
  class Observer { static supportedEntryTypes = ['resource']; observe() {} disconnect = disconnect; }
  class Profiler { addEventListener() {} async stop() { return { resources: [], frames: [], stacks: [], samples: [] }; } }
  vi.stubGlobal('window', { __render: render, Profiler }); vi.stubGlobal('PerformanceObserver', Observer);
  vi.stubGlobal('location', { href: 'http://localhost/', origin: 'http://localhost' });
  vi.stubGlobal('navigator', { userAgent: 'test' }); vi.stubGlobal('innerWidth', 800); vi.stubGlobal('innerHeight', 600);
  vi.stubGlobal('addEventListener', vi.fn()); vi.stubGlobal('removeEventListener', vi.fn()); vi.spyOn(console, 'error').mockImplementation(() => {});
  const fetch = vi.fn().mockRejectedValueOnce(Error('offline')).mockResolvedValue({ ok: true }); vi.stubGlobal('fetch', fetch);
  const game = { state: () => ({ paused: true, feet: [1, 2, 3], vel: [0, 0, 0] }), input: { actions: {} } };
  const recorder = new DriveCapture(game);
  render.afterFrame(); recorder.mark('resume');
  await recorder.stop();
  expect(render.afterFrame).toBe(previous); expect(disconnect).toHaveBeenCalled();
  expect(fetch.mock.calls.every(([, options]) => options.keepalive === undefined)).toBe(true);
  const delivered = fetch.mock.calls.slice(1).map(([, options]) => JSON.parse(options.body));
  expect(delivered.flatMap(body => body.frames ?? [])).toHaveLength(1);
  expect(delivered.flatMap(body => body.js ?? [])).toHaveLength(1);
  expect(delivered.flatMap(body => body.marks ?? []).map(mark => mark.k)).toEqual(['pause', 'resume']);
  expect(recorder.error).toBeNull();
});
