// Tileable sky noise for the cloud layer (project-owned): R = cumulus base fbm, G = billow (inverted Worley),
// B = fine detail fbm, A = stretched cirrus streaks. Every channel repeats exactly over the texture.
import { Img, rng } from './image.mjs';

function gradients(period, seed) {
  const r = rng(seed), g = [];
  for (let i = 0; i < period * period; i++) { const a = r() * Math.PI * 2; g.push([Math.cos(a), Math.sin(a)]); }
  return g;
}

function perlin(x, y, period, grad) {
  const xi = Math.floor(x), yi = Math.floor(y), fx = x - xi, fy = y - yi;
  const fade = t => t * t * t * (t * (t * 6 - 15) + 10);
  const dot = (i, j) => {
    const g = grad[(((yi + j) % period + period) % period) * period + (((xi + i) % period + period) % period)];
    return g[0] * (fx - i) + g[1] * (fy - j);
  };
  const u = fade(fx), v = fade(fy);
  const a = dot(0, 0) + (dot(1, 0) - dot(0, 0)) * u, b = dot(0, 1) + (dot(1, 1) - dot(0, 1)) * u;
  return (a + (b - a) * v) * 1.414;
}

function worley(x, y, period, seed) {
  const r = rng(seed), pts = [];
  for (let i = 0; i < period * period; i++) pts.push([r(), r()]);
  return (u, v) => {
    const px = u * period, py = v * period, xi = Math.floor(px), yi = Math.floor(py);
    let best = 9;
    for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) {
      const cx = xi + i, cy = yi + j, k = ((cy % period + period) % period) * period + ((cx % period + period) % period);
      const d = Math.hypot(cx + pts[k][0] - px, cy + pts[k][1] - py);
      best = Math.min(best, d);
    }
    return best;
  };
}

export function cloudNoise(size, seed) {
  const img = new Img(size, size);
  // Gradient lattices are cached per octave period and seed.
  const w1 = worley(0, 0, 6, seed + 50), w2 = worley(0, 0, 12, seed + 51);
  const lattice = new Map();
  const grads = (p, s) => { const k = `${p}:${s}`; if (!lattice.has(k)) lattice.set(k, gradients(p, s)); return lattice.get(k); };
  const fb = (u, v, period, octaves, s, stretch = 1) => {
    let sum = 0, amp = 0.5, norm = 0;
    for (let o = 0; o < octaves; o++) {
      const p = period << o;
      sum += amp * perlin(u * p, v * p * stretch, p, grads(p, s + o)); norm += amp; amp *= 0.5;
    }
    return sum / norm;
  };
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const u = x / size, v = y / size, o = (y * size + x) * 4;
    img.data[o] = Math.min(1, Math.max(0, fb(u, v, 3, 5, seed) * 0.9 + 0.5));
    img.data[o + 1] = Math.min(1, Math.max(0, 1 - (w1(u, v) * 0.65 + w2(u, v) * 0.35) * 1.15));
    img.data[o + 2] = Math.min(1, Math.max(0, fb(u, v, 16, 3, seed + 20) * 0.9 + 0.5));
    img.data[o + 3] = Math.min(1, Math.max(0, fb(u, v, 2, 5, seed + 30, 4) * 1.1 + 0.5));
  }
  return img;
}
