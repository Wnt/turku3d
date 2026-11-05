import { describe, expect, it, vi } from 'vitest';
import { optional, retriedByDefault, retryingFetch } from '../../src/core/retryFetch';

const answer = (status: number) => new Response(status === 200 ? 'ok' : 'bad gateway', { status });
const noWait = async () => {};

describe('retryingFetch', () => {
  it('retries 502/503/504 and network errors, then returns the first good answer', async () => {
    const base = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(answer(502)).mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(answer(504)).mockResolvedValueOnce(answer(200));
    const sleeps: number[] = [];
    const res = await retryingFetch(base, { sleep: async ms => { sleeps.push(ms); } })('http://h/assets/cars/passat-b8/lod0.glb');
    expect(res.status).toBe(200);
    expect(base).toHaveBeenCalledTimes(4);
    expect(sleeps).toEqual([1000, 3000, 6000]); // four tries over about 10 s
  });

  it('returns the last gateway answer after four tries, and never retries a 404', async () => {
    const down = vi.fn<typeof fetch>().mockImplementation(async () => answer(502));
    expect((await retryingFetch(down, { sleep: noWait })('http://h/a.glb')).status).toBe(502);
    expect(down).toHaveBeenCalledTimes(4);
    const missing = vi.fn<typeof fetch>().mockResolvedValue(answer(404));
    expect((await retryingFetch(missing, { sleep: noWait })('http://h/a.glb')).status).toBe(404);
    expect(missing).toHaveBeenCalledTimes(1);
  });

  it('rethrows the last network error and stops at once when aborted', async () => {
    const failing = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(retryingFetch(failing, { sleep: noWait })('http://h/a.bin')).rejects.toThrow('Failed to fetch');
    expect(failing).toHaveBeenCalledTimes(4);
    const controller = new AbortController(); controller.abort();
    const aborted = vi.fn<typeof fetch>().mockRejectedValue(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    await expect(retryingFetch(aborted, { sleep: noWait })('http://h/a.bin', { signal: controller.signal })).rejects.toThrow('aborted');
    expect(aborted).toHaveBeenCalledTimes(1);
  });

  it('retries Request inputs (three.js FileLoader) with a fresh clone each time', async () => {
    const seen: Request[] = [];
    const base = vi.fn<typeof fetch>().mockImplementation(async input => { seen.push(input as Request); return answer(seen.length < 2 ? 503 : 200); });
    const res = await retryingFetch(base, { sleep: noWait })(new Request('http://h/assets/character/player.glb'));
    expect(res.status).toBe(200);
    expect(seen).toHaveLength(2);
    expect(seen[0]).not.toBe(seen[1]);
  });

  it('leaves writes and the streams with their own outage handling to plain fetch', async () => {
    const base = vi.fn<typeof fetch>().mockResolvedValue(answer(502));
    const fetchOnce = retryingFetch(base, { sleep: noWait });
    await fetchOnce('http://h/telemetry', { method: 'POST', body: '{}' });
    await fetchOnce('http://h/coarse/t0_1_2/manifest.json');
    expect(base).toHaveBeenCalledTimes(2);
    expect(retriedByDefault(new URL('http://h/assets/audio/rain.ogg'), 'GET')).toBe(true);
    for (const path of ['/coarse/health', '/cities/espoo/coarse/status', '/live.json', '/scores/x.json', '/__capture/a'])
      expect(retriedByDefault(new URL(`http://h${path}`), 'GET')).toBe(false);
    expect(retriedByDefault(new URL('http://h/assets/a.glb'), 'POST')).toBe(false);
  });
});

describe('optional', () => {
  it('turns a failed optional load into its fallback with one warning', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    await expect(optional('walkers', Promise.reject(new Error('HTTP 502')), null)).resolves.toBeNull();
    await expect(optional('walkers', Promise.resolve(3), null)).resolves.toBe(3);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).toContain('HTTP 502');
    warn.mockRestore();
  });
});
