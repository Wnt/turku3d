// Delivery address pool published with the tiles (manifest.addresses, pipeline/turku3d/content/README.md): every
// addressed building in the published area with a street-side drop-off and its route distances from the pizzeria.
import type { WorldPoint } from '../contract';
import type { Manifest } from '../../world/manifest';
import { PIZZERIA } from './locations';

export interface PoolAddress {
  /** ASCII slug of the address, stable across tile builds (saved games store it). */
  id: string;
  address: string;
  /** L0 visual tile of the drop-off. */
  tile: string;
  /** Drop-off: the sidewalk spot in front of the door, outside every footprint. */
  point: WorldPoint;
  /** Where the drop-off meets the walkable street network. */
  street: WorldPoint;
  /** Street-route metres from the pizzeria on foot and by car (null: no drivable route). */
  foot: number;
  car: number | null;
}

interface AddressJson {
  format: string; version: number; frame: string; origin: [number, number];
  addresses: { id: string; address: string; tile: string; point: [number, number]; street: [number, number];
    foot: number; car: number | null }[];
}

/** The pool's origin must be the game's pizzeria, or every distance would be measured from elsewhere. */
const ORIGIN_TOLERANCE_M = 1;

/** Same slug as the pipeline (`turku3d.content.addresses.slug`): `Läntinen Rantakatu 13a` -> `lantinen-rantakatu-13a`. */
export function addressSlug(address: string): string {
  return address.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/** Decodes the published pool; addresses in tiles the manifest does not carry are skipped. */
export function parseAddressPool(json: AddressJson, tiles: (tile: string) => boolean): PoolAddress[] {
  if (json.format !== 'turku3d-addresses' || json.version !== 1 || json.frame !== 'engine-xz') {
    throw new Error(`address pool: unsupported ${json.format} v${json.version} (${json.frame})`);
  }
  const [x, z] = json.origin;
  if (Math.hypot(x - PIZZERIA.point.x, z - PIZZERIA.point.z) > ORIGIN_TOLERANCE_M) {
    throw new Error(`address pool: origin ${x}, ${z} is not the pizzeria`);
  }
  return json.addresses.filter(a => tiles(a.tile)).map(a => ({
    id: a.id, address: a.address, tile: a.tile, point: { x: a.point[0], z: a.point[1] },
    street: { x: a.street[0], z: a.street[1] }, foot: a.foot, car: a.car,
  }));
}

/** The published pool, or an empty one when the build carries none (synthetic dev tiles). */
export async function loadAddressPool(manifest: Pick<Manifest, 'addresses' | 'baseUrl' | 'visual'>): Promise<PoolAddress[]> {
  if (!manifest.addresses) return [];
  const url = new URL(manifest.addresses.url, manifest.baseUrl).href;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`address pool ${url}: HTTP ${res.status}`);
  return parseAddressPool(await res.json() as AddressJson, tile => tile in manifest.visual);
}
