import { afterEach, expect, test, vi } from 'vitest';
import { isolatePage } from '../../src/core/isolation';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
function browser() {
  const sw = Object.assign(new EventTarget(), { controller: null as null | { scriptURL: string }, register: vi.fn().mockResolvedValue({}) });
  const reload = vi.fn();
  vi.stubGlobal('crossOriginIsolated', false);
  vi.stubGlobal('isSecureContext', true);
  vi.stubGlobal('navigator', { serviceWorker: sw });
  vi.stubGlobal('document', { baseURI: 'https://example.test/turku-game/game/?scene=dev' });
  vi.stubGlobal('location', { reload });
  return { sw, reload };
}

test('isolated local hosts do not install a service worker', async () => {
  const { sw } = browser(); vi.stubGlobal('crossOriginIsolated', true);
  await isolatePage(); expect(sw.register).not.toHaveBeenCalled();
});

test('a controlled unsupported browser falls back without a reload loop', async () => {
  const { sw, reload } = browser();
  sw.controller = { scriptURL: 'https://example.test/turku-game/game/isolation.js' };
  await isolatePage(); expect(sw.register).not.toHaveBeenCalled(); expect(reload).not.toHaveBeenCalled();
});

test('registration is subpath-relative and a control timeout does not block startup', async () => {
  vi.useFakeTimers(); const { sw, reload } = browser();
  const pending = isolatePage();
  expect(sw.register.mock.calls[0][0].href).toBe('https://example.test/turku-game/game/isolation.js');
  expect(sw.register.mock.calls[0][1]).toEqual({ scope: './', updateViaCache: 'none' });
  await vi.advanceTimersByTimeAsync(4000); await pending;
  expect(reload).not.toHaveBeenCalled();
  sw.controller = { scriptURL: 'https://example.test/turku-game/game/isolation.js' };
  sw.dispatchEvent(new Event('controllerchange'));
  expect(reload).not.toHaveBeenCalled();
});

test('a different controlling worker can be replaced without storage access', async () => {
  const { sw, reload } = browser();
  sw.controller = { scriptURL: 'https://example.test/other.js' };
  void isolatePage();
  sw.controller = { scriptURL: 'https://example.test/turku-game/game/isolation.js' };
  sw.dispatchEvent(new Event('controllerchange'));
  await Promise.resolve();
  expect(reload).toHaveBeenCalledTimes(1);
});
