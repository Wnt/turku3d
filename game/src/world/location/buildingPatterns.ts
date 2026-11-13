// Procedural pattern maps for the building material families: no image files, only parameters.
//
// A pattern is a tiling pair of maps at a fixed metric size: `map` (R tone around 0.5, G share of the second colour:
// mortar, joints, grooves; B roughness around 0.5, A occlusion) and a tangent-space `normal` map from a height field
// in metres. A material tints the pattern with the building's own colours, so one pattern serves every colour.
import * as THREE from 'three/webgpu';

export interface PatternParams {
  family: string;
  finish?: string;
  bond?: string;
  orientation?: string;
  profile?: string;
  scale_m?: number;
}

export interface Pattern {
  map: THREE.DataTexture;
  normal: THREE.DataTexture;
  /** Metres covered by one repeat, along u and v. */
  size: [number, number];
  /** How strongly the tone channel modulates the colour, and the roughness channel the roughness. */
  tone: number;
  rough: number;
}

interface Field {
  n: number;
  size: [number, number];
  height: Float32Array;
  tone: Float32Array;
  joint: Float32Array;
  rough: Float32Array;
  occlusion: Float32Array;
}

function mulberry(seed: number) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
function hash2(x: number, y: number, seed: number) {
  let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ Math.imul(seed | 0, 2147483647);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}
/** Value noise that repeats after `period` cells in both directions. */
function noise(x: number, y: number, period: number, seed: number) {
  const xi = Math.floor(x), yi = Math.floor(y), fx = x - xi, fy = y - yi;
  const wrap = (v: number) => ((v % period) + period) % period;
  const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
  const a = hash2(wrap(xi), wrap(yi), seed), b = hash2(wrap(xi + 1), wrap(yi), seed);
  const c = hash2(wrap(xi), wrap(yi + 1), seed), d = hash2(wrap(xi + 1), wrap(yi + 1), seed);
  return a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy;
}
/** Fractal noise in 0..1 over the unit square, tiling; `cells` is the lowest frequency. */
function fbm(u: number, v: number, cells: number, octaves: number, seed: number) {
  let sum = 0, weight = 0, amplitude = 1, frequency = cells;
  for (let o = 0; o < octaves; o++) {
    sum += amplitude * noise(u * frequency, v * frequency, frequency, seed + o * 17);
    weight += amplitude; amplitude *= 0.5; frequency *= 2;
  }
  return sum / weight;
}

function field(n: number, size: [number, number]): Field {
  const make = (value: number) => new Float32Array(n * n).fill(value);
  return { n, size, height: make(0), tone: make(0.5), joint: make(0), rough: make(0.5), occlusion: make(1) };
}

function plaster(finish: string): Field {
  const f = field(512, [2.4, 2.4]);
  const grain = { smooth: 0.00025, fine: 0.0007, rough: 0.0018, roughcast: 0.0045, scored: 0.0007 }[finish] ?? 0.0007;
  const coarse = finish === 'roughcast' ? 96 : finish === 'rough' ? 64 : 48;
  for (let j = 0; j < f.n; j++) for (let i = 0; i < f.n; i++) {
    const u = i / f.n, v = j / f.n, k = j * f.n + i;
    const fine = fbm(u, v, coarse, 3, 11), trowel = fbm(u, v, 6, 3, 23);
    f.height[k] = grain * (fine * 2 - 1) + grain * 1.5 * (trowel - 0.5);
    f.tone[k] = 0.5 + 0.16 * (fbm(u, v, 4, 4, 5) - 0.5) + 0.1 * (fine - 0.5);
    f.rough[k] = 0.5 + 0.3 * (fine - 0.5);
  }
  return f;
}

function brick(bond: string, length: number): Field {
  const course = length * 0.07 / 0.27, joint = 0.013;
  const columns = bond === 'flemish' ? 9 : 8, rows = 32; // a whole number of bond periods per repeat
  const f = field(1024, [columns * length, rows * course]);
  const random = mulberry(91);
  // Per-brick tone by half-brick cell, so that every bond gets its own bricks.
  const tones = new Float32Array(columns * 2 * rows);
  for (let k = 0; k < tones.length; k++) tones[k] = (random() - 0.5) * 0.36 - (random() < 0.07 ? 0.22 : 0);
  for (let j = 0; j < f.n; j++) {
    const y = (j / f.n) * rows, row = Math.floor(y), fy = y - row;
    for (let i = 0; i < f.n; i++) {
      const x = (i / f.n) * columns * 2, k = j * f.n + i; // in half bricks
      let start: number, span: number;
      if (bond === 'header' || (bond === 'english' && row % 2 === 1)) { span = 1; start = Math.floor(x); }
      else if (bond === 'flemish') {
        // stretcher (2) then header (1), shifted by 1.5 on alternate courses
        const shift = row % 2 ? 1.5 : 0, p = (((x - shift) % 3) + 3) % 3;
        span = p < 2 ? 2 : 1; start = x - p + (p < 2 ? 0 : 2);
      } else {
        const shift = bond === 'stack' ? 0 : row % 2 ? 1 : bond === 'english' ? 0.5 : 0;
        const p = (((x - shift) % 2) + 2) % 2;
        span = 2; start = x - p;
      }
      const within = x - start, half = length / 2;
      const dx = Math.min(within, span - within) * half, dy = Math.min(fy, 1 - fy) * course;
      const edge = Math.min(dx, dy), mortar = edge < joint / 2 ? 1 : 0;
      const soft = Math.min(1, Math.max(0, (edge - joint / 2) / 0.004));
      const id = (((Math.round(start * 2) % (columns * 4)) + columns * 4) % (columns * 4)) + row * 131;
      const speck = fbm(i / f.n, j / f.n, 128, 2, 7);
      f.joint[k] = mortar ? 1 : 1 - soft;
      f.height[k] = mortar ? -0.004 + 0.001 * speck : -0.004 * (1 - soft) + 0.0012 * (speck - 0.5);
      f.tone[k] = mortar ? 0.5 + 0.15 * (speck - 0.5) : 0.5 + tones[id % tones.length] + 0.14 * (speck - 0.5);
      f.rough[k] = mortar ? 0.75 : 0.45 + 0.3 * speck;
      f.occlusion[k] = mortar ? 0.82 : 1;
    }
  }
  return f;
}

function boards(profile: string, exposure: number, vertical: boolean): Field {
  const count = 8, along = 2.4;
  const f = field(512, [along, count * exposure]);
  const random = mulberry(37);
  const boardTone = Array.from({ length: count }, () => (random() - 0.5) * 0.1);
  const butt = Array.from({ length: count }, () => random());
  const lap = profile === 'bevel';
  const groove = profile === 'beaded' ? 0.012 : 0.008;
  for (let j = 0; j < f.n; j++) {
    const y = (j / f.n) * count, row = Math.floor(y), fy = y - row; // 0 at a board's lower edge
    for (let i = 0; i < f.n; i++) {
      const u = i / f.n, k = j * f.n + i;
      const grain = fbm(u, (row + fy * 0.25) / count, 4, 3, 13 + row) * 0.6 + fbm(u * 1.0, j / f.n, 96, 2, 3) * 0.4;
      let height: number, occlusion = 1, joint = 0;
      if (lap) {
        // Lapped boards: the lower edge stands out, the upper edge disappears under the next board.
        height = 0.02 * (1 - fy);
        if (fy > 0.86) occlusion = 0.55 + 0.45 * (1 - (fy - 0.86) / 0.14);
        if (fy < 0.05) joint = 0.25;
      } else {
        const distance = Math.min(fy, 1 - fy) * exposure;
        const inGroove = distance < groove / 2;
        height = inGroove ? -0.007 : profile === 'beaded' && distance < groove * 1.6 ? 0.002 : 0;
        joint = inGroove ? 1 : 0; occlusion = inGroove ? 0.7 : 1;
        if (profile === 'board_batten' && distance < 0.022) { height = 0.02; joint = 0; occlusion = 1; }
      }
      const seam = Math.abs(u - butt[row]) * along;
      if (seam < 0.0025) { joint = Math.max(joint, 0.8); height -= 0.003; }
      f.height[k] = height + 0.0006 * (grain - 0.5);
      f.tone[k] = 0.5 + boardTone[row] + 0.14 * (grain - 0.5);
      f.joint[k] = joint; f.occlusion[k] = occlusion;
      f.rough[k] = 0.5 + 0.3 * (grain - 0.5);
    }
  }
  return vertical ? transpose(f) : f;
}

/** Swap the axes of a field: horizontal boards become vertical ones. */
function transpose(f: Field): Field {
  const out = field(f.n, [f.size[1], f.size[0]]);
  for (const name of ['height', 'tone', 'joint', 'rough', 'occlusion'] as const) {
    const from = f[name], to = out[name];
    for (let j = 0; j < f.n; j++) for (let i = 0; i < f.n; i++) to[i * f.n + j] = from[j * f.n + i];
  }
  return out;
}

function ashlar(finish: string, course: number, speckle: number): Field {
  const rows = Math.max(2, Math.round(2.4 / course)), length = course * 2.4;
  const columns = 3;
  const f = field(1024, [columns * length, rows * course]);
  const random = mulberry(53);
  const cuts = Array.from({ length: rows }, () => { const offset = random(); return Array.from({ length: columns }, (_, c) => (c + offset + (random() - 0.5) * 0.3) % columns).sort((a, b) => a - b); });
  // `quay`: rock-faced blocks of a harbour or river wall, read from across the water: wide sunk joints and a strong
  // tone per block (the 12 mm joints and 8 % tone steps of a facade vanish beyond 10 m).
  const quay = finish === 'quay';
  const tones = Array.from({ length: rows * columns }, () => (random() - 0.5) * (quay ? 0.5 : 0.16));
  const joint = quay ? 0.035 : finish === 'slab' || finish === 'polished' ? 0.006 : 0.012;
  const bulge = quay ? 0.05 : finish === 'rock_faced' ? 0.03 : 0.0;
  for (let j = 0; j < f.n; j++) {
    const y = (j / f.n) * rows, row = Math.floor(y), fy = y - row;
    for (let i = 0; i < f.n; i++) {
      const x = (i / f.n) * columns, k = j * f.n + i;
      const edges = cuts[row];
      let index = edges.length - 1;
      for (let c = 0; c < edges.length; c++) if (x >= edges[c]) index = c;
      const left = edges[index], right = index + 1 < edges.length ? edges[index + 1] : edges[0] + columns;
      const within = ((x - left) % columns + columns) % columns, span = right - left;
      const dx = Math.min(within, span - within) * length, dy = Math.min(fy, 1 - fy) * course;
      const edge = Math.min(dx, dy), inJoint = edge < joint / 2;
      const grain = fbm(i / f.n, j / f.n, 160, 2, 29), blotch = fbm(i / f.n, j / f.n, 12, 3, 31);
      const dome = bulge * Math.min(1, edge / 0.06) * (0.6 + 0.8 * fbm(i / f.n, j / f.n, 24, 3, 41));
      f.joint[k] = inJoint ? 1 : 0;
      f.height[k] = inJoint ? -0.006 : dome + 0.0008 * (grain - 0.5);
      f.tone[k] = inJoint ? (quay ? 0.25 : 0.45) : 0.5 + tones[row * columns + index] + speckle * (grain - 0.5) + 0.1 * (blotch - 0.5);
      f.rough[k] = inJoint ? 0.8 : finish === 'polished' ? 0.15 : 0.5 + 0.2 * (grain - 0.5);
      f.occlusion[k] = inJoint ? (quay ? 0.5 : 0.75) : 1;
    }
  }
  return f;
}

function rubble(stone: number): Field {
  const cells = Math.max(3, Math.round(2.4 / stone));
  const f = field(1024, [cells * stone, cells * stone]);
  const points: [number, number, number][] = [];
  for (let cy = 0; cy < cells; cy++) for (let cx = 0; cx < cells; cx++) {
    points.push([cx + 0.15 + 0.7 * hash2(cx, cy, 3), cy + 0.15 + 0.7 * hash2(cx, cy, 5), (hash2(cx, cy, 9) - 0.5) * 0.3]);
  }
  for (let j = 0; j < f.n; j++) for (let i = 0; i < f.n; i++) {
    const x = (i / f.n) * cells, y = (j / f.n) * cells, k = j * f.n + i;
    let nearest = 9, second = 9, tone = 0;
    const cx0 = Math.floor(x), cy0 = Math.floor(y);
    for (let oy = -1; oy <= 1; oy++) for (let ox = -1; ox <= 1; ox++) {
      const cx = ((cx0 + ox) % cells + cells) % cells, cy = ((cy0 + oy) % cells + cells) % cells;
      const p = points[cy * cells + cx];
      const dx = p[0] + (cx0 + ox - cx) - x, dy = (p[1] + (cy0 + oy - cy) - y) * 1.25; // stones lie flatter than tall
      const d = Math.hypot(dx, dy);
      if (d < nearest) { second = nearest; nearest = d; tone = p[2]; } else if (d < second) second = d;
    }
    const gap = (second - nearest) * stone, mortar = gap < 0.03;
    const grain = fbm(i / f.n, j / f.n, 128, 2, 61);
    f.joint[k] = mortar ? 1 : Math.max(0, 1 - (gap - 0.03) / 0.015);
    f.height[k] = mortar ? -0.012 : 0.02 * Math.min(1, (gap - 0.03) / 0.08) + 0.002 * (grain - 0.5);
    f.tone[k] = mortar ? 0.5 + 0.1 * (grain - 0.5) : 0.5 + tone + 0.2 * (grain - 0.5);
    f.rough[k] = mortar ? 0.8 : 0.5 + 0.2 * (grain - 0.5);
    f.occlusion[k] = mortar ? 0.7 : 1;
  }
  return f;
}

function concrete(finish: string): Field {
  const f = field(512, [3.0, 3.0]);
  for (let j = 0; j < f.n; j++) for (let i = 0; i < f.n; i++) {
    const u = i / f.n, v = j / f.n, k = j * f.n + i;
    const blotch = fbm(u, v, 3, 4, 71), fine = fbm(u, v, 96, 2, 73);
    const pits = fine > 0.78 ? (fine - 0.78) * 0.02 : 0;
    const board = finish === 'board_marked' ? ((v * 20) % 1 < 0.04 ? 0.002 : 0) : 0;
    const pebble = finish === 'washed' ? 0.003 * (fbm(u, v, 200, 1, 79) - 0.5) : 0;
    // Precast elements: a storey high and as wide, with a sealed joint between them.
    const seam = finish === 'panel' && Math.min(u, 1 - u, v, 1 - v) * 3.0 < 0.012;
    if (seam) f.joint[k] = 0.7;
    f.height[k] = -pits - board + pebble + 0.0006 * (fine - 0.5) - (seam ? 0.008 : 0);
    f.tone[k] = 0.5 + 0.22 * (blotch - 0.5) + 0.1 * (fine - 0.5) + (finish === 'washed' ? 0.25 * (fbm(u, v, 200, 1, 83) - 0.5) : 0);
    f.rough[k] = 0.5 + 0.25 * (fine - 0.5);
    f.occlusion[k] = 1 - pits * 12;
  }
  return f;
}

/** Metal cassettes: flat panels `course` high and three times as long, in a grid of open shadow joints; each
 *  panel a shade of its own, as anodised and coated sheets are. */
function cassettes(course: number): Field {
  const columns = 2, rows = 2, length = course * 3;
  const f = field(512, [columns * length, rows * course]);
  const random = mulberry(211);
  const panel = Array.from({ length: columns * rows }, () => (random() - 0.5) * 0.07);
  for (let j = 0; j < f.n; j++) for (let i = 0; i < f.n; i++) {
    const x = (i / f.n) * columns, y = (j / f.n) * rows, k = j * f.n + i;
    const column = Math.floor(x), row = Math.floor(y), fx = x - column, fy = y - row;
    const gap = Math.min(Math.min(fx, 1 - fx) * length, Math.min(fy, 1 - fy) * course);
    const open = gap < 0.011;
    const blotch = fbm(i / f.n, j / f.n, 3, 3, 223), streak = fbm(i / f.n, j / f.n * 0.25, 24, 3, 227);
    f.height[k] = open ? -0.012 : gap < 0.022 ? -0.012 * (1 - (gap - 0.011) / 0.011) : 0.0008 * Math.sin(fx * Math.PI);
    f.tone[k] = 0.5 + panel[row * columns + column] + 0.05 * (streak - 0.5) + 0.06 * (blotch - 0.5);
    f.joint[k] = open ? 0.85 : 0;
    f.occlusion[k] = open ? 0.45 : gap < 0.03 ? 0.8 : 1;
    f.rough[k] = 0.5 + 0.2 * (blotch - 0.5);
  }
  return f;
}

function seamedMetal(profile: string, pitch: number): Field {
  const seams = 4, rows = 2, sheet = 2.0;
  const f = field(512, [seams * pitch, rows * sheet]);
  const random = mulberry(101);
  const panel = Array.from({ length: seams * rows }, () => (random() - 0.5) * 0.08);
  const cross = Array.from({ length: seams }, () => random());
  for (let j = 0; j < f.n; j++) for (let i = 0; i < f.n; i++) {
    const x = (i / f.n) * seams, y = (j / f.n) * rows, k = j * f.n + i;
    const column = Math.floor(x), fx = x - column, row = Math.floor(y + cross[column]) % rows;
    const fromSeam = Math.min(fx, 1 - fx) * pitch;
    const along = (y + cross[column]) % 1, fromCross = Math.min(along, 1 - along) * sheet;
    let height = 0, joint = 0, occlusion = 1;
    if (profile === 'corrugated') height = 0.009 * Math.sin(fx * Math.PI * 2 * (pitch / 0.076));
    else if (profile === 'standing_seam') {
      if (fromSeam < 0.012) { height = 0.025 * (1 - fromSeam / 0.012); joint = 0.25; }
      else if (fromSeam < 0.035) occlusion = 0.8 + 0.2 * (fromSeam - 0.012) / 0.023;
      height += 0.0015 * Math.sin(fx * Math.PI) * (0.5 + fbm(i / f.n, j / f.n, 3, 2, 107)); // oil canning
    } else if (fromSeam < 0.006) { height = 0.003; joint = 0.5; }
    if (profile !== 'corrugated' && fromCross < 0.008) { height += 0.003; joint = Math.max(joint, 0.4); }
    const streak = fbm(i / f.n, j / f.n * 0.25, 24, 3, 113), blotch = fbm(i / f.n, j / f.n, 3, 3, 127);
    f.height[k] = height;
    f.tone[k] = 0.5 + panel[row * seams + column] + 0.08 * (streak - 0.5) + 0.1 * (blotch - 0.5);
    f.joint[k] = joint; f.occlusion[k] = occlusion;
    f.rough[k] = 0.5 + 0.3 * (blotch - 0.5);
  }
  return f;
}

function tiles(profile: string, width: number): Field {
  const columns = 8, rows = 8, gauge = width * 1.15;
  const f = field(512, [columns * width, rows * gauge]);
  const random = mulberry(131);
  const tone = Array.from({ length: columns * rows }, () => (random() - 0.5) * 0.3);
  for (let j = 0; j < f.n; j++) for (let i = 0; i < f.n; i++) {
    const x = (i / f.n) * columns, y = (j / f.n) * rows, k = j * f.n + i;
    const column = Math.floor(x), row = Math.floor(y), fx = x - column, fy = y - row;
    const wave = profile === 'pantile' ? 0.03 * Math.sin(fx * Math.PI * 2 - 0.6) : fx < 0.03 ? -0.004 : 0;
    const lapLift = 0.022 * (1 - fy); // the lower edge of a tile lies on the row below
    const grain = fbm(i / f.n, j / f.n, 96, 2, 137);
    f.height[k] = wave + lapLift;
    f.tone[k] = 0.5 + tone[row * columns + column] + 0.15 * (grain - 0.5);
    f.occlusion[k] = fy > 0.9 ? 0.6 : profile === 'pantile' ? 0.82 + 0.18 * Math.sin(fx * Math.PI * 2 - 0.6) : 1;
    f.joint[k] = fy < 0.03 ? 0.4 : 0;
    f.rough[k] = 0.5 + 0.3 * (grain - 0.5);
  }
  return f;
}

function felt(profile: string): Field {
  const f = field(512, [2.4, 2.0]);
  for (let j = 0; j < f.n; j++) for (let i = 0; i < f.n; i++) {
    const u = i / f.n, v = j / f.n, k = j * f.n + i;
    const grit = fbm(u, v, 200, 1, 141), blotch = fbm(u, v, 4, 3, 149);
    const lap = Math.min(v % 0.5, 0.5 - (v % 0.5)) * 2.0 < 0.012;
    const batten = profile === 'batten' && Math.min(u % 0.25, 0.25 - (u % 0.25)) * 2.4 < 0.02;
    f.height[k] = 0.0012 * (grit - 0.5) + (lap ? 0.003 : 0) + (batten ? 0.025 : 0);
    f.tone[k] = 0.5 + 0.3 * (grit - 0.5) + 0.16 * (blotch - 0.5);
    f.joint[k] = lap ? 0.4 : 0;
    f.rough[k] = 0.5 + 0.3 * (grit - 0.5);
  }
  return f;
}

function paint(): Field {
  const f = field(256, [1.2, 1.2]);
  for (let j = 0; j < f.n; j++) for (let i = 0; i < f.n; i++) {
    const u = i / f.n, v = j / f.n, k = j * f.n + i;
    const brush = fbm(u * 0.2, v, 64, 2, 151), blotch = fbm(u, v, 4, 3, 157);
    f.height[k] = 0.0003 * (brush - 0.5);
    f.tone[k] = 0.5 + 0.08 * (blotch - 0.5) + 0.05 * (brush - 0.5);
    f.rough[k] = 0.5 + 0.2 * (blotch - 0.5);
  }
  return f;
}

function bake(f: Field, tone: number, rough: number): Pattern {
  const n = f.n, map = new Uint8Array(n * n * 4), normal = new Uint8Array(n * n * 4);
  const dx = f.size[0] / n, dy = f.size[1] / n;
  const byte = (value: number) => Math.max(0, Math.min(255, Math.round(value * 255)));
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const k = j * n + i, o = k * 4;
    map[o] = byte(f.tone[k]); map[o + 1] = byte(f.joint[k]); map[o + 2] = byte(f.rough[k]); map[o + 3] = byte(f.occlusion[k]);
    const left = f.height[j * n + (i + n - 1) % n], right = f.height[j * n + (i + 1) % n];
    const down = f.height[((j + n - 1) % n) * n + i], up = f.height[((j + 1) % n) * n + i];
    const nx = -(right - left) / (2 * dx), ny = -(up - down) / (2 * dy), length = Math.hypot(nx, ny, 1);
    normal[o] = byte(nx / length * 0.5 + 0.5); normal[o + 1] = byte(ny / length * 0.5 + 0.5); normal[o + 2] = byte(1 / length * 0.5 + 0.5); normal[o + 3] = 255;
  }
  const texture = (data: Uint8Array) => {
    const t = new THREE.DataTexture(data, n, n, THREE.RGBAFormat, THREE.UnsignedByteType);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.magFilter = THREE.LinearFilter; t.minFilter = THREE.LinearMipmapLinearFilter;
    t.generateMipmaps = true; t.anisotropy = 8; t.colorSpace = THREE.NoColorSpace;
    t.needsUpdate = true;
    return t;
  };
  return { map: texture(map), normal: texture(normal), size: f.size, tone, rough };
}

let weather: THREE.DataTexture | null = null;

/**
 * What weather does to any wall, one tiling map shared by every family: R soft patches metres across (damp, faded
 * and repainted areas), G sparse vertical streaks (rain runs), B blotches a hand to an arm wide. The material reads
 * it at two unrelated scales, so no facade shows the same patch twice.
 */
export function weatherMap(): THREE.DataTexture {
  if (weather) return weather;
  const n = 256, data = new Uint8Array(n * n * 4);
  const byte = (value: number) => Math.max(0, Math.min(255, Math.round(value * 255)));
  // Value noise with its own period per axis: streaks are narrow across the wall and long down it.
  const stretched = (x: number, y: number, px: number, py: number, seed: number) => {
    const xi = Math.floor(x), yi = Math.floor(y), fx = x - xi, fy = y - yi;
    const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
    const at = (i: number, j: number) => hash2(((i % px) + px) % px, ((j % py) + py) % py, seed);
    const a = at(xi, yi), b = at(xi + 1, yi), c = at(xi, yi + 1), d = at(xi + 1, yi + 1);
    return a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy;
  };
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const u = i / n, v = j / n, o = (j * n + i) * 4;
    const patch = 0.5 + (fbm(u, v, 3, 4, 911) - 0.5) * 2.6;
    const run = stretched(u * 56, v * 3, 56, 3, 409) * 0.75 + stretched(u * 112, v * 5, 112, 5, 433) * 0.25;
    const streak = Math.max(0, Math.min(1, (run - 0.6) / 0.25)) * (0.35 + 0.65 * fbm(u, v, 4, 2, 77));
    const blotch = 0.5 + (fbm(u, v, 14, 3, 523) - 0.5) * 2.4;
    data[o] = byte(patch); data[o + 1] = byte(streak); data[o + 2] = byte(blotch); data[o + 3] = 255;
  }
  weather = new THREE.DataTexture(data, n, n, THREE.RGBAFormat, THREE.UnsignedByteType);
  weather.wrapS = weather.wrapT = THREE.RepeatWrapping;
  weather.magFilter = THREE.LinearFilter; weather.minFilter = THREE.LinearMipmapLinearFilter;
  weather.generateMipmaps = true; weather.colorSpace = THREE.NoColorSpace;
  weather.needsUpdate = true;
  return weather;
}

const cache = new Map<string, Pattern>();

/** The pattern of a family and its structural parameters (colours do not matter here). */
export function buildingPattern(p: PatternParams): Pattern {
  const scale = p.scale_m;
  let key: string, make: () => Pattern;
  switch (p.family) {
    case 'brick':
      key = `brick/${p.bond ?? 'stretcher'}/${scale ?? 0.27}`;
      make = () => bake(brick(p.bond ?? 'stretcher', scale ?? 0.27), 1.0, 0.4);
      break;
    case 'weatherboard': case 'log': {
      const vertical = p.orientation === 'vertical', profile = p.family === 'log' ? 'bevel' : p.profile ?? 'bevel';
      key = `boards/${profile}/${scale ?? 0.13}/${vertical}`;
      make = () => bake(boards(profile, scale ?? 0.13, vertical), 1.0, 0.3);
      break;
    }
    case 'granite': case 'stone': case 'ceramic_tile': case 'slate': {
      const finish = p.finish ?? (p.family === 'granite' ? 'ashlar' : 'slab'), speckle = p.family === 'granite' ? 0.34 : 0.1;
      key = `ashlar/${finish}/${scale ?? 0.45}/${speckle}`;
      make = () => bake(ashlar(finish, scale ?? 0.45, speckle), 1.0, 0.4);
      break;
    }
    case 'rubble':
      key = `rubble/${scale ?? 0.45}`;
      make = () => bake(rubble(scale ?? 0.45), 1.0, 0.3);
      break;
    case 'concrete':
      key = `concrete/${p.finish ?? 'cast'}`;
      make = () => bake(concrete(p.finish ?? 'cast'), 1.0, 0.3);
      break;
    case 'sheet_metal': case 'copper':
      key = `metal/${p.profile ?? 'standing_seam'}/${scale ?? 0.55}`;
      make = p.profile === 'cassette'
        ? () => bake(cassettes(Math.min(1.5, Math.max(0.3, scale ?? 0.9))), 1.0, 0.4)
        : () => bake(seamedMetal(p.profile ?? 'standing_seam', scale ?? 0.55), 1.0, 0.5);
      break;
    case 'clay_tile': case 'concrete_tile':
      key = `tile/${p.profile ?? 'pantile'}/${scale ?? 0.3}`;
      make = () => bake(tiles(p.profile ?? 'pantile', scale ?? 0.3), 1.0, 0.3);
      break;
    case 'felt':
      key = `felt/${p.profile ?? 'plain'}`;
      make = () => bake(felt(p.profile ?? 'plain'), 1.0, 0.2);
      break;
    case 'painted_wood': case 'metal':
      key = 'paint';
      make = () => bake(paint(), 1.0, 0.3);
      break;
    default:
      key = `plaster/${p.finish ?? 'fine'}`;
      make = () => bake(plaster(p.finish ?? 'fine'), 1.0, 0.3);
  }
  let pattern = cache.get(key);
  if (!pattern) { pattern = make(); cache.set(key, pattern); }
  return pattern;
}
