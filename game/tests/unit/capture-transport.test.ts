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

test('failed final delivery stays visible and retry preserves the same capture before another can start', async () => {
  const { captureControl } = await import('../../src/telemetry/capture');
  const { captureMenuItems } = await import('../../src/ui/menu');
  const render = { afterFrame: undefined as (() => void) | undefined,
    renderer: { info: { frame: 1, memory: {} }, getPixelRatio: () => 1, backend: {} }, perf: {} };
  class Observer { static supportedEntryTypes: string[] = []; }
  vi.stubGlobal('window', { __render: render }); vi.stubGlobal('PerformanceObserver', Observer);
  vi.stubGlobal('location', { href: 'http://localhost/' }); vi.stubGlobal('navigator', { userAgent: 'test' });
  vi.stubGlobal('innerWidth', 800); vi.stubGlobal('innerHeight', 600);
  vi.stubGlobal('addEventListener', vi.fn()); vi.stubGlobal('removeEventListener', vi.fn());
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const storage = { getItem: vi.fn(), setItem: vi.fn() }; vi.stubGlobal('sessionStorage', storage);
  const marker = { style: { cssText: '', color: '' }, setAttribute: vi.fn(), remove: vi.fn(), hidden: false, textContent: '', title: '', id: '' };
  vi.stubGlobal('document', { createElement: () => marker });
  const fetch = vi.fn().mockRejectedValue(Error('disk unavailable')); vi.stubGlobal('fetch', fetch);
  const control = captureControl({ state: () => ({ paused: true }) }, { append: vi.fn() } as unknown as HTMLElement);
  const changed = vi.fn(); const unsubscribe = control.subscribe(changed);
  try {
    control.toggle(); const id = control.recorder!.id;
    await vi.waitFor(() => expect(control.recorder!.error).toContain('disk unavailable'));
    expect(marker.textContent).toBe('● REC · SAVE FAILED');
    expect(control.detail).toContain('still buffered');
    control.toggle();
    await vi.waitFor(() => expect(control.retry).toBeTypeOf('function'));
    expect(render.afterFrame).toBeUndefined(); expect(control.on).toBe(false);
    expect(marker.hidden).toBe(false); expect(marker.textContent).toBe('SAVE FAILED');
    expect(control.enabled).toBe(false);
    control.toggle(); expect(control.recorder).toBeNull();
    const items = captureMenuItems(control, vi.fn());
    expect(items.find(item => item.id === 'performance-capture')!.enabled).toBe(false);
    const retry = items.find(item => item.id === 'capture-retry')!;
    expect(retry.label).toBe('Retry saving capture');
    fetch.mockResolvedValue({ ok: true }); retry.run();
    expect(control.detail).toContain('Saving');
    await vi.waitFor(() => expect(control.detail).toBe(`Saved as ${id}`));
    expect(marker.hidden).toBe(true); expect(control.enabled).toBe(true); expect(control.retry).toBeUndefined();
    expect(control.lastRecorder!.id).toBe(id); expect(control.lastRecorder!.error).toBeNull();
    expect(changed).toHaveBeenCalled();
  } finally { unsubscribe(); control.dispose(); }
});

test('switching Off waits for a profiler rollover already in progress and saves its trace once', async () => {
  let sampleBufferFull!: () => void, complete!: (trace: object) => void;
  const profilerStop = new Promise<object>(resolve => { complete = resolve; });
  class Profiler {
    addEventListener(_type: string, listener: () => void) { sampleBufferFull = listener; }
    stop() { return profilerStop; }
  }
  class Observer { static supportedEntryTypes: string[] = []; }
  const render = { renderer: { getPixelRatio: () => 1, backend: {} }, afterFrame: undefined };
  vi.stubGlobal('window', { __render: render, Profiler }); vi.stubGlobal('PerformanceObserver', Observer);
  vi.stubGlobal('location', { href: 'http://localhost/' }); vi.stubGlobal('navigator', { userAgent: 'test' });
  vi.stubGlobal('innerWidth', 800); vi.stubGlobal('innerHeight', 600);
  vi.stubGlobal('addEventListener', vi.fn()); vi.stubGlobal('removeEventListener', vi.fn());
  const fetch = vi.fn().mockResolvedValue({ ok: true }); vi.stubGlobal('fetch', fetch);
  const recorder = new DriveCapture({ state: () => ({ paused: true }) });
  sampleBufferFull();
  let saved = false;
  const stopped = recorder.stop().then(() => { saved = true; });
  try {
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(saved).toBe(false);
  } finally {
    complete({ resources: [], frames: [], stacks: [], samples: [{ timestamp: 1 }] });
    await stopped;
  }
  const traces = fetch.mock.calls.flatMap(([, options]) => JSON.parse(options.body).js ?? []);
  expect(traces).toHaveLength(1); expect(traces[0].trace.samples).toHaveLength(1);
  expect(render.afterFrame).toBeUndefined();
});

test('page exit beacons the unacknowledged fetch batch as well as queued rows', async () => {
  class Observer { static supportedEntryTypes: string[] = []; }
  const render = { renderer: { getPixelRatio: () => 1, backend: {} }, afterFrame: undefined };
  vi.stubGlobal('window', { __render: render }); vi.stubGlobal('PerformanceObserver', Observer);
  vi.stubGlobal('location', { href: 'http://localhost/' });
  vi.stubGlobal('innerWidth', 800); vi.stubGlobal('innerHeight', 600);
  const addEventListener = vi.fn(); vi.stubGlobal('addEventListener', addEventListener); vi.stubGlobal('removeEventListener', vi.fn());
  const beacon = vi.fn().mockReturnValue(true); vi.stubGlobal('navigator', { userAgent: 'test', sendBeacon: beacon });
  let acknowledge!: (response: object) => void;
  const fetch = vi.fn().mockImplementation(() => new Promise(resolve => { acknowledge = resolve; })); vi.stubGlobal('fetch', fetch);
  const recorder = new DriveCapture({ state: () => ({ paused: true }) });
  const body = fetch.mock.calls[0][1].body;
  const hide = addEventListener.mock.calls.find(([type]) => type === 'pagehide')![1];
  try {
    hide();
    const bodies = await Promise.all(beacon.mock.calls.map(async ([, blob]) => (blob as Blob).text()));
    expect(bodies).toContain(body);
    expect(JSON.parse(body).batch).toBe(0);
  } finally {
    acknowledge({ ok: true }); await recorder.stop();
  }
});

test('viewport stats follow renderer resize while start metadata retains its original size', async () => {
  let width = 800, height = 600;
  class Observer { static supportedEntryTypes: string[] = []; }
  const render = { afterFrame: undefined as (() => void) | undefined,
    renderer: { info: { frame: 1, memory: {} }, getPixelRatio: () => 1, backend: {}, getSize: (out: any) => out.set(width, height) },
    camera: { position: { toArray: () => [1, 2, 3] }, quaternion: { toArray: () => [0, 0, 0, 1] }, fov: 60 }, perf: {} };
  vi.stubGlobal('window', { __render: render }); vi.stubGlobal('PerformanceObserver', Observer);
  vi.stubGlobal('location', { href: 'http://localhost/' }); vi.stubGlobal('navigator', { userAgent: 'test' });
  vi.stubGlobal('innerWidth', 1280); vi.stubGlobal('innerHeight', 720);
  vi.stubGlobal('addEventListener', vi.fn()); vi.stubGlobal('removeEventListener', vi.fn());
  const fetch = vi.fn().mockResolvedValue({ ok: true }); vi.stubGlobal('fetch', fetch);
  const recorder = new DriveCapture({ state: () => ({ paused: false, feet: [0, 0, 0], vel: [0, 0, 0] }) });
  width = 1600; height = 900; render.afterFrame!(); await recorder.stop();
  const batches = fetch.mock.calls.map(([, options]) => JSON.parse(options.body));
  expect(batches.find(batch => batch.meta).meta.viewport).toEqual([800, 600]);
  expect(batches.flatMap(batch => batch.stats ?? [])[0].viewport).toEqual([1600, 900]);
});

test.each([false, true])('capture reports JS sampling support=%s while frame/pose capture still saves', async supported => {
  const { captureControl } = await import('../../src/telemetry/capture');
  class Observer { static supportedEntryTypes: string[] = []; }
  class Profiler { addEventListener() {} async stop() { return { resources: [], frames: [], stacks: [], samples: [] }; } }
  const render = { afterFrame: undefined as (() => void) | undefined,
    renderer: { info: { frame: 1, memory: {} }, getPixelRatio: () => 1, backend: {} }, perf: {},
    camera: { position: { toArray: () => [0, 2, 3] }, quaternion: { toArray: () => [0, 0, 0, 1] }, fov: 60 } };
  vi.stubGlobal('window', { __render: render, Profiler: supported ? Profiler : undefined }); vi.stubGlobal('PerformanceObserver', Observer);
  vi.stubGlobal('location', { href: 'http://localhost/' }); vi.stubGlobal('navigator', { userAgent: 'test' });
  vi.stubGlobal('innerWidth', 800); vi.stubGlobal('innerHeight', 600);
  vi.stubGlobal('addEventListener', vi.fn()); vi.stubGlobal('removeEventListener', vi.fn());
  vi.stubGlobal('sessionStorage', { getItem: vi.fn(), setItem: vi.fn() });
  const marker = { style: { cssText: '', color: '' }, setAttribute: vi.fn(), remove: vi.fn(), hidden: false, textContent: '', title: '', id: '' };
  vi.stubGlobal('document', { createElement: () => marker });
  const fetch = vi.fn().mockResolvedValue({ ok: true }); vi.stubGlobal('fetch', fetch);
  const control = captureControl({ state: () => ({ paused: true, feet: [0, 0, 0], vel: [0, 0, 0] }) }, { append: vi.fn() } as unknown as HTMLElement);
  try {
    control.toggle(); const id = control.recorder!.id;
    expect(control.on).toBe(true); expect(control.recorder!.jsSamplingAvailable).toBe(supported);
    expect(marker.textContent).toBe(supported ? '● REC' : '● REC · NO JS SAMPLES');
    expect(marker.style.color).toBe(supported ? '#ff6868' : '#ffd27a');
    if (supported) expect(control.detail).not.toContain('unavailable');
    else { expect(control.detail).toContain('JS sampling unavailable'); expect(marker.title).toContain('telemetry are still recording'); }
    render.afterFrame!(); control.toggle();
    await vi.waitFor(() => expect(control.detail).toBe(`Saved as ${id}`));
    expect(marker.hidden).toBe(true);
    const batches = fetch.mock.calls.map(([, options]) => JSON.parse(options.body));
    expect(batches.find(batch => batch.meta).meta.jsProfiler.available).toBe(supported);
    expect(batches.flatMap(batch => batch.frames ?? [])).toHaveLength(1);
    expect(batches.flatMap(batch => batch.poses ?? [])).toHaveLength(1);
  } finally { control.dispose(); }
});

test.each([true, false])('GPU pass stats preserve frame ownership and snapshot supported=%s', async supported => {
  class Observer { static supportedEntryTypes: string[] = []; }
  const passes = { prepass: 1.5, beauty: 4.25 };
  const render = { afterFrame: undefined as (() => void) | undefined,
    renderer: { info: { frame: 301, memory: {} }, getPixelRatio: () => 1, backend: {} },
    camera: { position: { toArray: () => [0, 2, 3] }, quaternion: { toArray: () => [0, 0, 0, 1] }, fov: 60 },
    perf: { lastCpu: 2, lastGpu: 6, lastGpuFrame: 299, calls: 10, triangles: 20,
      lastGpuPasses: supported ? passes : null, lastGpuPassFrame: 298 } };
  vi.stubGlobal('window', { __render: render }); vi.stubGlobal('PerformanceObserver', Observer);
  vi.stubGlobal('location', { href: 'http://localhost/' }); vi.stubGlobal('navigator', { userAgent: 'test' });
  vi.stubGlobal('innerWidth', 800); vi.stubGlobal('innerHeight', 600);
  vi.stubGlobal('addEventListener', vi.fn()); vi.stubGlobal('removeEventListener', vi.fn());
  const fetch = vi.fn().mockResolvedValue({ ok: true }); vi.stubGlobal('fetch', fetch);
  const recorder = new DriveCapture({ state: () => ({ paused: false, feet: [0, 0, 0], vel: [0, 0, 0] }) });
  render.afterFrame!();
  passes.prepass = 99; // A later readback reusing the map cannot rewrite this queued sample.
  await recorder.stop();
  const batches = fetch.mock.calls.map(([, options]) => JSON.parse(options.body));
  const stats = batches.flatMap(batch => batch.stats ?? []);
  expect(stats[0].gpuPassMs).toEqual(supported ? { prepass: 1.5, beauty: 4.25 } : null);
  expect(stats[0].gpuPassFrame).toBe(supported ? 298 : null);
  const frames = batches.flatMap(batch => batch.frames ?? []);
  expect(frames[0]).toHaveLength(7);
  expect(frames[0].slice(1)).toEqual([301, 2, 6, 299, 10, 20]);
});
