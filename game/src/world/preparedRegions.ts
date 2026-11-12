import { readPreparedScene, type PreparedScene } from './preparedScene';

export interface PreparedRegion {
  id: string; label: string; url: string; release: string;
  bounds: PreparedScene['bounds']; spawn: PreparedScene['spawn'];
}
export interface PreparedRegions { schema: 'turku-prepared-regions/1'; defaultRegion: string; regions: PreparedRegion[] }
const object = (value: unknown): value is Record<string, any> => !!value && typeof value === 'object' && !Array.isArray(value);
const keys = (value: Record<string, unknown>, expected: string[]) => Object.keys(value).sort().join(',') === expected.sort().join(',');
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const vector = (value: unknown, size: number): value is number[] => Array.isArray(value) && value.length === size && value.every(finite);
const identity = (value: unknown): value is string => typeof value === 'string' && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value);
const fail = (why: string): never => { throw new Error(`Invalid prepared region catalog: ${why}`); };

/** One catalog pins complete scenes. Unknown regions never fall back to a different world. */
export function readPreparedRegions(value: unknown): PreparedRegions {
  if (!object(value) || !keys(value, ['schema', 'defaultRegion', 'regions'])
    || value.schema !== 'turku-prepared-regions/1' || !Array.isArray(value.regions)) fail('schema');
  const catalog = value as Record<string, any>, ids = new Set<string>();
  for (const row of catalog.regions) {
    if (!object(row) || !keys(row, ['id', 'label', 'url', 'release', 'bounds', 'spawn'])) fail('descriptor');
    if (!identity(row.id) || ids.has(row.id)) fail('duplicate or invalid identity');
    ids.add(row.id);
    if (typeof row.label !== 'string' || !row.label.trim()) fail('label');
    if (typeof row.url !== 'string' || !/^\/scenes\/[a-z0-9]+(?:-[a-z0-9]+)*\.scene\.json$/.test(row.url)) fail('same-origin scene URL');
    if (typeof row.release !== 'string' || !/^[0-9a-f]{64}$/.test(row.release)) fail('release');
    const b = row.bounds, p = row.spawn;
    if (!vector(b, 4) || b[0] >= b[2] || b[1] >= b[3]) fail('bounds');
    if (!object(p) || !keys(p, ['position', 'headingDeg', 'vehicle']) || !vector(p.position, 3) || !finite(p.headingDeg)
      || !object(p.vehicle) || !keys(p.vehicle, ['position', 'yaw']) || !vector(p.vehicle.position, 3) || !finite(p.vehicle.yaw)) fail('spawn');
    for (const at of [p.position, p.vehicle.position])
      if (at[0] < b[0] || at[0] >= b[2] || at[2] < b[1] || at[2] >= b[3]) fail('spawn outside bounds');
  }
  if (!ids.has(catalog.defaultRegion)) fail('default region missing');
  return catalog as PreparedRegions;
}

export function selectPreparedRegion(catalog: PreparedRegions, request: string | null): PreparedRegion {
  const id = request ?? catalog.defaultRegion, region = catalog.regions.find(row => row.id === id);
  if (!region) throw new Error(`Unknown prepared region: ${id}`);
  return region;
}

/** Validate the selected document before any collision, textures or geometry are published. */
export async function loadPreparedRegion(page: string, fetcher: typeof fetch = fetch) {
  const pageUrl = new URL(page);
  const get = async (url: string) => {
    const response = await fetcher(url, { signal: AbortSignal.timeout(15000), cache: 'no-cache' });
    if (!response.ok) throw new Error(`Prepared region: HTTP ${response.status} (${url})`);
    return response.json();
  };
  const catalog = readPreparedRegions(await get(new URL('/scenes/regions.json', pageUrl).href));
  const region = selectPreparedRegion(catalog, pageUrl.searchParams.get('region'));
  const url = new URL(region.url, pageUrl).href, data = readPreparedScene(await get(url));
  const same = (a: readonly number[], b: readonly number[]) => a.length === b.length && a.every((n, i) => n === b[i]);
  if (data.id !== region.id || data.release !== region.release || !same(data.bounds, region.bounds)
    || !same(data.spawn.position, region.spawn.position) || data.spawn.headingDeg !== region.spawn.headingDeg
    || !same(data.spawn.vehicle.position, region.spawn.vehicle.position) || data.spawn.vehicle.yaw !== region.spawn.vehicle.yaw)
    throw new Error('Prepared region differs from its pinned scene');
  return { region, url, data, sessionScope: region.id === catalog.defaultRegion ? null : region.id };
}
