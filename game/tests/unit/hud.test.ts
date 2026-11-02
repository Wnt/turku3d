import { afterEach, expect, test, vi } from 'vitest';
import { Hud } from '../../src/ui/hud';

afterEach(() => vi.unstubAllGlobals());

test('hidden diagnostics collect no data while nicknames update, and toggling samples current values immediately', () => {
  vi.stubGlobal('document', { body: { classList: { toggle() {} } } });
  let text = '';
  const write = vi.fn((value: string) => { text = value; });
  const element = { hidden: false, classList: { toggle() {} },
    get textContent() { return text; }, set textContent(value: string) { write(value); } };
  const snapshot = vi.fn(() => ({ cpuMs: {}, gpuMs: {}, frameMs: {}, gpuStatus: 'unsupported', drawCalls: 12, triangles: 90 }));
  let fps = 30;
  const read = vi.fn(() => ({ backend: 'WebGPU', fps, frameMs: 33, physMs: 1, visTris: 30, visModels: 1,
    colTiles: 1, colTris: 10, colMB: 1, colWasmMB: 2, E: 23459500, N: 6704500, H: 10, heading: 0,
    mode: 'foot', speed: 0, ground: 'stone', tile: 'c_0_0', perf: { snapshot } as any }));
  const hud = new Hud(element as unknown as HTMLElement, false);
  hud.update(0, 'Jonni', read); hud.update(16, 'Jonni', read);
  expect(read).not.toHaveBeenCalled();
  expect(write).toHaveBeenCalledOnce();
  expect(element.hidden).toBe(false);
  hud.update(32, undefined, read); hud.update(48, undefined, read);
  expect(write).toHaveBeenCalledTimes(2);
  expect(element.hidden).toBe(true);
  hud.toggle(); hud.update(64, 'Jonni', read);
  expect(read).toHaveBeenCalledOnce();
  expect(text).toContain('30 fps');
  fps = 20;
  hud.update(100, 'Jonni', read);
  expect(read).toHaveBeenCalledOnce();
  expect(text).toContain('30 fps');
  hud.update(314, 'Jonni', read);
  expect(read).toHaveBeenCalledTimes(2);
  expect(snapshot).toHaveBeenCalledTimes(2);
  expect(text).toContain('20 fps');
  hud.toggle(); hud.update(315, 'New nickname', read);
  expect(read).toHaveBeenCalledTimes(2);
  expect(text).toBe('New nickname');
});
