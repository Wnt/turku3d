// Small float RGBA raster used by the nature asset build: sprite extraction, affine compositing,
// twig strokes, colour dilation and PNG I/O through sharp. Straight (non-premultiplied) alpha.
import sharp from 'sharp';

export class Img {
  constructor(w, h, fill = [0, 0, 0, 0]) {
    this.w = w; this.h = h;
    this.data = new Float32Array(w * h * 4);
    if (fill.some(v => v)) for (let i = 0; i < w * h; i++) this.data.set(fill, i * 4);
  }
  get(x, y) { const i = (y * this.w + x) * 4; return this.data.subarray(i, i + 4); }
  clone() { const c = new Img(this.w, this.h); c.data.set(this.data); return c; }
}

/** Colour (sRGB 0..1) plus an optional separate opacity map. */
export async function loadImage(colorPath, opacityPath) {
  const { data, info } = await sharp(colorPath).removeAlpha().raw().toBuffer({ resolveWithObject: true });
  const img = new Img(info.width, info.height);
  for (let i = 0; i < info.width * info.height; i++) {
    img.data[i * 4] = data[i * 3] / 255; img.data[i * 4 + 1] = data[i * 3 + 1] / 255;
    img.data[i * 4 + 2] = data[i * 3 + 2] / 255; img.data[i * 4 + 3] = 1;
  }
  if (opacityPath) {
    const a = await sharp(opacityPath).extractChannel(0).resize(info.width, info.height).raw().toBuffer();
    for (let i = 0; i < info.width * info.height; i++) img.data[i * 4 + 3] = a[i] / 255;
  }
  return img;
}

export async function resized(path, w, h) {
  const { data } = await sharp(path).removeAlpha().resize(w, h, { fit: 'fill', kernel: 'lanczos3' }).raw().toBuffer({ resolveWithObject: true });
  const img = new Img(w, h);
  for (let i = 0; i < w * h; i++) {
    img.data[i * 4] = data[i * 3] / 255; img.data[i * 4 + 1] = data[i * 3 + 1] / 255;
    img.data[i * 4 + 2] = data[i * 3 + 2] / 255; img.data[i * 4 + 3] = 1;
  }
  return img;
}

export function toRGBA8(img) {
  const out = Buffer.alloc(img.w * img.h * 4);
  for (let i = 0; i < out.length; i++) out[i] = Math.max(0, Math.min(255, Math.round(img.data[i] * 255)));
  return out;
}

export async function savePng(img, path, { alpha = true } = {}) {
  const raw = toRGBA8(img);
  let s = sharp(raw, { raw: { width: img.w, height: img.h, channels: 4 } });
  if (!alpha) s = s.removeAlpha();
  await s.png({ compressionLevel: 9 }).toFile(path);
}

/** Premultiplied bilinear sample; outside the image is transparent. */
export function sample(img, x, y, out) {
  x -= 0.5; y -= 0.5;
  const x0 = Math.floor(x), y0 = Math.floor(y), fx = x - x0, fy = y - y0;
  out[0] = out[1] = out[2] = out[3] = 0;
  for (let j = 0; j < 2; j++) for (let i = 0; i < 2; i++) {
    const px = x0 + i, py = y0 + j;
    if (px < 0 || py < 0 || px >= img.w || py >= img.h) continue;
    const w = (i ? fx : 1 - fx) * (j ? fy : 1 - fy), k = (py * img.w + px) * 4, a = img.data[k + 3] * w;
    out[0] += img.data[k] * a; out[1] += img.data[k + 1] * a; out[2] += img.data[k + 2] * a; out[3] += a;
  }
  if (out[3] > 1e-6) { out[0] /= out[3]; out[1] /= out[3]; out[2] /= out[3]; }
  return out;
}

/** Source-over of one premultiplied-equivalent sample into dst pixel k. */
function over(dst, k, r, g, b, a) {
  const da = dst.data[k + 3], oa = a + da * (1 - a);
  if (oa < 1e-6) return;
  dst.data[k] = (r * a + dst.data[k] * da * (1 - a)) / oa;
  dst.data[k + 1] = (g * a + dst.data[k + 1] * da * (1 - a)) / oa;
  dst.data[k + 2] = (b * a + dst.data[k + 2] * da * (1 - a)) / oa;
  dst.data[k + 3] = oa;
}

/**
 * Draw a sprite region of src into dst. The sprite's pivot (sx, sy in src pixels) lands at (dx, dy); the sprite axis
 * from pivot to tip (src "up", i.e. -y) is rotated to `angle` (radians, 0 = up in dst, positive clockwise) and scaled.
 * `shade(r,g,b) -> [r,g,b]` recolours.
 */
export function drawSprite(dst, src, rect, pivot, dx, dy, angle, scale, shade, opts = {}) {
  const cos = Math.cos(angle), sin = Math.sin(angle), flip = opts.flip ? -1 : 1;
  const corners = [[rect.x, rect.y], [rect.x + rect.w, rect.y], [rect.x, rect.y + rect.h], [rect.x + rect.w, rect.y + rect.h]];
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const [x, y] of corners) {
    const lx = (x - pivot[0]) * scale * flip, ly = (y - pivot[1]) * scale;
    const X = dx + lx * cos - ly * sin, Y = dy + lx * sin + ly * cos;
    minX = Math.min(minX, X); maxX = Math.max(maxX, X); minY = Math.min(minY, Y); maxY = Math.max(maxY, Y);
  }
  const s = [0, 0, 0, 0], alpha = opts.alpha ?? 1;
  for (let y = Math.max(0, Math.floor(minY)); y < Math.min(dst.h, Math.ceil(maxY)); y++) {
    for (let x = Math.max(0, Math.floor(minX)); x < Math.min(dst.w, Math.ceil(maxX)); x++) {
      const px = x + 0.5 - dx, py = y + 0.5 - dy;
      const lx = (px * cos + py * sin) / scale * flip, ly = (-px * sin + py * cos) / scale;
      const sx = pivot[0] + lx, sy = pivot[1] + ly;
      if (sx < rect.x || sy < rect.y || sx > rect.x + rect.w || sy > rect.y + rect.h) continue;
      sample(src, sx, sy, s);
      if (s[3] < 0.004) continue;
      const [r, g, b] = shade ? shade(s[0], s[1], s[2], sx, sy) : s;
      over(dst, (y * dst.w + x) * 4, r, g, b, s[3] * alpha);
    }
  }
}

/** Tapered stroke from p0 (radius r0) to p1 (r1), anti-aliased, with a lighter centre line like a lit twig. */
export function drawStroke(dst, p0, p1, r0, r1, color) {
  const minX = Math.floor(Math.min(p0[0] - r0, p1[0] - r1) - 1), maxX = Math.ceil(Math.max(p0[0] + r0, p1[0] + r1) + 1);
  const minY = Math.floor(Math.min(p0[1] - r0, p1[1] - r1) - 1), maxY = Math.ceil(Math.max(p0[1] + r0, p1[1] + r1) + 1);
  const vx = p1[0] - p0[0], vy = p1[1] - p0[1], len2 = vx * vx + vy * vy || 1;
  for (let y = Math.max(0, minY); y < Math.min(dst.h, maxY); y++) for (let x = Math.max(0, minX); x < Math.min(dst.w, maxX); x++) {
    const px = x + 0.5 - p0[0], py = y + 0.5 - p0[1];
    const t = Math.max(0, Math.min(1, (px * vx + py * vy) / len2));
    const d = Math.hypot(px - vx * t, py - vy * t), r = r0 + (r1 - r0) * t;
    const a = Math.max(0, Math.min(1, r - d + 0.5));
    if (a <= 0) continue;
    const shade = 0.75 + 0.35 * (1 - Math.min(1, d / Math.max(r, 0.5)));
    over(dst, (y * dst.w + x) * 4, color[0] * shade, color[1] * shade, color[2] * shade, a);
  }
}

/** Polyline of a quadratic Bezier, for twigs. */
export function bezier(p0, c, p1, n = 12) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, u = 1 - t;
    pts.push([u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0], u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1]]);
  }
  return pts;
}

export function drawPolyline(dst, pts, r0, r1, color) {
  for (let i = 0; i < pts.length - 1; i++) {
    const a = r0 + (r1 - r0) * i / (pts.length - 1), b = r0 + (r1 - r0) * (i + 1) / (pts.length - 1);
    drawStroke(dst, pts[i], pts[i + 1], a, b, color);
  }
}

/** Fill RGB of (nearly) transparent texels from their neighbours so mips and bilinear filtering never pull in black. */
export function dilate(img, passes = 24) {
  const { w, h } = img;
  let src = img.data, known = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) known[i] = src[i * 4 + 3] > 0.02 ? 1 : 0;
  for (let pass = 0; pass < passes; pass++) {
    const next = new Uint8Array(known), out = new Float32Array(src);
    let changed = 0;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = y * w + x;
      if (known[i]) continue;
      let r = 0, g = 0, b = 0, n = 0;
      for (let j = -1; j <= 1; j++) for (let k = -1; k <= 1; k++) {
        const xx = x + k, yy = y + j;
        if (xx < 0 || yy < 0 || xx >= w || yy >= h) continue;
        const q = yy * w + xx;
        if (!known[q]) continue;
        r += src[q * 4]; g += src[q * 4 + 1]; b += src[q * 4 + 2]; n++;
      }
      if (n) { out[i * 4] = r / n; out[i * 4 + 1] = g / n; out[i * 4 + 2] = b / n; next[i] = 1; changed++; }
    }
    src = out; known = next;
    if (!changed) break;
  }
  // Remaining far texels get the mean colour of the image's opaque texels.
  let r = 0, g = 0, b = 0, n = 0;
  for (let i = 0; i < w * h; i++) if (img.data[i * 4 + 3] > 0.5) { r += img.data[i * 4]; g += img.data[i * 4 + 1]; b += img.data[i * 4 + 2]; n++; }
  for (let i = 0; i < w * h; i++) {
    img.data[i * 4 + 3] = img.data[i * 4 + 3];
    if (known[i]) { img.data[i * 4] = src[i * 4]; img.data[i * 4 + 1] = src[i * 4 + 1]; img.data[i * 4 + 2] = src[i * 4 + 2]; }
    else if (n) { img.data[i * 4] = r / n; img.data[i * 4 + 1] = g / n; img.data[i * 4 + 2] = b / n; }
  }
  return img;
}

/** Connected components of alpha > 0.5 on a downsampled grid, returned as full-resolution rects, largest first. */
export function sprites(img, { step = 4, minArea = 0.0015, margin = 6 } = {}) {
  const gw = Math.floor(img.w / step), gh = Math.floor(img.h / step), mask = new Uint8Array(gw * gh);
  for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) {
    let a = 0;
    for (let j = 0; j < step; j++) for (let i = 0; i < step; i++) a = Math.max(a, img.data[((y * step + j) * img.w + x * step + i) * 4 + 3]);
    mask[y * gw + x] = a > 0.35 ? 1 : 0;
  }
  const label = new Int32Array(gw * gh).fill(-1), rects = [];
  for (let s = 0; s < gw * gh; s++) {
    if (!mask[s] || label[s] >= 0) continue;
    const stack = [s]; label[s] = rects.length;
    let minX = gw, minY = gh, maxX = 0, maxY = 0, area = 0;
    while (stack.length) {
      const c = stack.pop(), cx = c % gw, cy = (c - cx) / gw;
      area++; minX = Math.min(minX, cx); maxX = Math.max(maxX, cx); minY = Math.min(minY, cy); maxY = Math.max(maxY, cy);
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1], [1, -1], [-1, 1]]) {
        const nx = cx + dx, ny = cy + dy;
        if (nx < 0 || ny < 0 || nx >= gw || ny >= gh) continue;
        const q = ny * gw + nx;
        if (mask[q] && label[q] < 0) { label[q] = rects.length; stack.push(q); }
      }
    }
    rects.push({ x: Math.max(0, minX * step - margin), y: Math.max(0, minY * step - margin),
      w: Math.min(img.w, (maxX + 1) * step + margin) - Math.max(0, minX * step - margin),
      h: Math.min(img.h, (maxY + 1) * step + margin) - Math.max(0, minY * step - margin), area: area / (gw * gh) });
  }
  return rects.filter(r => r.area >= minArea).sort((a, b) => b.area - a.area);
}

/** Deterministic PRNG (mulberry32). */
export function rng(seed) {
  let a = seed >>> 0;
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  next.range = (lo, hi) => lo + (hi - lo) * next();
  next.int = (lo, hi) => Math.floor(lo + (hi - lo + 1) * next());
  next.pick = list => list[Math.floor(next() * list.length)];
  return next;
}

export function blit(dst, src, ox, oy) {
  for (let y = 0; y < src.h; y++) {
    const row = (oy + y) * dst.w + ox;
    dst.data.set(src.data.subarray(y * src.w * 4, (y + 1) * src.w * 4), row * 4);
  }
}

export function srgbToLinear(c) { return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; }
export function linearToSrgb(c) { return c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055; }

/** 2x2 box downsample. */
export function half(img) {
  const out = new Img(img.w >> 1, img.h >> 1);
  for (let y = 0; y < out.h; y++) for (let x = 0; x < out.w; x++) for (let c = 0; c < 4; c++) {
    const k = (i, j) => img.data[((y * 2 + j) * img.w + x * 2 + i) * 4 + c];
    out.data[(y * out.w + x) * 4 + c] = (k(0, 0) + k(1, 0) + k(0, 1) + k(1, 1)) / 4;
  }
  return out;
}
