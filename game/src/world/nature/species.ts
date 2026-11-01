// Tile vegetation instances carry a shape variant, an inventory id and a measured size; this picks the library
// asset (a Turku species) and a stable per-tree tint for each. Deterministic: every client draws the same tree.

/** FNV-1a of a string, as an unsigned 32-bit integer. */
export function hashId(id: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return h >>> 0;
}

type Share = readonly (readonly [string, number])[];
// Street and park shares in central Turku: lindens and maples line streets and parks, birches everywhere, oaks and
// rowans in parks and yards, willows only by the water.
const SMALL: Share = [['tree_birch', 0.36], ['tree_rowan', 0.24], ['tree_maple', 0.2], ['tree_linden', 0.2]];
const LARGE: Share = [['tree_linden', 0.36], ['tree_maple', 0.28], ['tree_birch', 0.24], ['tree_oak', 0.12]];
const BROAD: Share = [['tree_oak', 0.65], ['tree_linden', 0.35]];
const CONIFER: Share = [['tree_spruce', 0.55], ['tree_pine', 0.45]];
const BUSH: Share = [['shrub_lilac', 0.45], ['shrub_rose', 0.3], ['shrub_juniper', 0.25]];

function pick(share: Share, unit: number): string {
  let acc = 0;
  for (const [name, p] of share) { acc += p; if (unit < acc) return name; }
  return share[share.length - 1][0];
}

/**
 * Library asset for one tile instance. `variant` is the tile prototype name (legacy tiles: the node name);
 * `nearWater` marks a trunk within reach of the tile's river or sea surface, where some broadleaves become willows.
 */
export function natureAsset(variant: string, objectId: string, height: number, nearWater = false): string {
  const unit = (hashId(objectId) % 10007) / 10007;
  if (objectId.startsWith('bush:')) return pick(BUSH, unit);
  if (/pine/i.test(variant)) return pick(CONIFER, unit);
  if (nearWater && height >= 6 && unit < 0.45) return 'tree_willow';
  if (/oak/i.test(variant)) return pick(BROAD, unit);
  if (height < 6) return unit < 0.6 ? 'tree_rowan' : 'tree_birch';
  return pick(/detailed/i.test(variant) || height >= 13 ? LARGE : SMALL, unit);
}

/** Per-tree linear tint (brightness and a little yellow/blue shift), shared by every LOD of that tree. */
export function natureTint(objectId: string, out: [number, number, number] = [1, 1, 1]): [number, number, number] {
  const h = hashId(`${objectId}:tint`);
  const brightness = 0.82 + ((h & 0xff) / 255) * 0.3;
  const warm = (((h >>> 8) & 0xff) / 255 - 0.5) * 0.16;
  out[0] = brightness * (1 + warm); out[1] = brightness * (1 + warm * 0.35); out[2] = brightness * (1 - warm);
  return out;
}

/**
 * Library scale for a tile instance of measured height and crown radius: uniform by height, with the crown allowed
 * to widen or narrow by at most 30 %, so leaves and bark keep their proportions.
 */
export function natureScale(height: number, crownRadius: number, assetHeight: number, assetRadius: number) {
  const k = height / assetHeight;
  const crown = Math.min(1.3 * k, Math.max(0.75 * k, crownRadius / assetRadius));
  return { y: k, xz: crown };
}
