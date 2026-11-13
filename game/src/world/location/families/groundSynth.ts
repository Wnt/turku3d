// Procedural ground surfaces: albedo, normal with height, and occlusion/roughness maps synthesised from metric
// parameters, for the covers whose pattern is a parameter (`paving`, `setts`, `cobbles`, paint) or that the
// photographic library lacks (`soil`, `sand`, `bedrock`); ground.ts draws the others from the library. A cover
// becomes maps here at the reference texel density of docs/design/render-readiness.md (M4), on a
// tile that is a whole number of pattern repeats so it wraps without a seam. The height map with `reliefM` is the
// displacement a later geometry path can apply (G1). Pure functions on typed arrays: this also runs in Node.

export const TEXELS_PER_METRE = 512;

export interface GroundMaps {
  width: number;
  height: number;
  /** What one repeat of the maps covers, in metres (u, v). */
  metres: [number, number];
  /** RGBA8, sRGB. */
  albedo: Uint8Array;
  /** RGBA8: tangent-space normal in rgb, height in alpha (0..1 spans `reliefM`). */
  normal: Uint8Array;
  /** RGBA8: occlusion, roughness, metalness. */
  orm: Uint8Array;
  /** Metres of relief between alpha 0 and 1: the displacement a later geometry path can apply. */
  reliefM: number;
}

export type GroundParams = Record<string, unknown>;

interface Canvas {
  w: number; h: number; mx: number; my: number;
  /** Linear colour. */
  r: Float32Array; g: Float32Array; b: Float32Array;
  /** Metres, any origin. */
  height: Float32Array;
  rough: Float32Array;
}

type Rgb = [number, number, number];

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);
const mix = (a: number, b: number, t: number) => a + (b - a) * t;
const smooth = (a: number, b: number, v: number) => { const t = clamp01((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const num = (v: unknown, fallback: number) => (typeof v === 'number' && Number.isFinite(v) ? v : fallback);
/** Linear RGB of a `#rrggbb` colour, or null. */
function hex(v: unknown): Rgb | null {
  if (typeof v !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(v)) return null;
  const channel = (k: number) => { const c = parseInt(v.slice(k, k + 2), 16) / 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  return [channel(1), channel(3), channel(5)];
}

function hash(ix: number, iy: number, seed: number): number {
  let h = Math.imul(ix, 374761393) ^ Math.imul(iy, 668265263) ^ Math.imul(seed, 1274126177);
  h = Math.imul(h ^ (h >>> 13), 1103515245);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

function canvas(mx: number, my: number): Canvas {
  const w = Math.round(mx * TEXELS_PER_METRE), h = Math.round(my * TEXELS_PER_METRE);
  const n = w * h;
  return { w, h, mx, my, r: new Float32Array(n), g: new Float32Array(n), b: new Float32Array(n), height: new Float32Array(n),
    rough: new Float32Array(n).fill(0.9) };
}

/** Tileable value noise with `cx` by `cy` lattice cells over the canvas, 0..1. */
function noise(w: number, h: number, cx: number, cy: number, seed: number): Float32Array {
  const out = new Float32Array(w * h);
  const lattice = new Float32Array(cx * cy);
  for (let j = 0; j < cy; j++) for (let i = 0; i < cx; i++) lattice[j * cx + i] = hash(i, j, seed);
  const x0 = new Int32Array(w), x1 = new Int32Array(w), fx = new Float32Array(w);
  for (let x = 0; x < w; x++) {
    const u = (x + 0.5) / w * cx, i = Math.floor(u), t = u - i;
    x0[x] = i % cx; x1[x] = (i + 1) % cx; fx[x] = t * t * (3 - 2 * t);
  }
  for (let y = 0; y < h; y++) {
    const v = (y + 0.5) / h * cy, j = Math.floor(v), t = v - j, fy = t * t * (3 - 2 * t);
    const r0 = (j % cy) * cx, r1 = ((j + 1) % cy) * cx, row = y * w;
    for (let x = 0; x < w; x++) {
      const a = lattice[r0 + x0[x]] + (lattice[r0 + x1[x]] - lattice[r0 + x0[x]]) * fx[x];
      const b = lattice[r1 + x0[x]] + (lattice[r1 + x1[x]] - lattice[r1 + x0[x]]) * fx[x];
      out[row + x] = a + (b - a) * fy;
    }
  }
  return out;
}

/** Fractal noise, 0..1, starting at `cells` lattice cells per metre... per canvas side scaled by aspect. */
function fbm(c: Canvas, cellM: number, octaves: number, seed: number, gain = 0.5): Float32Array {
  const out = new Float32Array(c.w * c.h);
  let amplitude = 1, total = 0, size = cellM;
  for (let o = 0; o < octaves; o++) {
    const cx = Math.max(1, Math.round(c.mx / size)), cy = Math.max(1, Math.round(c.my / size));
    if (cx > c.w / 2 || cy > c.h / 2) break;
    const layer = noise(c.w, c.h, cx, cy, seed + o * 17);
    for (let k = 0; k < out.length; k++) out[k] += layer[k] * amplitude;
    total += amplitude; amplitude *= gain; size /= 2;
  }
  for (let k = 0; k < out.length; k++) out[k] /= total;
  return out;
}

interface Cells { edge: Float32Array; id: Float32Array; id2: Float32Array; centre: Float32Array }

/** Tileable cellular pattern of stones about `sizeM` across: distance to the cell's edge in metres, two random
 * numbers per cell and the distance to its centre in metres. */
function cells(c: Canvas, sizeM: number, seed: number, jitter = 0.85): Cells {
  const nx = Math.max(2, Math.round(c.mx / sizeM)), ny = Math.max(2, Math.round(c.my / sizeM));
  const px = new Float32Array(nx * ny), py = new Float32Array(nx * ny);
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    px[j * nx + i] = 0.5 + (hash(i, j, seed) - 0.5) * jitter;
    py[j * nx + i] = 0.5 + (hash(i, j, seed + 1) - 0.5) * jitter;
  }
  const n = c.w * c.h;
  const edge = new Float32Array(n), id = new Float32Array(n), id2 = new Float32Array(n), centre = new Float32Array(n);
  const sx = c.mx / nx, sy = c.my / ny;
  for (let y = 0; y < c.h; y++) {
    const v = (y + 0.5) / c.h * ny, cj = Math.floor(v);
    for (let x = 0; x < c.w; x++) {
      const u = (x + 0.5) / c.w * nx, ci = Math.floor(u);
      let d1 = 1e9, d2 = 1e9, best = 0;
      for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) {
        const i = ci + di, j = cj + dj;
        const k = ((j % ny) + ny) % ny * nx + ((i % nx) + nx) % nx;
        const ex = (i + px[k] - u) * sx, ey = (j + py[k] - v) * sy;
        const d = ex * ex + ey * ey;
        if (d < d1) { d2 = d1; d1 = d; best = k; } else if (d < d2) d2 = d;
      }
      const at = y * c.w + x;
      const a = Math.sqrt(d1), b = Math.sqrt(d2);
      edge[at] = (b - a) / 2;
      centre[at] = a;
      id[at] = hash(best, 7, seed + 2);
      id2[at] = hash(best, 11, seed + 3);
    }
  }
  return { edge, id, id2, centre };
}

interface Bricks { edge: Float32Array; id: Float32Array; id2: Float32Array; id3: Float32Array; u: Float32Array; v: Float32Array }

/** Tileable courses of rectangular stones `sx` by `sy` metres. `shift` is the bond: 0 stacks, 0.5 is half-bond,
 * a negative value shifts every course by its own random amount. `wobble` moves each joint by up to that many
 * metres. Gives the distance to the stone's edge (metres, joint centre = 0), three random numbers per stone and the
 * position inside the stone (-1..1). */
function bricks(c: Canvas, sx: number, sy: number, shift: number, wobble: number, seed: number): Bricks {
  const nx = Math.round(c.mx / sx), ny = Math.round(c.my / sy);
  const n = c.w * c.h;
  const out: Bricks = { edge: new Float32Array(n), id: new Float32Array(n), id2: new Float32Array(n), id3: new Float32Array(n),
    u: new Float32Array(n), v: new Float32Array(n) };
  const side = (row: number, k: number) => (hash(((k % nx) + nx) % nx, row, seed + 5) - 0.5) * 2 * wobble;
  const course = (row: number) => (hash(0, ((row % ny) + ny) % ny, seed + 6) - 0.5) * 2 * wobble;
  for (let y = 0; y < c.h; y++) {
    const ym = (y + 0.5) / c.h * c.my;
    let row = Math.floor(ym / sy);
    // The joint below this course and the one above it are each moved once, for both courses they separate.
    if (ym - row * sy < course(row)) row -= 1;
    else if (ym - (row + 1) * sy > course(row + 1)) row += 1;
    const wrapped = ((row % ny) + ny) % ny;
    const bottom = row * sy + course(row), top = (row + 1) * sy + course(row + 1);
    const offset = shift < 0 ? hash(3, wrapped, seed + 7) * sx : (wrapped % 2) * shift * sx;
    for (let x = 0; x < c.w; x++) {
      const xm = (x + 0.5) / c.w * c.mx + offset;
      let k = Math.floor(xm / sx);
      if (xm - k * sx < side(wrapped, k)) k -= 1;
      else if (xm - (k + 1) * sx > side(wrapped, k + 1)) k += 1;
      const left = k * sx + side(wrapped, k), right = (k + 1) * sx + side(wrapped, k + 1);
      const at = y * c.w + x;
      out.edge[at] = Math.min(xm - left, right - xm, ym - bottom, top - ym);
      const stone = ((k % nx) + nx) % nx;
      out.id[at] = hash(stone, wrapped, seed + 8);
      out.id2[at] = hash(stone, wrapped, seed + 9);
      out.id3[at] = hash(stone, wrapped, seed + 10);
      out.u[at] = ((xm - left) / (right - left)) * 2 - 1;
      out.v[at] = ((ym - bottom) / (top - bottom)) * 2 - 1;
    }
  }
  return out;
}

function paint(c: Canvas, at: number, colour: Rgb, gain = 1) {
  c.r[at] = colour[0] * gain; c.g[at] = colour[1] * gain; c.b[at] = colour[2] * gain;
}

const SRGB = (() => {
  const table = new Uint8Array(4096);
  for (let i = 0; i < 4096; i++) {
    const v = i / 4095;
    table[i] = Math.round(255 * (v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055));
  }
  return table;
})();

/** Mean of a wrapped square window, for the occlusion of hollows. */
function blur(src: Float32Array, w: number, h: number, radius: number): Float32Array {
  const tmp = new Float32Array(src.length), out = new Float32Array(src.length);
  const span = 2 * radius + 1;
  for (let y = 0; y < h; y++) {
    let sum = 0;
    for (let k = -radius; k <= radius; k++) sum += src[y * w + ((k % w) + w) % w];
    for (let x = 0; x < w; x++) {
      tmp[y * w + x] = sum / span;
      sum += src[y * w + (x + radius + 1) % w] - src[y * w + ((x - radius) % w + w) % w];
    }
  }
  for (let x = 0; x < w; x++) {
    let sum = 0;
    for (let k = -radius; k <= radius; k++) sum += tmp[(((k % h) + h) % h) * w + x];
    for (let y = 0; y < h; y++) {
      out[y * w + x] = sum / span;
      sum += tmp[((y + radius + 1) % h) * w + x] - tmp[(((y - radius) % h + h) % h) * w + x];
    }
  }
  return out;
}

/** Packs a canvas: normals and occlusion come from the height, colour is encoded to sRGB. */
function pack(c: Canvas, occlusion: number): GroundMaps {
  const { w, h } = c, n = w * h;
  let low = Infinity, high = -Infinity;
  for (let k = 0; k < n; k++) { if (c.height[k] < low) low = c.height[k]; if (c.height[k] > high) high = c.height[k]; }
  const relief = Math.max(high - low, 1e-5);
  const albedo = new Uint8Array(n * 4), normal = new Uint8Array(n * 4), orm = new Uint8Array(n * 4);
  const texel = 1 / TEXELS_PER_METRE;
  const mean = blur(c.height, w, h, Math.max(2, Math.round(0.012 * TEXELS_PER_METRE)));
  for (let y = 0; y < h; y++) {
    const up = ((y + 1) % h) * w, down = ((y - 1 + h) % h) * w;
    for (let x = 0; x < w; x++) {
      const at = y * w + x, o = at * 4;
      const dx = (c.height[y * w + (x + 1) % w] - c.height[y * w + (x - 1 + w) % w]) / (2 * texel);
      const dy = (c.height[up + x] - c.height[down + x]) / (2 * texel);
      const inv = 1 / Math.sqrt(dx * dx + dy * dy + 1);
      normal[o] = Math.round((-dx * inv * 0.5 + 0.5) * 255);
      normal[o + 1] = Math.round((-dy * inv * 0.5 + 0.5) * 255);
      normal[o + 2] = Math.round((inv * 0.5 + 0.5) * 255);
      normal[o + 3] = Math.round((c.height[at] - low) / relief * 255);
      const hollow = clamp01((mean[at] - c.height[at]) / Math.max(relief * 0.5, 1e-4));
      const ao = 1 - occlusion * hollow;
      albedo[o] = SRGB[Math.round(clamp01(c.r[at]) * 4095)];
      albedo[o + 1] = SRGB[Math.round(clamp01(c.g[at]) * 4095)];
      albedo[o + 2] = SRGB[Math.round(clamp01(c.b[at]) * 4095)];
      albedo[o + 3] = 255;
      orm[o] = Math.round(clamp01(ao) * 255);
      orm[o + 1] = Math.round(clamp01(c.rough[at]) * 255);
      orm[o + 2] = 0;
      orm[o + 3] = 255;
    }
  }
  return { width: w, height: h, metres: [c.mx, c.my], albedo, normal, orm, reliefM: relief };
}

/** A tile close to `target` metres that holds a whole, even number of `period` repeats. */
function whole(period: number, target = 2.0): number {
  return Math.max(2, Math.round(target / period / 2) * 2) * period;
}

// Granite as it is laid in Turku: grey and red stone mixed, some dark, some pale. Linear colour.
const GRANITE: Array<[Rgb, number]> = [
  [[0.20, 0.195, 0.19], 0.42], [[0.25, 0.165, 0.14], 0.24], [[0.115, 0.115, 0.12], 0.14], [[0.32, 0.30, 0.28], 0.12],
  [[0.22, 0.19, 0.155], 0.08],
];
const FIELD_STONE: Array<[Rgb, number]> = [
  [[0.20, 0.19, 0.18], 0.4], [[0.16, 0.13, 0.10], 0.2], [[0.24, 0.17, 0.14], 0.15], [[0.11, 0.11, 0.115], 0.15], [[0.30, 0.28, 0.25], 0.1],
];

function pick(palette: Array<[Rgb, number]>, t: number): Rgb {
  let sum = 0;
  for (const [colour, weight] of palette) { sum += weight; if (t < sum) return colour; }
  return palette[palette.length - 1][0];
}

function paving(p: GroundParams): GroundMaps {
  const sx = num(p.size_x, 0.3), sy = num(p.size_y, 0.3);
  const tone = num(p.tone, 0.5), joint = num(p.joint_m, 0.006);
  const bond = p.bond === 'stack' ? 0 : 0.5;
  const natural = p.stone === 'granite' || p.stone === 'slate';
  const tint = hex(p.tint) ?? (p.stone === 'slate' ? [0.8, 0.86, 0.95] as Rgb : [1.0, 0.975, 0.93] as Rgb);
  const c = canvas(whole(sx), whole(sy));
  const lay = bricks(c, sx, sy, bond, natural ? 0.002 : 0.0012, 51);
  const stain = fbm(c, 0.7, 4, 61), grit = fbm(c, 0.006, 2, 71), moss = fbm(c, 0.5, 3, 81), dust = fbm(c, 0.1, 3, 91);
  const sand = cells(c, 0.004, 101);
  const base = mix(0.15, 0.40, tone);
  for (let k = 0; k < c.r.length; k++) {
    const top = smooth(joint / 2, joint / 2 + 0.004, lay.edge[k]);
    let face: Rgb;
    if (natural) {
      // Sawn stone: each slab from its own block, crystals showing.
      const body = p.stone === 'slate' ? [0.11, 0.115, 0.125] as Rgb : pick(GRANITE, lay.id[k] * 0.66);
      const crystal = sand.id[k] > 0.8 ? 1.3 : sand.id[k] < 0.2 ? 0.65 : 1;
      const shade = mix(0.75, 1.3, tone) * mix(0.9, 1.1, lay.id2[k]) * crystal * (1 + (stain[k] - 0.5) * 0.25);
      face = [body[0] * shade, body[1] * shade, body[2] * shade];
    } else {
      // Cast concrete: every stone its own shade, sand and fines showing in the face, edges darkened by damp joints.
      const shade = base * mix(0.82, 1.16, lay.id[k]) * (1 + (stain[k] - 0.5) * 0.2) * mix(0.86, 1.1, grit[k]);
      const speck = sand.id[k] > 0.93 ? 1.35 : sand.id[k] < 0.05 ? 0.7 : 1;
      const rim = mix(0.8, 1, smooth(0, 0.02, lay.edge[k] - joint / 2));
      face = [shade * speck * rim * tint[0], shade * speck * rim * tint[1], shade * speck * rim * tint[2]];
    }
    const green = smooth(0.55, 0.75, moss[k]);
    // Joints of cast concrete are brushed full of sand, paler than the damp soil between sawn stone.
    const sandy = natural ? 1 : 2.2;
    const fill: Rgb = [mix(0.055 * sandy, 0.035, green) * mix(0.7, 1.3, dust[k]), mix(0.05 * sandy, 0.05, green) * mix(0.7, 1.3, dust[k]),
      mix(0.04 * sandy, 0.02, green) * mix(0.7, 1.3, dust[k])];
    paint(c, k, [mix(fill[0], face[0], top), mix(fill[1], face[1], top), mix(fill[2], face[2], top)]);
    const tilt = (lay.id2[k] - 0.5) * 0.0012 * lay.u[k] + (lay.id3[k] - 0.5) * 0.0012 * lay.v[k];
    c.height[k] = top * (0.005 + (lay.id2[k] - 0.5) * 0.0016 + tilt) + grit[k] * 0.0004 * top + (1 - top) * dust[k] * 0.0015;
    c.rough[k] = mix(0.96, natural ? 0.7 : mix(0.8, 0.9, grit[k]), top);
  }
  return pack(c, 0.6);
}

function setts(p: GroundParams): GroundMaps {
  const sx = num(p.size_x, 0.09), sy = num(p.size_y, 0.09);
  const tone = num(p.tone, 0.5);
  const c = canvas(whole(sx), whole(sy));
  const lay = bricks(c, sx, sy, -1, Math.min(sx, sy) * 0.09, 111);
  const grainA = cells(c, 0.0035, 121), stain = fbm(c, 0.6, 4, 131), fill = fbm(c, 0.012, 3, 141), face = fbm(c, 0.03, 3, 151);
  const joint = Math.min(sx, sy) * 0.11, round = Math.min(sx, sy) * 0.22;
  const gain = mix(0.7, 1.35, tone);
  for (let k = 0; k < c.r.length; k++) {
    // A rounded rectangle: corners are worn off, the joint widens there.
    const cornerU = Math.max(0, Math.abs(lay.u[k]) - (1 - 2 * round / sx)) * sx / 2;
    const cornerV = Math.max(0, Math.abs(lay.v[k]) - (1 - 2 * round / sy)) * sy / 2;
    const corner = Math.hypot(cornerU, cornerV);
    const edge = Math.min(lay.edge[k], round - corner) - joint / 2;
    const top = smooth(0, 0.006, edge);
    const stone = pick(GRANITE, lay.id[k]);
    const crystal = grainA.id[k] > 0.8 ? 1.3 : grainA.id[k] < 0.22 ? 0.62 : 1;
    const shade = gain * mix(0.8, 1.2, lay.id2[k]) * crystal * (1 + (stain[k] - 0.5) * 0.25) * mix(0.9, 1.08, face[k]);
    const sandy: Rgb = [0.085 * mix(0.6, 1.4, fill[k]), 0.075 * mix(0.6, 1.4, fill[k]), 0.058 * mix(0.6, 1.4, fill[k])];
    paint(c, k, [mix(sandy[0], stone[0] * shade, top), mix(sandy[1], stone[1] * shade, top), mix(sandy[2], stone[2] * shade, top)]);
    const dome = 1 - 0.5 * (lay.u[k] * lay.u[k] + lay.v[k] * lay.v[k]);
    const tilt = (lay.id2[k] - 0.5) * 0.004 * lay.u[k] + (lay.id3[k] - 0.5) * 0.004 * lay.v[k];
    c.height[k] = top * (0.008 + dome * 0.003 + (lay.id3[k] - 0.5) * 0.004 + tilt) + smooth(-joint / 2, 0.004, edge) * 0.004
      + face[k] * 0.0008 * top + (1 - top) * fill[k] * 0.003;
    c.rough[k] = mix(0.97, mix(0.62, 0.82, face[k]), top);
  }
  return pack(c, 0.75);
}

function cobbles(p: GroundParams): GroundMaps {
  const size = num(p.size_m, 0.14);
  const c = canvas(whole(size), whole(size));
  const stones = cells(c, size, 161, 0.7);
  const speck = cells(c, 0.004, 171), fill = fbm(c, 0.015, 3, 181), stain = fbm(c, 0.6, 3, 191);
  for (let k = 0; k < c.r.length; k++) {
    const top = smooth(0.006, 0.02, stones.edge[k]);
    const stone = pick(FIELD_STONE, stones.id[k]);
    const shade = mix(0.8, 1.2, stones.id2[k]) * (speck.id[k] > 0.85 ? 1.25 : speck.id[k] < 0.15 ? 0.75 : 1) * (1 + (stain[k] - 0.5) * 0.2);
    const earth: Rgb = [0.07 * mix(0.6, 1.4, fill[k]), 0.06 * mix(0.6, 1.4, fill[k]), 0.042 * mix(0.6, 1.4, fill[k])];
    paint(c, k, [mix(earth[0], stone[0] * shade, top), mix(earth[1], stone[1] * shade, top), mix(earth[2], stone[2] * shade, top)]);
    // A field stone is a dome: highest at its centre, rolling off into the joint.
    const dome = Math.sqrt(clamp01(stones.edge[k] / (size * 0.4)));
    c.height[k] = dome * 0.022 * mix(0.7, 1.1, stones.id2[k]) + (1 - top) * fill[k] * 0.004;
    c.rough[k] = mix(0.97, 0.7, top);
  }
  return pack(c, 0.8);
}

function roadPaint(p: GroundParams): GroundMaps {
  const colour = hex(p.colour) ?? [0.82, 0.82, 0.8] as Rgb;
  const wear = num(p.wear, 0.35);
  const c = canvas(2.0, 2.0);
  const stones = cells(c, 0.009, 371), worn = fbm(c, 0.25, 5, 381, 0.6), grime = fbm(c, 0.6, 3, 391);
  for (let k = 0; k < c.r.length; k++) {
    // Thermoplastic paint over asphalt: it chips off the tops of the aggregate first, then in patches.
    const chipped = smooth(1 - wear - 0.12, 1 - wear + 0.12, worn[k] * 0.75 + stones.id[k] * 0.25 * smooth(0.0005, 0.002, stones.edge[k]));
    const dirt = mix(0.72, 1, grime[k]);
    const under = 0.075 * mix(1, 2.4, stones.id2[k] * smooth(0.0004, 0.002, stones.edge[k]));
    paint(c, k, [mix(colour[0] * dirt, under, chipped), mix(colour[1] * dirt, under, chipped), mix(colour[2] * dirt * 0.97, under, chipped)]);
    c.height[k] = (1 - chipped) * 0.0015 + smooth(0.0004, 0.002, stones.edge[k]) * 0.0008;
    c.rough[k] = mix(0.62, 0.9, chipped);
  }
  return pack(c, 0.3);
}

function soil(p: GroundParams): GroundMaps {
  const forest = p.kind === 'forest';
  const c = canvas(2.0, 2.0);
  const chips = cells(c, forest ? 0.03 : 0.018, 401), crumb = fbm(c, 0.03, 4, 411), patch = fbm(c, 0.7, 3, 421);
  for (let k = 0; k < c.r.length; k++) {
    const litter = chips.id[k] > 0.55 ? smooth(0.001, 0.004, chips.edge[k]) : 0;
    const soil = mix(0.6, 1.3, crumb[k]) * (1 + (patch[k] - 0.5) * 0.4);
    const leaf: Rgb = chips.id2[k] > 0.6 ? [0.13, 0.085, 0.04] : chips.id2[k] > 0.25 ? [0.075, 0.05, 0.028] : [0.06, 0.085, 0.03];
    const earth: Rgb = forest ? [0.04, 0.036, 0.022] : [0.034, 0.026, 0.018];
    paint(c, k, [mix(earth[0] * soil, leaf[0], litter), mix(earth[1] * soil, leaf[1], litter), mix(earth[2] * soil, leaf[2], litter)]);
    c.height[k] = crumb[k] * 0.012 + litter * 0.004;
    c.rough[k] = 0.95;
  }
  return pack(c, 0.8);
}

function sand(): GroundMaps {
  const c = canvas(2.0, 2.0);
  const grains = cells(c, 0.004, 431), dune = fbm(c, 0.4, 4, 441), fine = fbm(c, 0.01, 2, 451);
  for (let k = 0; k < c.r.length; k++) {
    const shade = mix(0.82, 1.15, grains.id[k]) * (1 + (dune[k] - 0.5) * 0.3) * mix(0.9, 1.08, fine[k]);
    paint(c, k, [0.36 * shade, 0.30 * shade, 0.21 * shade]);
    c.height[k] = dune[k] * 0.02 + fine[k] * 0.001;
    c.rough[k] = 0.96;
  }
  return pack(c, 0.5);
}

function bedrock(): GroundMaps {
  const c = canvas(2.0, 2.0);
  const mass = fbm(c, 0.9, 6, 461), crack = fbm(c, 0.5, 5, 471), crystal = cells(c, 0.005, 481);
  for (let k = 0; k < c.r.length; k++) {
    const seam = smooth(0.04, 0, Math.abs(crack[k] - 0.5));
    const shade = mix(0.7, 1.25, mass[k]) * (crystal.id[k] > 0.85 ? 1.25 : crystal.id[k] < 0.15 ? 0.7 : 1) * (1 - seam * 0.6);
    paint(c, k, [0.2 * shade, 0.19 * shade, 0.18 * shade]);
    c.height[k] = mass[k] * 0.06 - seam * 0.015;
    c.rough[k] = 0.85;
  }
  return pack(c, 0.6);
}

const FAMILIES: Record<string, (params: GroundParams) => GroundMaps> = {
  paving, setts, cobbles, soil, sand, bedrock, road_paint: roadPaint,
};

export const GROUND_FAMILIES = Object.keys(FAMILIES);

/** The maps of one ground family for its parameters. Deterministic: the same parameters give the same texels. */
export function synthesise(family: string, params: GroundParams = {}): GroundMaps {
  const make = FAMILIES[family];
  if (!make) throw new Error(`Unknown ground family ${family}`);
  return make(params);
}
