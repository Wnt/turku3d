// Seeded, periodic density channels: cumulus (R), warped cirrus (G), fine detail (B).
export function cloudPixels(size = 512) {
  const pixels = new Uint8Array(size * size * 4);
  const hash = (x: number, y: number, periodX: number, periodY = periodX) => {
    let h = Math.imul((x % periodX + periodX) % periodX, 374761393) ^ Math.imul((y % periodY + periodY) % periodY, 668265263);
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
  };
  const noise = (x: number, y: number, periodX: number, periodY = periodX) => {
    const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
    const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
    const a = hash(ix, iy, periodX, periodY), b = hash(ix + 1, iy, periodX, periodY);
    const c = hash(ix, iy + 1, periodX, periodY), d = hash(ix + 1, iy + 1, periodX, periodY);
    return (a + (b - a) * u) * (1 - v) + (c + (d - c) * u) * v;
  };
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    let n = 0, weight = 0;
    for (let octave = 0; octave < 6; octave++) {
      const period = 8 * 2 ** octave, a = 0.5 ** octave;
      n += noise(x / size * period, y / size * period, period) * a; weight += a;
    }
    const i = (y * size + x) * 4;
    pixels[i] = Math.round(n / weight * 255);
    // Long, warped filaments in their own channel; the cumulus density remains isotropic.
    const warpX = noise(x / size * 8, y / size * 8, 8);
    const warpY = noise(x / size * 8 + 3.7, y / size * 8 + 1.1, 8);
    const filament = noise(x / size * 4 + warpX * 0.7, y / size * 32 + warpY * 2, 4, 32);
    pixels[i + 1] = Math.round(filament * 255);
    pixels[i + 2] = Math.round(noise(x / size * 32, y / size * 32, 32) * 255);
    pixels[i + 3] = 255;
  }
  return pixels;
}
