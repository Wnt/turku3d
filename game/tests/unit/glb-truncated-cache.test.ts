import { expect, test, vi } from 'vitest';
import { completeGlb, fetchGlb } from '../../src/world/loadGlb';

const glb = (bytes: number, declared = bytes) => {
  const buffer = new ArrayBuffer(bytes), view = new DataView(buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, declared, true);
  return buffer;
};

test('a GLB shorter than its header (a cut response cached as complete) is fetched again past the HTTP cache', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const reload = vi.fn(async () => new Response(glb(64)));
  const bytes = await fetchGlb('https://x/shared/a.glb', async () => glb(40, 64), reload);
  expect(completeGlb(bytes)).toBe(true); expect(bytes.byteLength).toBe(64);
  expect(reload).toHaveBeenCalledWith('https://x/shared/a.glb');
  expect(warn).toHaveBeenCalledOnce();
  warn.mockRestore();
});

test('a complete GLB is used as loaded, without a second fetch', async () => {
  const reload = vi.fn(async () => new Response(glb(64)));
  const bytes = await fetchGlb('https://x/a.glb', async () => glb(64), reload);
  expect(bytes.byteLength).toBe(64); expect(reload).not.toHaveBeenCalled();
  expect(completeGlb(glb(12, 13))).toBe(false); expect(completeGlb(new ArrayBuffer(8))).toBe(false);
});
