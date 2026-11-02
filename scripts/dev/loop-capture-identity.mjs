import { createHash } from 'node:crypto';

export function captureIdentity(value) {
  const canonical = v => Array.isArray(v) ? v.map(canonical)
    : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, canonical(v[k])])) : v;
  return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
}

export function residentTilesMatch(tiles, expected) {
  return Object.entries(expected).every(([id, hash]) => hash && tiles.some(t => t.id === id && t.full && t.visual_sha256 === hash));
}

/** The shared farm answers 202 while building; a partial pass is not a capture input. */
export async function waitForTileManifest(url, tile, timeoutMs, request = fetch,
  pause = ms => new Promise(resolve => setTimeout(resolve, ms))) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const response = await request(url, { cache: 'no-store',
      signal: AbortSignal.timeout(Math.max(1, Math.min(20000, deadline - Date.now()))) });
    if (response.status === 200) {
      const manifest = await response.json();
      if (manifest.pass_kind === 'full' && manifest.visual?.[tile]?.sha256) return manifest;
    } else if (response.status !== 202 && response.status !== 429) {
      throw new Error(`Capture tile ${tile}: HTTP ${response.status}`);
    }
    const retryMs = Math.max(250, Math.min(5000, Number(response.headers.get('retry-after') || 2) * 1000));
    await pause(Math.min(retryMs, Math.max(0, deadline - Date.now())));
  }
  throw new Error(`Capture tile ${tile}: full manifest did not become ready`);
}
