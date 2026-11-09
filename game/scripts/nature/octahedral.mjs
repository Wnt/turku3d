// Hemi-octahedral tree impostors: FRAMES x FRAMES orthographic views of each tree over the upper hemisphere, with
// albedo + coverage, and the normal + depth towards the viewer, per view. The runtime (src/world/nature/impostor.ts)
// draws one camera-facing quad per tree and blends the three views nearest its view direction, each reprojected onto
// the fragment's view ray with its depth. The view layout below is mirrored there; keep the two in step.
import { half } from './image.mjs';
import { bake } from './raster.mjs';

/** Views per axis of the hemi-octahedral grid; the horizon ring holds 4 (FRAMES - 1) of them. */
export const FRAMES = 8;
/** Albedo texels per view cell (the normal + depth atlas is at half resolution). */
export const FRAME = 128;
/** Each view's image covers the tree's bounding cylinder seen along it, times this: a clear border inside every cell,
 *  so mips and the reprojection do not reach the neighbouring view. */
export const MARGIN = 1.08;
/** Trees per atlas row: blocks of FRAMES x FRAMES views. */
export const BLOCKS = 4;

/** Unit direction (+Y up) of hemi-octahedral coordinates (u, v) in [0, 1]^2: the upper hemisphere's octahedron
 *  unfolded and turned 45 degrees, so the horizon is the square's border. */
export function hemiOctDecode(u, v) {
  const tx = u * 2 - 1, ty = v * 2 - 1;
  const x = (tx + ty) / 2, z = (tx - ty) / 2, y = 1 - Math.abs(x) - Math.abs(z);
  const l = Math.hypot(x, y, z);
  return [x / l, y / l, z / l];
}

/** Hemi-octahedral coordinates of a direction; below the horizon it takes the horizon's. */
export function hemiOctEncode([x, y, z]) {
  const s = Math.abs(x) + Math.max(y, 0) + Math.abs(z) || 1, px = x / s, pz = z / s;
  return [(px + pz) * 0.5 + 0.5, (px - pz) * 0.5 + 0.5];
}

/** View (i, j) of a tree with bounding cylinder radius R and height top: `toward` (unit, from the tree to the viewer),
 *  `right` (horizontal) and `up` span the image; width x height (m) frame the cylinder centred on its middle, and
 *  `depth` is the cylinder's extent along `toward`. */
export function viewFrame(i, j, R, top) {
  const toward = hemiOctDecode(i / (FRAMES - 1), j / (FRAMES - 1));
  const cosE = Math.hypot(toward[0], toward[2]), sinE = toward[1];
  const right = cosE > 1e-6 ? [toward[2] / cosE, 0, -toward[0] / cosE] : [1, 0, 0];
  const up = [toward[1] * right[2] - toward[2] * right[1], toward[2] * right[0] - toward[0] * right[2], toward[0] * right[1] - toward[1] * right[0]];
  return { toward, right, up, centre: [0, top / 2, 0],
    width: 2 * R * MARGIN, height: (top * cosE + 2 * R * sinE) * MARGIN, depth: (top * sinE + 2 * R * cosE) * MARGIN };
}

/** Atlas sizes for `trees` trees: albedo (FRAME per view) and normal + depth (half). */
export function atlasSize(trees) {
  const rows = Math.ceil(trees / BLOCKS);
  return { width: BLOCKS * FRAMES * FRAME, height: rows * FRAMES * FRAME };
}

/**
 * Bake tree `index`'s views into the albedo atlas (rgb, coverage alpha) and the full-resolution normal + depth atlas
 * (view-frame normal in rgb, depth in a: 0.5 at the cylinder's middle, 0 and 1 at its ends along the view).
 * parts: raster.mjs parts of the tree's full LOD, as the runtime draws it (crown fit applied).
 */
export function bakeTree(albedoAtlas, normalAtlas, index, parts, R, top) {
  const bx = (index % BLOCKS) * FRAMES * FRAME, by = Math.floor(index / BLOCKS) * FRAMES * FRAME;
  const n = FRAME * FRAME, known = new Uint8Array(n), colour = new Float32Array(n * 3), nd = new Float32Array(n * 4);
  for (let j = 0; j < FRAMES; j++) for (let i = 0; i < FRAMES; i++) {
    const view = viewFrame(i, j, R, top);
    const { albedo, normal, depth } = bake(parts, view, FRAME, FRAME);
    for (let k = 0; k < n; k++) {
      known[k] = albedo.data[k * 4 + 3] > 0 && Number.isFinite(depth[k]) ? 1 : 0;
      colour.set(albedo.data.subarray(k * 4, k * 4 + 3), k * 3);
      nd.set([normal.data[k * 4], normal.data[k * 4 + 1], normal.data[k * 4 + 2],
        known[k] ? Math.min(1, Math.max(0, 0.5 + depth[k] / view.depth)) : 0.5], k * 4);
    }
    if (!known.some(Boolean)) nd.fill(0.5);
    // Colour, normal and depth spread from the covered texels into the empty ones (push-pull), so filtering, mips and
    // the reprojection never read a missing value; a view with no cover keeps a flat front normal at the middle.
    pushPull(colour, 3, known, FRAME); pushPull(nd, 4, known, FRAME);
    for (let y = 0; y < FRAME; y++) for (let x = 0; x < FRAME; x++) {
      const k = y * FRAME + x, a = ((by + j * FRAME + y) * albedoAtlas.w + bx + i * FRAME + x) * 4;
      albedoAtlas.data.set([colour[k * 3], colour[k * 3 + 1], colour[k * 3 + 2], albedo.data[k * 4 + 3]], a);
      normalAtlas.data.set(nd.subarray(k * 4, k * 4 + 4), a);
    }
  }
}

/** Fill the unknown texels of a size x size image (`channels` floats per texel) with the mean of the known ones
 *  around them, coarser the farther they are (a push-pull pyramid). */
function pushPull(values, channels, known, size) {
  if (size === 1) return;
  const half = size >> 1, sums = new Float32Array(half * half * channels), any = new Uint8Array(half * half);
  for (let y = 0; y < half; y++) for (let x = 0; x < half; x++) {
    let w = 0;
    const out = (y * half + x) * channels;
    for (let j = 0; j < 2; j++) for (let i = 0; i < 2; i++) {
      const k = (y * 2 + j) * size + x * 2 + i;
      if (!known[k]) continue;
      w++;
      for (let c = 0; c < channels; c++) sums[out + c] += values[k * channels + c];
    }
    if (w) { any[y * half + x] = 1; for (let c = 0; c < channels; c++) sums[out + c] /= w; }
  }
  pushPull(sums, channels, any, half);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const k = y * size + x;
    if (known[k]) continue;
    const parent = ((y >> 1) * half + (x >> 1)) * channels;
    for (let c = 0; c < channels; c++) values[k * channels + c] = sums[parent + c];
  }
}

/** The normal + depth atlas as shipped: half resolution. */
export const shippedNormal = normalAtlas => half(normalAtlas);

/**
 * The impostor LOD's mesh: one quad whose corners (uv) the runtime places facing the camera. Its positions frame the
 * tree from the side (bounds only); `_impostor` carries the tree's bounding cylinder and atlas slot: (R, top, index, 0).
 */
export function impostorQuad(MeshBuilder, index, R, top) {
  const mesh = new MeshBuilder();
  const corner = (x, y, u, v) => mesh.vertex([x, y, 0], [0, 0, 1], [u, v], [1, 1, 1], [0, 0, 0, 0]);
  const ids = [corner(-R, 0, 0, 1), corner(R, 0, 1, 1), corner(-R, top, 0, 0), corner(R, top, 1, 0)];
  mesh.tri(ids[0], ids[1], ids[2]); mesh.tri(ids[1], ids[3], ids[2]);
  mesh.impostor = Array.from({ length: 4 }, () => [R, top, index, 0]).flat();
  return mesh;
}

/**
 * The runtime's crown fit (src/world/nature/crowns.ts, NatureLibrary.fitCrowns) applied in place to a tree's full LOD
 * parts, so its views show the crowns the tiles draw; `height` and `radius` are the asset's numbers in nature.json.
 * Returns the fitted horizontal extent (m).
 */
export async function fitTree(parts, species, height, radius) {
  const { crownShape, crownWidening, crownFactor } = await import('../../src/world/nature/crowns.ts');
  const view = mesh => ({ getAttribute: () => ({ count: mesh.positions.length / 3, getX: i => mesh.positions[i * 3],
    getY: i => mesh.positions[i * 3 + 1], getZ: i => mesh.positions[i * 3 + 2] }) });
  const asset = { kind: 'tree', species, height, radius,
    lods: [{ parts: parts.map(p => ({ geometry: view(p.mesh), material: { name: p.cutout ? 'nature-leaves' : 'nature-bark' } })) }] };
  const shape = crownShape(asset), widening = shape && crownWidening(asset, shape);
  let extent = 0;
  for (const { mesh } of parts) for (let i = 0; i < mesh.positions.length; i += 3) {
    const x = mesh.positions[i], y = mesh.positions[i + 1], z = mesh.positions[i + 2];
    const k = shape ? crownFactor(y / height, Math.hypot(x, z) / radius, shape, widening) : 1;
    mesh.positions[i] = x * k; mesh.positions[i + 2] = z * k;
    extent = Math.max(extent, Math.hypot(x * k, z * k));
  }
  return extent;
}
