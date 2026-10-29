import { test, expect } from 'vitest';
import { splitCollisionBundle } from '../../src/world/collisionBundle';
import type { ManifestEntry } from '../../src/world/manifest';

test('bundle preserves cell boundaries and rejects truncation, corruption and unsupported formats', async () => {
  const data = Uint8Array.of(1, 2, 3, 4, 5).buffer;
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))].map(b => b.toString(16).padStart(2, '0')).join('');
  const bundle = { url: 'bundle.col', sha256: digest, bytes: 5, format: 'sorted-col-v1' as const };
  const cells = { c_2_0: { bytes: 2 }, c_1_0: { bytes: 3 } } as unknown as Record<string, ManifestEntry>;
  const decoded = await splitCollisionBundle(data, bundle, cells);
  expect([...new Uint8Array(decoded.get('c_1_0')!)]).toEqual([1, 2, 3]);
  expect([...new Uint8Array(decoded.get('c_2_0')!)]).toEqual([4, 5]);
  await expect(splitCollisionBundle(data.slice(1), bundle, cells)).rejects.toThrow('size/format');
  await expect(splitCollisionBundle(data, { ...bundle, sha256: 'bad' }, cells)).rejects.toThrow('SHA-256');
  await expect(splitCollisionBundle(data, { ...bundle, format: 'v2' as never }, cells)).rejects.toThrow('size/format');
  await expect(splitCollisionBundle(data, bundle, { c_1_0: { bytes: -1 } } as never)).rejects.toThrow('size/format');
});
