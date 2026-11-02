/** Updated once per world frame from the server-synchronised clock (or the fixed QA phase). */
export const riverClock = { seconds: 0 };

/** Periodic directional wave slopes. Integer frequencies keep every mip level seamless. */
export function riverWavePixels(size = 128): Uint8Array {
  const pixels = new Uint8Array(size * size * 4);
  const spectrum = [[2, 1, 0.085, 0.4], [3, -2, 0.07, 2.1], [5, 2, 0.045, 1.2],
    [7, -3, 0.034, 3.4], [11, 4, 0.025, 5.2], [13, -7, 0.018, 0.7],
    [17, 3, 0.012, 4.3], [19, -9, 0.009, 2.7]];
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    let dx = 0, dz = 0;
    for (const [kx, kz, amplitude, phase] of spectrum) {
      const slope = amplitude * Math.cos((x * kx + y * kz) / size * Math.PI * 2 + phase) / Math.hypot(kx, kz);
      dx += kx * slope; dz += kz * slope;
    }
    // Store slopes over ±0.4 instead of almost-white normals: retain 8-bit precision in calm water.
    pixels.set([Math.round((dx / 0.8 + 0.5) * 255), Math.round((dz / 0.8 + 0.5) * 255), 255, 255], (y * size + x) * 4);
  }
  return pixels;
}

/** Wrap in double precision before uploading floats; all clients use server-synchronised UTC seconds. */
export function riverWaveOffsets(seconds: number): [number, number, number, number] {
  const wrap = (value: number) => value - Math.floor(value);
  return [wrap(seconds * 0.0042), wrap(seconds * -0.0027),
    wrap(seconds * 0.012 + 0.37), wrap(seconds * 0.0065 + 0.61)];
}
