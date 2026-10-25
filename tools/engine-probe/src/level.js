// Procedural "city block slice" collision + render geometry.
// Coordinates: Y up, metres. +X is "forward" for both the walker and the car.
// Everything is merged into ONE indexed triangle soup, exactly like a streamed
// city tile's collision mesh would be (no primitive shortcuts).

export const CURB_H = 0.12; // 12 cm curb
export const STEP_RISE = 0.17;
export const STEP_RUN = 0.30;
export const N_STEPS = 8;

export function buildLevel() {
  const pos = [];
  const idx = [];
  const col = []; // per-vertex colour for render
  const parts = [];

  const v = (x, y, z, c) => {
    pos.push(x, y, z);
    col.push(c[0], c[1], c[2]);
    return pos.length / 3 - 1;
  };
  const quad = (a, b, c, d) => idx.push(a, b, c, a, c, d);

  // Axis-aligned column from y0 (bottom) to y1 (top). Only emits top + 4 sides.
  function box(name, x0, x1, z0, z1, y0, y1, c) {
    parts.push({ name, x0, x1, z0, z1, y0, y1 });
    // top (CCW seen from +Y)
    quad(v(x0, y1, z0, c), v(x0, y1, z1, c), v(x1, y1, z1, c), v(x1, y1, z0, c));
    const s = c.map((k) => k * 0.8);
    // -X face
    quad(v(x0, y0, z0, s), v(x0, y0, z1, s), v(x0, y1, z1, s), v(x0, y1, z0, s));
    // +X face
    quad(v(x1, y0, z1, s), v(x1, y0, z0, s), v(x1, y1, z0, s), v(x1, y1, z1, s));
    // -Z face
    quad(v(x1, y0, z0, s), v(x0, y0, z0, s), v(x0, y1, z0, s), v(x1, y1, z0, s));
    // +Z face
    quad(v(x0, y0, z1, s), v(x1, y0, z1, s), v(x1, y1, z1, s), v(x0, y1, z1, s));
  }

  // Ramp rising along +X from ya (at x0) to yb (at x1), solid down to yBottom.
  function ramp(name, x0, x1, z0, z1, ya, yb, yBottom, c) {
    parts.push({ name, x0, x1, z0, z1, ya, yb, slopeDeg: (Math.atan2(yb - ya, x1 - x0) * 180) / Math.PI });
    quad(v(x0, ya, z0, c), v(x0, ya, z1, c), v(x1, yb, z1, c), v(x1, yb, z0, c));
    const s = c.map((k) => k * 0.8);
    quad(v(x0, yBottom, z0, s), v(x0, yBottom, z1, s), v(x0, ya, z1, s), v(x0, ya, z0, s));
    quad(v(x1, yBottom, z1, s), v(x1, yBottom, z0, s), v(x1, yb, z0, s), v(x1, yb, z1, s));
    quad(v(x1, yBottom, z0, s), v(x0, yBottom, z0, s), v(x0, ya, z0, s), v(x1, yb, z0, s));
    quad(v(x0, yBottom, z1, s), v(x1, yBottom, z1, s), v(x1, yb, z1, s), v(x0, ya, z1, s));
  }

  const ASPHALT = [0.18, 0.18, 0.2];
  const STONE = [0.55, 0.55, 0.52];
  const GRANITE = [0.42, 0.4, 0.4];
  const CONC = [0.65, 0.63, 0.6];
  const B = -1.0; // bottom of all solids

  const Z0 = -12, Z1 = 12;
  // Road (asphalt) x in [-30, 0]
  box('road', -30, 0, Z0, Z1, B, 0.0, ASPHALT);
  // Granite curb stone 0.15 m wide, top at 12 cm (vertical 12 cm face at x=0)
  box('curb', 0, 0.15, Z0, Z1, B, CURB_H, GRANITE);

  // --- Walker lane  z in [-1.5, 1.5]: sidewalk -> staircase -> platform
  box('sidewalk_walk', 0.15, 6, -1.5, 1.5, B, CURB_H, STONE);
  let x = 6;
  for (let i = 1; i <= N_STEPS; i++) {
    box(`step${i}`, x, x + STEP_RUN, -1.5, 1.5, B, CURB_H + i * STEP_RISE, CONC);
    x += STEP_RUN;
  }
  const platformY = CURB_H + N_STEPS * STEP_RISE;
  box('platform', x, x + 8, -1.5, 1.5, B, platformY, CONC);

  // --- Gentle ramp lane z in [2, 5]: sidewalk -> 15 deg ramp (should climb)
  box('sidewalk_r15', 0.15, 4, 2, 5, B, CURB_H, STONE);
  const r15 = Math.tan((15 * Math.PI) / 180) * 6;
  ramp('ramp15', 4, 10, 2, 5, CURB_H, CURB_H + r15, B, CONC);
  box('ramp15_top', 10, 16, 2, 5, B, CURB_H + r15, CONC);

  // --- Steep lane z in [6, 9]: sidewalk -> 55 deg slope (must NOT be climbable)
  box('sidewalk_r55', 0.15, 4, 6, 9, B, CURB_H, STONE);
  const r55 = Math.tan((55 * Math.PI) / 180) * 2;
  ramp('ramp55', 4, 6, 6, 9, CURB_H, CURB_H + r55, B, CONC);

  // --- Car lane z in [-9, -3]: curb -> sidewalk/plaza -> 8 deg driveway ramp -> deck
  box('plaza_car', 0.15, 12, -9, -3, B, CURB_H, STONE);
  const r8 = Math.tan((8 * Math.PI) / 180) * 14;
  ramp('ramp8_car', 12, 26, -9, -3, CURB_H, CURB_H + r8, B, CONC);
  box('deck_car', 26, 60, -9, -3, B, CURB_H + r8, CONC);

  // Fill the remaining gaps behind the curb so nobody falls through
  box('fill_a', 0.15, 20, -3, -1.5, B, CURB_H, STONE);
  box('fill_b', 0.15, 20, 1.5, 2, B, CURB_H, STONE);
  box('fill_c', 0.15, 20, 5, 6, B, CURB_H, STONE);
  box('fill_d', 0.15, 20, 9, Z1, B, CURB_H, STONE);
  box('fill_e', 0.15, 20, Z0, -9, B, CURB_H, STONE);

  return {
    positions: new Float32Array(pos),
    indices: new Uint32Array(idx),
    colors: new Float32Array(col),
    parts,
    platformY,
    ramp15TopY: CURB_H + r15,
    ramp8TopY: CURB_H + r8,
  };
}

// Big synthetic trimesh used for the collision-memory/perf benchmark:
// a grid "city terrain" with random 0..20 cm bumps, n x n quads, cellSize metres.
export function buildBigGrid(n, cell = 0.25) {
  const pos = new Float32Array((n + 1) * (n + 1) * 3);
  let p = 0;
  let seed = 1234567;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
  for (let j = 0; j <= n; j++)
    for (let i = 0; i <= n; i++) {
      pos[p++] = i * cell;
      pos[p++] = rnd() < 0.02 ? 0.12 : 0.0;
      pos[p++] = j * cell;
    }
  const idx = new Uint32Array(n * n * 6);
  let q = 0;
  for (let j = 0; j < n; j++)
    for (let i = 0; i < n; i++) {
      const a = j * (n + 1) + i, b = a + 1, c = a + (n + 1), d = c + 1;
      idx[q++] = a; idx[q++] = c; idx[q++] = b;
      idx[q++] = b; idx[q++] = c; idx[q++] = d;
    }
  return { positions: pos, indices: idx };
}
