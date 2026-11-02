import { createHash } from 'node:crypto';

export function captureIdentity(value) {
  const canonical = v => Array.isArray(v) ? v.map(canonical)
    : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, canonical(v[k])])) : v;
  return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
}

export function residentTilesMatch(tiles, expected) {
  return Object.entries(expected).every(([id, hash]) => hash && tiles.some(t => t.id === id && t.full && t.visual_sha256 === hash));
}
