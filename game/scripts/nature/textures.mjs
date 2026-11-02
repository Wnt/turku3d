// Texture stage of the nature asset build: leaf/needle/grass cards composed from CC0 leaf scans into one foliage
// atlas, the bark column atlas, and the fence/wall material tiles. Every composition is our own; the inputs are
// ambientCG and Poly Haven CC0 scans listed in public/assets/nature/README.md.
import path from 'node:path';
import { Img, loadImage, resized, sample, drawSprite, drawPolyline, bezier, dilate, sprites, rng, blit, srgbToLinear, linearToSrgb } from './image.mjs';

export const CELL = 512;
/** Foliage atlas: 4 x 4 cells of 512 px. Card pivot (twig base) is the bottom centre of a cell. */
export const FOLIAGE_CELLS = ['birch', 'linden', 'maple', 'oak', 'rowan', 'willow', 'spruce', 'pine',
  'lilac', 'rose', 'hedge', 'juniper', 'grass', 'lawn', 'meadow', 'rosette'];
/** Bark column atlas: 8 slots of 512 x 512 (448 px content + 32 px wrap gutters each side). U wraps once around
 *  a branch inside the content; V repeats over the whole atlas height (1 m of bark per repeat). */
export const BARK_SLOTS = ['birch', 'pine', 'spruce', 'oak', 'linden', 'maple', 'smooth', 'willow'];
export const BARK_SLOT = 512, BARK_GUTTER = 32;

const acg = (src, id, map, ext = 'png', res = '2K-PNG') => path.join(src, 'acg', id, `${id}_${res}_${map}.${ext}`);
const leafSet = async (src, id) => loadImage(acg(src, id, 'Color'), acg(src, id, 'Opacity'));

/** Leaf sprites with a pivot at their base (petiole) and their own tip direction. */
function leafSprites(img, baseAngle, { minArea = 0.004, keep = 12, minAspect = 0 } = {}) {
  const rects = sprites(img, { minArea }).slice(0, keep).filter(r => Math.max(r.w, r.h) / Math.min(r.w, r.h) >= minAspect);
  return rects.map(rect => {
    // Pivot: the alpha centroid of the last 6 % of the sprite along its base direction.
    const along = baseAngle === 0 ? 'bottom' : baseAngle > 0 ? 'left' : 'right';
    let sx = 0, sy = 0, n = 0;
    for (let y = rect.y; y < rect.y + rect.h; y++) for (let x = rect.x; x < rect.x + rect.w; x++) {
      const a = img.data[(y * img.w + x) * 4 + 3];
      if (a < 0.5) continue;
      const t = along === 'bottom' ? (y - rect.y) / rect.h : along === 'left' ? 1 - (x - rect.x) / rect.w : (x - rect.x) / rect.w;
      if (t < 0.94) continue;
      sx += x; sy += y; n++;
    }
    const pivot = n ? [sx / n, sy / n] : along === 'bottom' ? [rect.x + rect.w / 2, rect.y + rect.h] : along === 'left' ? [rect.x, rect.y + rect.h / 2] : [rect.x + rect.w, rect.y + rect.h / 2];
    const length = along === 'bottom' ? rect.h : rect.w;
    return { img, rect, pivot, baseAngle, length };
  });
}

function shadeLeaf(r, { bright = 1, warm = 0, sat = 1, tint = null }) {
  return (R, G, B) => {
    let l = 0.3 * R + 0.59 * G + 0.11 * B;
    let r0 = l + (R - l) * sat, g0 = l + (G - l) * sat, b0 = l + (B - l) * sat;
    if (tint) { const t = l * 1.6; r0 = r0 * 0.25 + t * tint[0] * 0.75; g0 = g0 * 0.25 + t * tint[1] * 0.75; b0 = b0 * 0.25 + t * tint[2] * 0.75; }
    return [Math.min(1, r0 * bright * (1 + warm * 0.25)), Math.min(1, g0 * bright * (1 + warm * 0.08)), Math.min(1, b0 * bright * (1 - warm * 0.3))];
  };
}

/** Place one sprite so its base sits at p with its tip pointing at angle (0 = up, clockwise) and a given length. */
function placeLeaf(dst, leaf, p, angle, length, shade, opts) {
  drawSprite(dst, leaf.img, leaf.rect, leaf.pivot, p[0], p[1], angle - leaf.baseAngle, length / leaf.length, shade, opts);
}

const TWIG = [0.30, 0.25, 0.20];
/** Cards are composed on a 768 px canvas and then fitted into a 512 px cell, so nothing is clipped at cell edges. */
const S = 768;

/** Uniform downscale into a CELL keeping the pivot (bottom centre, or centre for rosettes) and a margin. */
function fit(img, centred = false) {
  let minX = S, minY = S, maxX = 0, maxY = 0;
  for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) if (img.data[(y * S + x) * 4 + 3] > 0.02) {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }
  const px = S / 2, py = centred ? S / 2 : S - 4, margin = 10;
  const half = Math.max(px - minX, maxX - px, centred ? Math.max(py - minY, maxY - py) : 0);
  const k = Math.min(1, (CELL / 2 - margin) / Math.max(1, half), centred ? 1 : (CELL - margin - 1) / Math.max(1, py - minY));
  const out = new Img(CELL, CELL), sampleOut = [0, 0, 0, 0];
  const cx = CELL / 2, cy = centred ? CELL / 2 : CELL - 4 * k;
  for (let y = 0; y < CELL; y++) for (let x = 0; x < CELL; x++) {
    const sx = px + (x + 0.5 - cx) / k, sy = py + (y + 0.5 - cy) / k;
    // Box-filtered minification: average a few bilinear taps across the footprint.
    let r = 0, g = 0, b = 0, a = 0;
    const taps = Math.max(1, Math.ceil(1 / k));
    for (let j = 0; j < taps; j++) for (let i = 0; i < taps; i++) {
      sample(img, sx + ((i + 0.5) / taps - 0.5) / k, sy + ((j + 0.5) / taps - 0.5) / k, sampleOut);
      r += sampleOut[0] * sampleOut[3]; g += sampleOut[1] * sampleOut[3]; b += sampleOut[2] * sampleOut[3]; a += sampleOut[3];
    }
    const o = (y * CELL + x) * 4;
    if (a > 1e-6) { out.data[o] = r / a; out.data[o + 1] = g / a; out.data[o + 2] = b / a; }
    out.data[o + 3] = a / (taps * taps);
  }
  out.scale = k;
  return out;
}

/** Points along a polyline every `step` px from fraction t0, with their direction angle. */
function along(pts, step, t0 = 0) {
  const lengths = [0];
  for (let i = 1; i < pts.length; i++) lengths.push(lengths[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  const total = lengths.at(-1), out = [];
  for (let s = total * t0; s <= total; s += step) {
    let i = 1;
    while (i < pts.length - 1 && lengths[i] < s) i++;
    const f = (s - lengths[i - 1]) / Math.max(1e-6, lengths[i] - lengths[i - 1]);
    const a = pts[i - 1], b = pts[i];
    out.push({ p: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f], dir: Math.atan2(b[0] - a[0], -(b[1] - a[1])), t: s / total });
  }
  return out;
}

/** Broadleaf twig card: a curved twig with side twiglets; alternate leaves along them, terminal leaves at tips. */
function broadleafCard(leaves, seed, o) {
  const r = rng(seed), img = new Img(S, S);
  const base = [S / 2, S - 4], top = [S / 2 + r.range(-50, 50), o.reach ?? 300];
  const main = bezier(base, [S / 2 + r.range(-60, 60), S * 0.5], top);
  const twigs = [main];
  for (const t of o.sides ?? [0.3, 0.5, 0.68]) {
    const p = main[Math.round(t * (main.length - 1))], side = twigs.length % 2 ? 1 : -1;
    const end = [p[0] + side * r.range(150, 230), p[1] - r.range(50, 130)];
    twigs.push(bezier(p, [(p[0] + end[0]) / 2 + side * 10, p[1] - 10], end, 8));
  }
  for (const [i, t] of twigs.entries()) drawPolyline(img, t, i ? 3 : 5.5, 1.2, TWIG);
  const placements = [];
  for (const [i, twig] of twigs.entries()) {
    let side = 1;
    for (const q of along(twig, o.spacing, i ? 0.15 : 0.22)) {
      side = -side;
      placements.push({ p: q.p, angle: q.dir + side * r.range(o.spread[0], o.spread[1]) * Math.PI / 180, t: q.t });
      if (o.opposite) placements.push({ p: q.p, angle: q.dir - side * r.range(o.spread[0], o.spread[1]) * Math.PI / 180, t: q.t });
    }
    const end = twig.at(-1), prev = twig.at(-2);
    placements.push({ p: end, angle: Math.atan2(end[0] - prev[0], -(end[1] - prev[1])) + r.range(-0.2, 0.2), t: 1 });
  }
  // A back layer of leaves at random points along the twigs fills the cluster, as in a real crown's outer shell.
  for (let k = 0; k < (o.fill ?? 0); k++) {
    const twig = r.pick(twigs), i = r.int(Math.floor(twig.length * 0.25), twig.length - 2);
    const dir = Math.atan2(twig[i + 1][0] - twig[i][0], -(twig[i + 1][1] - twig[i][1]));
    placements.push({ p: twig[i], angle: dir + r.range(-1.6, 1.6), t: i / (twig.length - 1), back: true });
  }
  // Back-to-front by a random depth: deeper leaves are darker, as inside a real twig cluster.
  const order = placements.map(pl => ({ ...pl, depth: pl.back ? r() * 0.45 : r() })).sort((a, b) => a.depth - b.depth);
  for (const pl of order) {
    const leaf = r.pick(leaves), len = o.leaf * r.range(0.78, 1.08) * (0.8 + 0.25 * pl.t);
    const bright = (o.bright ?? 1) * (0.5 + 0.58 * pl.depth);
    placeLeaf(img, leaf, pl.p, pl.angle, len, shadeLeaf(r, { bright, warm: r.range(-0.3, 0.6) * (o.warm ?? 0.3), sat: o.sat ?? 1, tint: o.tint }));
  }
  return img;
}

/** A pinnate compound leaf (rowan, rose) drawn into its own sprite image, pivot at the rachis base. */
function compoundLeaf(leaflets, seed, { pairs, leaflet, spacing, angle }) {
  const r = rng(seed), h = Math.round(pairs * spacing + leaflet * 1.3 + 20), w = Math.round(leaflet * 2.6 + 20);
  const img = new Img(w, h), base = [w / 2, h - 2], tip = [w / 2 + r.range(-6, 6), leaflet * 0.9 + 6];
  const rachis = bezier(base, [w / 2 + r.range(-10, 10), h / 2], tip, 10);
  drawPolyline(img, rachis, 1.8, 0.8, [0.36, 0.42, 0.22]);
  const pts = along(rachis, spacing, 0.18);
  for (const [i, q] of pts.entries()) {
    const scale = 0.85 + 0.25 * (i / Math.max(1, pts.length - 1));
    for (const side of [-1, 1]) placeLeaf(img, r.pick(leaflets), q.p, q.dir + side * angle * Math.PI / 180, leaflet * scale, null);
  }
  placeLeaf(img, r.pick(leaflets), tip, 0, leaflet * 1.05, null);
  return { img, rect: { x: 0, y: 0, w, h }, pivot: base, baseAngle: 0, length: h };
}

/** Conifer branchlet: a straight twig with sprigs fanning out both sides (spruce, juniper). */
function coniferCard(sprigs, seed, o) {
  const r = rng(seed), img = new Img(S, S);
  const main = bezier([S / 2, S - 4], [S / 2 + r.range(-25, 25), S / 2], [S / 2 + r.range(-30, 30), 40], 10);
  drawPolyline(img, main, 5, 1.5, [0.32, 0.24, 0.18]);
  const placements = [];
  for (const q of along(main, o.spacing, 0.05)) {
    for (const side of [-1, 1]) placements.push({ p: q.p, angle: q.dir + side * r.range(o.spread[0], o.spread[1]) * Math.PI / 180, t: q.t, len: o.sprig * (1.15 - 0.55 * q.t) });
  }
  placements.push({ p: main.at(-1), angle: 0, t: 1, len: o.sprig * 0.7 });
  for (const pl of placements.map(pl => ({ ...pl, d: r() })).sort((a, b) => a.d - b.d)) {
    placeLeaf(img, r.pick(sprigs), pl.p, pl.angle, pl.len * r.range(0.85, 1.1),
      shadeLeaf(r, { bright: 0.75 + 0.3 * pl.d, sat: o.sat ?? 1, tint: o.tint }));
  }
  return img;
}

/** Pine: needle tufts radiating from the tips of a short forked twig. */
function pineCard(needles, seed, o) {
  const r = rng(seed), img = new Img(S, S);
  const main = bezier([S / 2, S - 4], [S / 2 + r.range(-20, 20), S * 0.6], [S / 2 + r.range(-20, 20), 200], 10);
  const forks = [main, bezier(main[5], [S / 2 - 80, 300], [S / 2 - 140, 230], 6), bezier(main[7], [S / 2 + 80, 290], [S / 2 + 150, 240], 6)];
  for (const [i, f] of forks.entries()) drawPolyline(img, f, i ? 4 : 6, 3, [0.42, 0.30, 0.20]);
  const all = [];
  for (const f of forks) for (const q of along(f, 9, 0.35)) {
    const n = q.t > 0.8 ? 7 : 3;
    for (let k = 0; k < n; k++) all.push({ p: q.p, angle: q.dir + r.range(-1.25, 1.25) * (q.t > 0.85 ? 0.85 : 1), len: o.needle * r.range(0.75, 1.05), d: r() });
  }
  for (const pl of all.sort((a, b) => a.d - b.d)) {
    placeLeaf(img, r.pick(needles), pl.p, pl.angle, pl.len, shadeLeaf(r, { bright: 0.7 + 0.35 * pl.d, tint: o.tint }));
  }
  return img;
}

/** Grass clump card: blades rising from a narrow base, fanning out. */
function grassCard(blades, seed, o) {
  const r = rng(seed), img = new Img(S, S), list = [];
  for (let i = 0; i < o.count; i++) {
    const x = S / 2 + r.range(-o.base, o.base), edge = Math.abs(x - S / 2) / o.base;
    // A tapered card (lawn) thins out and shortens towards both ends, so overlapping cards blend without straight cuts.
    if (o.taper && r() < edge * edge * o.taper) continue;
    const len = r.range(o.len[0], o.len[1]) * (o.taper ? 1 - 0.55 * edge * edge : 1);
    // Lawn blades start at staggered heights: no crisp straight base line where a card meets the ground.
    const y = S - 2 - (o.taper ? r() * r() * 46 : 0);
    list.push({ p: [x, y], angle: (x - S / 2) / o.base * o.fan + r.range(-0.18, 0.18), len, d: r(), blade: r.pick(blades) });
  }
  for (const pl of list.sort((a, b) => a.d - b.d)) {
    const dry = r() < (o.dry ?? 0.12);
    placeLeaf(img, pl.blade, pl.p, pl.angle, pl.len, shadeLeaf(r, { bright: 0.65 + 0.4 * pl.d, warm: dry ? 0.9 : r.range(-0.2, 0.3), sat: dry ? 0.6 : 1, tint: o.tint }));
  }
  if (o.flowers) {
    for (let i = 0; i < o.flowers; i++) {
      const c = r.pick([[0.95, 0.93, 0.85], [0.96, 0.82, 0.18], [0.62, 0.52, 0.86], [0.95, 0.95, 0.95]]);
      const x = S / 2 + r.range(-170, 170), y = r.range(60, 260), rad = r.range(5, 9);
      drawPolyline(img, [[x, y], [x + r.range(-10, 10), S - 2]], 1.2, 1.6, [0.30, 0.42, 0.16]);
      for (let k = 0; k < 6; k++) {
        const a = k / 6 * Math.PI * 2 + r();
        drawPolyline(img, [[x, y], [x + Math.sin(a) * rad, y - Math.cos(a) * rad]], rad * 0.45, rad * 0.25, c);
      }
      drawPolyline(img, [[x - 1, y], [x + 1, y]], rad * 0.35, rad * 0.35, [0.9, 0.7, 0.15]);
    }
  }
  return img;
}

/** Ground rosette seen from above: leaves radiating from the centre of the cell. */
function rosetteCard(leaves, seed, o) {
  const r = rng(seed), img = new Img(S, S), c = [S / 2, S / 2], list = [];
  for (let i = 0; i < o.count; i++) list.push({ angle: i / o.count * Math.PI * 2 + r.range(-0.2, 0.2), len: r.range(o.len[0], o.len[1]), d: r() });
  for (const pl of list.sort((a, b) => a.d - b.d)) {
    placeLeaf(img, r.pick(leaves), c, pl.angle, pl.len, shadeLeaf(r, { bright: 0.7 + 0.35 * pl.d, tint: o.tint }));
  }
  for (let i = 0; i < (o.flowers ?? 0); i++) {
    const a = r() * Math.PI * 2, d = r.range(10, 70), x = c[0] + Math.sin(a) * d, y = c[1] + Math.cos(a) * d;
    for (let k = 0; k < 18; k++) {
      const b = k / 18 * Math.PI * 2;
      drawPolyline(img, [[x, y], [x + Math.sin(b) * 14, y - Math.cos(b) * 14]], 2.2, 1.4, [0.98, 0.80, 0.10]);
    }
  }
  return img;
}

/** Dense bush card: sprigs radiating over a half disc, with an opaque core for hedge interiors. */
function bushCard(sprigs, seed, o) {
  const r = rng(seed), img = new Img(S, S), list = [];
  for (let i = 0; i < o.count; i++) {
    const a = r.range(-1.45, 1.45), d = r.range(0, 0.55) * S;
    list.push({ p: [S / 2 + Math.sin(a) * d * 0.6, S - 4 - Math.cos(a) * d * 0.5], angle: a * 0.9 + r.range(-0.3, 0.3), len: o.sprig * r.range(0.7, 1.05), d: r() });
  }
  for (const pl of list.sort((a, b) => a.d - b.d)) {
    placeLeaf(img, r.pick(sprigs), pl.p, pl.angle, pl.len, shadeLeaf(r, { bright: 0.65 + 0.4 * pl.d, tint: o.tint, sat: o.sat ?? 1 }));
  }
  return img;
}

/** Scale an albedo card so the mean linear luminance of its opaque texels hits `target`, keeping hue. */
function calibrate(img, target, satScale = 1) {
  let sum = 0, n = 0;
  for (let i = 0; i < img.w * img.h; i++) {
    if (img.data[i * 4 + 3] < 0.5) continue;
    sum += 0.2126 * srgbToLinear(img.data[i * 4]) + 0.7152 * srgbToLinear(img.data[i * 4 + 1]) + 0.0722 * srgbToLinear(img.data[i * 4 + 2]); n++;
  }
  const k = n ? target / (sum / n) : 1;
  for (let i = 0; i < img.w * img.h; i++) {
    const rgb = [0, 1, 2].map(c => srgbToLinear(img.data[i * 4 + c]) * k);
    const l = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
    for (let c = 0; c < 3; c++) img.data[i * 4 + c] = linearToSrgb(Math.max(0, Math.min(1, l + (rgb[c] - l) * satScale)));
  }
  return img;
}

export async function foliageAtlas(src) {
  const set = {};
  for (const [name, id, baseAngle, opts] of [
    ['birch', 'LeafSet005', 0, {}], ['linden', 'LeafSet023', 90, {}], ['maple', 'LeafSet010', 0, {}],
    ['oak', 'LeafSet016', 0, {}], ['narrow', 'LeafSet013', 0, { minAspect: 2.5 }], ['lilac', 'LeafSet004', 0, {}],
    ['small', 'LeafSet024', 0, {}], ['privet', 'LeafSet002', 0, {}], ['sprig', 'LeafSet019', -90, { keep: 3 }],
    ['needle', 'PineNeedles001', 90, { minArea: 0.0008, keep: 20 }], ['blade', 'Foliage001', 0, { minArea: 0.001, keep: 9 }],
    ['weed', 'Foliage003', 0, { minArea: 0.0008, keep: 6 }],
    ['blade2', 'Foliage006', 0, { minArea: 0.0008, keep: 8 }], ['dandelion', 'LeafSet020', 0, { minArea: 0.002 }],
  ]) set[name] = leafSprites(await leafSet(src, id), baseAngle, opts);
  const rowanLeaves = [0, 1, 2].map(k => compoundLeaf(set.narrow, 100 + k, { pairs: 6, leaflet: 64, spacing: 34, angle: 62 }));
  const roseLeaves = [0, 1, 2].map(k => compoundLeaf(set.small, 200 + k, { pairs: 2, leaflet: 58, spacing: 40, angle: 55 }));
  // Seasonal look: Turku summer greens. Card targets are linear albedo luminance of opaque texels.
  const green = [0.36, 0.62, 0.20], needleGreen = [0.22, 0.42, 0.24], juniperGreen = [0.30, 0.46, 0.38];
  const cards = {
    birch: calibrate(fit(broadleafCard(set.birch, 1, { fill: 130, leaf: 44, spacing: 14, spread: [35, 70], warm: 0.2, reach: 200, sides: [0.15, 0.27, 0.39, 0.51, 0.63, 0.75] })), 0.1, 1.2),
    linden: calibrate(fit(broadleafCard(set.linden, 2, { fill: 85, leaf: 64, spacing: 21, spread: [40, 75], sides: [0.18, 0.32, 0.46, 0.6, 0.74] })), 0.095, 1.15),
    maple: calibrate(fit(broadleafCard(set.maple, 3, { fill: 55, leaf: 86, spacing: 30, spread: [40, 80], opposite: true, sides: [0.18, 0.34, 0.5, 0.66] })), 0.095, 1.15),
    oak: calibrate(fit(broadleafCard(set.oak, 4, { fill: 85, leaf: 70, spacing: 21, spread: [35, 70], warm: 0.1, sides: [0.18, 0.32, 0.46, 0.6, 0.74] })), 0.075, 1.05),
    rowan: calibrate(fit(broadleafCard(rowanLeaves, 5, { fill: 22, leaf: 165, spacing: 42, spread: [40, 70], sides: [0.22, 0.42, 0.62] })), 0.09, 1.15),
    willow: calibrate(fit(broadleafCard(set.narrow, 6, { fill: 170, leaf: 80, spacing: 9, spread: [15, 35], reach: 60, sides: [0.2, 0.35, 0.5, 0.65, 0.8], tint: [0.55, 0.62, 0.35] })), 0.07, 0.8),
    spruce: calibrate(fit(coniferCard(set.sprig, 7, { sprig: 190, spacing: 26, spread: [40, 75], tint: needleGreen })), 0.035, 0.9),
    pine: calibrate(fit(pineCard(set.needle, 8, { needle: 150, tint: [0.30, 0.46, 0.26] })), 0.045, 0.9),
    lilac: calibrate(fit(broadleafCard(set.lilac, 9, { fill: 55, leaf: 78, spacing: 26, spread: [40, 70], opposite: true, sides: [0.2, 0.38, 0.56, 0.72] })), 0.06),
    rose: calibrate(fit(broadleafCard(roseLeaves, 10, { fill: 24, leaf: 110, spacing: 34, spread: [35, 65], sides: [0.2, 0.38, 0.56, 0.72] })), 0.06),
    hedge: calibrate(fit(bushCard(set.privet, 11, { count: 120, sprig: 210 })), 0.06),
    juniper: calibrate(fit(coniferCard(set.sprig, 12, { sprig: 150, spacing: 20, spread: [20, 45], tint: juniperGreen, sat: 0.7 })), 0.05, 0.75),
    grass: calibrate(fit(grassCard([...set.blade, ...set.blade2], 13, { count: 46, base: 70, fan: 0.42, len: [230, 470], tint: green })), 0.11, 1.15),
    lawn: calibrate(fit(grassCard([...set.blade, ...set.blade2], 14, { count: 200, base: 360, fan: 0.12, len: [90, 230], dry: 0.06, tint: green, taper: 0.9 })), 0.11, 1.15),
    meadow: calibrate(fit(grassCard([...set.weed, ...set.blade], 15, { count: 34, base: 110, fan: 0.35, len: [260, 500], flowers: 9, dry: 0.25 })), 0.085),
    rosette: calibrate(fit(rosetteCard(set.dandelion, 16, { count: 11, len: [150, 225], flowers: 1 }), true), 0.08),
  };
  void green;
  const atlas = new Img(CELL * 4, CELL * 4);
  for (const [i, name] of FOLIAGE_CELLS.entries()) {
    const card = cards[name];
    // A 6 px transparent border keeps neighbouring cells out of bilinear and early mip footprints.
    for (let y = 0; y < CELL; y++) for (let x = 0; x < CELL; x++) if (x < 6 || y < 4 || x >= CELL - 6 || y >= CELL - 1) card.data[(y * CELL + x) * 4 + 3] = 0;
    dilate(card, 40);
    blit(atlas, card, (i % 4) * CELL, Math.floor(i / 4) * CELL);
  }
  return { atlas, cards };
}

function seamlessDraw(img, fn) {
  for (const ox of [-img.w, 0, img.w]) for (const oy of [-img.h, 0, img.h]) fn(ox, oy);
}

/** Birch bark: a pale scan pushed to chalk white, then dark horizontal lenticels and black patches drawn tileably. */
async function birchBark(src, w, h) {
  const base = await resized(acg(src, 'Bark009', 'Color', 'jpg', '1K-JPG'), w, h), r = rng(77);
  for (let i = 0; i < w * h; i++) {
    const l = 0.3 * base.data[i * 4] + 0.59 * base.data[i * 4 + 1] + 0.11 * base.data[i * 4 + 2];
    const v = 0.62 + (l - 0.55) * 0.55;
    base.data[i * 4] = v * 1.0; base.data[i * 4 + 1] = v * 0.985; base.data[i * 4 + 2] = v * 0.95;
  }
  const dark = [0.13, 0.12, 0.11];
  for (let i = 0; i < 190; i++) {
    const x = r() * w, y = r() * h, len = r.range(10, 55), th = r.range(1.2, 3.2);
    seamlessDraw(base, (ox, oy) => drawPolyline(base, [[x + ox, y + oy], [x + ox + len, y + oy + r.range(-1, 1)]], th, th * 0.6, dark));
  }
  for (let i = 0; i < 9; i++) {
    const x = r() * w, y = r() * h, len = r.range(40, 120), th = r.range(6, 16);
    seamlessDraw(base, (ox, oy) => {
      drawPolyline(base, [[x + ox, y + oy], [x + ox + len * 0.5, y + oy - th * 0.3], [x + ox + len, y + oy]], th, th * 0.3, [0.07, 0.065, 0.06]);
    });
  }
  return base;
}

const BARK_SOURCES = {
  birch: null,
  pine: ['ph', 'pine_bark'], spruce: ['acg', 'Bark004'], oak: ['ph', 'jolcham_oak_bark_01'], linden: ['ph', 'bark_brown_02'],
  maple: ['acg', 'Bark002'], smooth: ['acg', 'Bark010'], willow: ['ph', 'bark_willow'],
};
/** Linear albedo luminance targets and saturation per bark. */
const BARK_LOOK = { birch: [0.42, 1], pine: [0.12, 1.1], spruce: [0.10, 0.6], oak: [0.09, 0.7], linden: [0.10, 0.55], maple: [0.12, 0.45], smooth: [0.14, 0.35], willow: [0.08, 0.6] };

function barkPaths(src, name) {
  const [kind, id] = BARK_SOURCES[name];
  if (kind === 'ph') return [path.join(src, 'polyhaven', id, `${id}_diff_1k.jpg`), path.join(src, 'polyhaven', id, `${id}_nor_gl_1k.jpg`)];
  return [acg(src, id, 'Color', 'jpg', '1K-JPG'), acg(src, id, 'NormalGL', 'jpg', '1K-JPG')];
}

/** Clone-stamp: replace an elliptical area (centre, radii) with the pixels at an offset, feathered at the rim. */
function clonePatch(img, [cx, cy], [rx, ry], [dx, dy]) {
  const copy = img.clone();
  for (let y = Math.floor(cy - ry); y <= cy + ry; y++) for (let x = Math.floor(cx - rx); x <= cx + rx; x++) {
    const d = Math.hypot((x - cx) / rx, (y - cy) / ry);
    if (d >= 1) continue;
    const w = Math.min(1, (1 - d) / 0.35), o = (((y % img.h) + img.h) % img.h * img.w + ((x % img.w) + img.w) % img.w) * 4;
    const sx = ((x + dx) % img.w + img.w) % img.w, sy = ((y + dy) % img.h + img.h) % img.h, so = (sy * img.w + sx) * 4;
    for (let c = 0; c < 4; c++) img.data[o + c] = copy.data[o + c] * (1 - w) + copy.data[so + c] * w;
  }
}

export async function barkAtlas(src) {
  const content = BARK_SLOT - 2 * BARK_GUTTER;
  const albedo = new Img(BARK_SLOT * BARK_SLOTS.length, BARK_SLOT), normal = new Img(BARK_SLOT * BARK_SLOTS.length, BARK_SLOT);
  for (const [i, name] of BARK_SLOTS.entries()) {
    const color = name === 'birch' ? await birchBark(src, content, BARK_SLOT) : await resized(barkPaths(src, name)[0], content, BARK_SLOT);
    const nrm = await resized(name === 'birch' ? acg(src, 'Bark009', 'NormalGL', 'jpg', '1K-JPG') : barkPaths(src, name)[1], content, BARK_SLOT);
    // Bark010 has initials carved into the bark: clone clean bark over them in both maps.
    if (name === 'smooth') for (const img of [color, nrm]) clonePatch(img, [120, 204], [38, 32], [0, 110]);
    calibrate(color, BARK_LOOK[name][0], BARK_LOOK[name][1]);
    for (const [dst, img] of [[albedo, color], [normal, nrm]]) {
      for (let y = 0; y < BARK_SLOT; y++) for (let x = 0; x < BARK_SLOT; x++) {
        const sx = ((x - BARK_GUTTER) % content + content) % content;
        dst.data.set(img.data.subarray((y * content + sx) * 4, (y * content + sx) * 4 + 4), ((y) * dst.w + i * BARK_SLOT + x) * 4);
      }
    }
  }
  return { albedo, normal };
}

/** Fence and wall material tiles (512 px, tileable, 1 m per repeat). Paint colour comes from the instance. */
export async function propTiles(src) {
  // Painted planed wood drawn by our code (the earlier scan carried stencilled lettering in its relief).
  const { albedo: wood, normal: woodNormal } = paintedWood();
  const granite = calibrate(await resized(acg(src, 'Granite003B', 'Color', 'jpg', '1K-JPG'), 512, 512), 0.20, 0.8);
  const graniteNormal = await resized(acg(src, 'Granite003B', 'NormalGL', 'jpg', '1K-JPG'), 512, 512);
  return { wood, woodNormal, granite, graniteNormal, chain: chainLink() };
}

/** Tileable periodic value noise on a w x h lattice, smoothstep-interpolated; returns f(u, v) for u, v in [0, 1). */
function periodicNoise(seed, w, h) {
  const r = rng(seed), lattice = Float32Array.from({ length: w * h }, () => r());
  const at = (i, j) => lattice[((j % h + h) % h) * w + ((i % w + w) % w)];
  return (u, v) => {
    const x = u * w, y = v * h, i = Math.floor(x), j = Math.floor(y), fx = x - i, fy = y - j;
    const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
    return (at(i, j) * (1 - sx) + at(i + 1, j) * sx) * (1 - sy) + (at(i, j + 1) * (1 - sx) + at(i + 1, j + 1) * sx) * sy;
  };
}

/**
 * Painted planed softwood, 1 m tile (512 px), grain along U. Grey albedo (paint colour comes from the instance) and a
 * GL normal map from a height field of grain ridges, fine fibres, two knots and flaked paint. Tileable in U and V.
 */
function paintedWood(size = 512) {
  const grain = periodicNoise(301, 3, 90), fibre = periodicNoise(302, 12, 260), band = periodicNoise(303, 2, 9);
  const wear = periodicNoise(304, 4, 22), flake = periodicNoise(305, 14, 110);
  const knots = [[0.27, 0.31, 0.022], [0.71, 0.78, 0.017]];
  const height = new Float32Array(size * size), albedo = new Img(size, size), normal = new Img(size, size);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    let u = x / size, v = y / size;
    // Grain bends round the knots.
    let knot = 0;
    for (const [ku, kv, kr] of knots) {
      const du = Math.min(Math.abs(u - ku), 1 - Math.abs(u - ku)) / (kr * 2.6), dv = Math.min(Math.abs(v - kv), 1 - Math.abs(v - kv)) / kr;
      const d2 = du * du + dv * dv;
      v += Math.sign(v - kv || 1) * kr * 0.8 * Math.exp(-d2);
      knot = Math.max(knot, Math.exp(-d2 * 3));
    }
    v = ((v % 1) + 1) % 1;
    const g = grain(u, v), f = fibre(u, v);
    const ring = 0.5 + 0.5 * Math.sin((g * 7 + v * 26) * Math.PI);
    const peeled = Math.max(0, Math.min(1, (wear(u, v) - 0.8) * 8)) * Math.max(0, Math.min(1, (flake(u, v) - 0.3) * 3));
    height[y * size + x] = ring * 0.3 + f * 0.2 - knot * 0.5 - peeled * 0.3;
    // Paint: near-uniform light grey with faint grain; bare weathered wood where it flaked off.
    const paint = 0.80 + (band(u, v) - 0.5) * 0.05 + (ring - 0.5) * 0.03 + (f - 0.5) * 0.04 - knot * 0.08;
    const bare = 0.42 + (ring - 0.5) * 0.12 + (f - 0.5) * 0.08;
    const c = paint * (1 - peeled) + bare * peeled;
    const o = (y * size + x) * 4;
    albedo.data[o] = c; albedo.data[o + 1] = c; albedo.data[o + 2] = c; albedo.data[o + 3] = 1;
  }
  const h = (x, y) => height[((y + size) % size) * size + ((x + size) % size)], strength = 1.0;
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const dx = (h(x + 1, y) - h(x - 1, y)) * strength, dy = (h(x, y + 1) - h(x, y - 1)) * strength;
    const l = Math.hypot(dx, dy, 1), o = (y * size + x) * 4;
    // OpenGL convention: +Y (green) points up the image, image rows run down.
    normal.data[o] = 0.5 - 0.5 * dx / l; normal.data[o + 1] = 0.5 + 0.5 * dy / l; normal.data[o + 2] = 0.5 + 0.5 / l; normal.data[o + 3] = 1;
  }
  return { albedo, normal };
}

/** Galvanised chain-link wire, tileable at 1 m (256 px): 5 cm diamonds of 3 mm wire, drawn by our code. */
function chainLink(size = 256, cells = 20) {
  const img = new Img(size, size), pitch = size / cells, wire = 1.15;
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    // Distance to the two diagonal wire families of a diamond lattice (periodic in the tile).
    const u = (x + 0.5) / pitch, v = (y + 0.5) / pitch;
    const d1 = Math.abs(((u + v) % 1 + 1) % 1 - 0.5), d2 = Math.abs(((u - v) % 1 + 1) % 1 - 0.5);
    const d = (0.5 - Math.max(d1, d2)) * pitch / Math.SQRT2;
    const a = Math.max(0, Math.min(1, wire - d + 0.5));
    // A round wire: brighter along its crest.
    const crest = 0.8 + 0.2 * Math.max(0, 1 - d / wire);
    const o = (y * size + x) * 4;
    img.data[o] = 0.62 * crest; img.data[o + 1] = 0.64 * crest; img.data[o + 2] = 0.62 * crest; img.data[o + 3] = a;
  }
  return img;
}
