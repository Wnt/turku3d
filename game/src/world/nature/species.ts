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
export function natureAsset(variant: string, objectId: string, height: number, nearWater = false, species = ""): string {
  // Surveyed genus wins over shape and habitat guesses. Elm and ash use the
  // closest available broadleaf crown until their own library assets are ready.
  const genus = species.toLowerCase();
  const known: [RegExp, string][] = [
    [/tilia|lehmus/, 'tree_linden'], [/acer|vaahtera/, 'tree_maple'],
    [/betula|koivu/, 'tree_birch'], [/quercus|tammi|ulmus|jalava/, 'tree_oak'],
    [/fraxinus|saarni/, 'tree_linden'], [/sorbus|pihlaja/, 'tree_rowan'],
    [/salix|paju|poppeli|populus/, 'tree_willow'], [/pinus|mänty/, 'tree_pine'],
    [/picea|abies|kuusi/, 'tree_spruce'],
  ];
  for (const [pattern, asset] of known) if (pattern.test(genus)) return asset;
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

/** Tile barrier prototypes (coarse fences.py, `draft_<style>` material keys) drawn with the fence kit. A chain-link
 *  fence is two tile prototypes: its panel becomes the kit module and its posts are hidden (''). Brick walls and
 *  bollards keep the tile's own geometry (null). */
export function fenceAsset(materialKey: string, height: number): string | null {
  const style = materialKey.replace(/^draft_/, '');
  if (style === 'chain_panel') return 'chain_fence';
  if (style === 'chain') return '';
  if (style === 'wood') return height >= 1.3 ? 'board_fence' : 'picket_fence';
  if (style === 'bars') return height < 0.8 ? 'park_fence' : 'metal_railing';
  if (style === 'rail') return 'metal_railing';
  if (style === 'stone') return 'granite_wall';
  if (style === 'hedge') return 'hedge';
  return null;
}

// Finnish fence paint: white, falu red, ochre and dark green on wood; near-black or dark green on steel.
const WOOD_PAINT: [number, number, number, number][] = [[0.82, 0.81, 0.77, 0.5], [0.40, 0.085, 0.055, 0.25], [0.74, 0.55, 0.24, 0.1], [0.16, 0.24, 0.17, 0.15]];
const METAL_PAINT: [number, number, number, number][] = [[0.05, 0.06, 0.06, 0.7], [0.03, 0.075, 0.05, 0.3]];
const GALVANISED: [number, number, number] = [0.55, 0.57, 0.56];

/** Per-segment paint (wood, metal) or a plain tint, stable for a segment position. */
export function fenceTint(asset: string, key: string): [number, number, number] {
  const unit = (hashId(key) % 10007) / 10007;
  const palette = asset.endsWith('_fence') && asset !== 'park_fence' ? WOOD_PAINT : asset === 'metal_railing' || asset === 'park_fence' ? METAL_PAINT : null;
  if (asset === 'chain_fence') return [...GALVANISED];
  if (!palette) return asset === 'hedge' ? natureTint(key) : [1, 1, 1];
  let acc = 0;
  for (const [r, g, b, p] of palette) { acc += p; if (unit < acc) return [r, g, b]; }
  const [r, g, b] = palette[palette.length - 1];
  return [r, g, b];
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
