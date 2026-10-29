import type { ManifestEntry } from './manifest';

export interface CollisionBundle { url: string; bytes: number; sha256: string; format: 'sorted-col-v1' }

/** Serving bundle: unchanged .col files concatenated by lexicographically sorted cell id. */
export async function splitCollisionBundle(buffer: ArrayBuffer, bundle: CollisionBundle, cells: Record<string, ManifestEntry>) {
  const rows = Object.entries(cells).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
  if (bundle.format !== 'sorted-col-v1' || buffer.byteLength !== bundle.bytes
    || rows.some(([, e]) => !Number.isSafeInteger(e.bytes) || e.bytes <= 0)
    || rows.reduce((sum, [, e]) => sum + e.bytes, 0) !== buffer.byteLength) throw new Error('collision bundle size/format mismatch');
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))].map(b => b.toString(16).padStart(2, '0')).join('');
  if (digest !== bundle.sha256) throw new Error('collision bundle SHA-256 mismatch');
  let offset = 0;
  return new Map(rows.map(([id, entry]) => {
    const cell = buffer.slice(offset, offset + entry.bytes);
    offset += entry.bytes;
    return [id, cell] as const;
  }));
}
