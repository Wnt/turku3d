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
