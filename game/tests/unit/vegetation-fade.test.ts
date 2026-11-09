// Tree LOD cross-fades (world/vegetationLod.ts, world/nature/materials.ts): continuity without flipping (hysteresis),
// per-tree stagger, and the impostor atlas keeping its alpha-test coverage through the mips it is drawn at.
import { describe, expect, it } from 'vitest';
import * as THREE from 'three/webgpu';
import { LodFades, dissolveTwin, drawnCoverage, isDissolveTwin, precompileDissolve, stableMaterial } from '../../src/render/lodFade';
import { natureTextureMips } from './natureTextures';
import { CROWN_GAPS, VEGETATION_FADE, crownFill, crownGapCurve, crownGapCut, crownGapNoise, crownSunShare, impostorAlphaScale } from '../../src/world/nature/materials';
import { vegetationLevels } from '../../src/world/vegetationLod';
import { OPEN_STEP, UNMEASURED_OPENNESS, decodeOpenness, encodeOpenness, natureTint } from '../../src/world/nature/species';

const bands = [50, 180, 650] as const;
const smooth = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
/** CPU mirror of lodVariant's mask: the fade value at boundary j for a tree with stagger seed u in [0, 1). */
const fade = (j: number, d: number, u: number) => {
  const end = bands[j] * (1 - VEGETATION_FADE.stagger[j] * u);
  return smooth(end - 2 * bands[j] * VEGETATION_FADE.width[j], end, d);
};
const coverage = (k: number, d: number, u: number) => (k > 0 ? fade(k - 1, d, u) : 1) - fade(k, d, u);
/** CPU mirror of the shader's tint hash (float rounding differs slightly from the GPU's; the spread is what counts). */
const seed = (tint: number[]) => { const x = Math.sin(tint[0] * 12.9898 + tint[1] * 78.233 + tint[2] * 37.719) * 43758.5453; return x - Math.floor(x); };

describe('tree LOD cross-fade', () => {
  it('moves continuously with distance, so a slow climb or descent across a boundary never flips a tree', () => {
    for (const u of [0, 0.3, 0.7, 0.99]) {
      let previousMask = vegetationLevels(20, bands, 3), previous = [0, 1, 2].map(k => coverage(k, 20, u));
      // Back and forth through all three boundaries in 5 cm steps (a camera bobbing at 45 m included).
      for (let i = 1; i < 30000; i++) {
        const d = 20 + 680 * Math.abs(Math.sin(i / 3000)) + 0.4 * Math.sin(i);
        const now = [0, 1, 2].map(k => coverage(k, d, u)), mask = vegetationLevels(d, bands, 3);
        // Each level's share changes by no more than the fade's slope allows over the step: no jumps.
        for (let k = 0; k < 3; k++) expect(Math.abs(now[k] - previous[k])).toBeLessThan(0.12);
        // Draw membership changes only where that level shows nothing (the band edges).
        for (let k = 0; k < 3; k++) if ((mask ^ previousMask) & (1 << k)) expect(Math.min(now[k], previous[k])).toBeLessThan(1e-9);
        previous = now; previousMask = mask;
      }
    }
  });

  it('staggers trees: one tile at one distance is mid-fade over a spread, never switching all at once', () => {
    const seeds = Array.from({ length: 400 }, (_, i) => seed(natureTint(`osm:${i}`)));
    const deciles = new Array(10).fill(0);
    for (const s of seeds) deciles[Math.min(9, Math.floor(s * 10))]++;
    expect(Math.min(...deciles)).toBeGreaterThan(15); expect(Math.max(...deciles)).toBeLessThan(70);
    // The same tree always gets the same seed: its tint is a pure function of its id, so repacking never re-rolls it.
    expect(seed(natureTint('osm:7'))).toBe(seed(natureTint('osm:7')));
    // 400 trees at 42 m (mid-band): their near fades spread over a wide range instead of one shared value.
    const at = seeds.map(u => fade(0, 42, u)).sort((a, b) => a - b);
    expect(at[at.length - 1] - at[0]).toBeGreaterThan(0.3);
    // The band stays inside its boundary for every seed: full meshes never draw past 50 m.
    for (const u of seeds) expect(fade(0, 50, u)).toBe(1);
  });
});

/** The impostor atlas's alpha per mip. */
async function impostorMips() {
  return (await natureTextureMips('nature_impostor', 'baseColor')).map(({ rgba }) => {
    const alpha = new Float32Array(rgba.length / 4);
    for (let i = 0; i < alpha.length; i++) alpha[i] = rgba[i * 4 + 3] / 255;
    return alpha;
  });
}

describe('impostor alpha coverage', () => {
  it('keeps the atlas alpha-test coverage at its mip-0 value through the mips impostors are drawn at', async () => {
    const mips = await impostorMips();
    const coverage = (alpha: Float32Array, scale: number) => { let n = 0; for (const a of alpha) if (a * scale >= 0.5) n++; return n / alpha.length; };
    const base = coverage(mips[0], 1);
    // Impostors draw from the mid boundary (100-130 m) to the far one (650 m medium, 850 m high). A view is 128 texels
    // over the tree's height: a 20 m crown at 650 m is about 30 pixels at 1080p, mip 2; small crowns reach mip 3.
    // Through mip 5 the scaled coverage stays within 12 % of mip 0, so a crown keeps its density and silhouette.
    // (Mip 6 holds 2 x 2 texels per view: a crown under 2 pixels, kilometres past the far boundary.)
    for (let level = 0; level <= 5; level++) {
      const ratio = coverage(mips[level], impostorAlphaScale(level)) / base;
      expect(ratio).toBeGreaterThan(0.88); expect(ratio).toBeLessThan(1.12);
    }
    // The previous boost (1 + 0.3 per mip, from the chain-link fence) made crowns visibly denser from mip 2 on.
    expect(coverage(mips[2], 1 + 0.3 * 2) / base).toBeGreaterThan(1.15);
  }, 30000);
});

describe('settled frames keep no dither', () => {
  it('every dissolve reaches its end state within its duration: base materials, no coverage, shadows back', async () => {
    const fades = new LodFades();
    const roots = Array.from({ length: 6 }, (_, i) => {
      const root = new THREE.Group();
      for (let k = 0; k < 3; k++) {
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardNodeMaterial({ name: `m${i}-${k}`, alphaTest: k ? 0.5 : 0 }));
        mesh.castShadow = k !== 1; root.add(mesh);
      }
      return root;
    });
    await precompileDissolve(roots, [0], () => {});
    // A random schedule of starts, reversals, cancels and updates over 3 s, as cuts, blocks and tiles issue them.
    let now = 0, seed = 7;
    const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    let lastStart = 0;
    for (let step = 0; step < 300; step++) {
      now += 10;
      const root = roots[Math.floor(random() * roots.length)], action = random();
      if (action < 0.25) { fades.start(root, random() < 0.5 ? 0 : 1, random() < 0.5 ? 0 : 1, undefined, now, 0.6); lastStart = now; }
      else if (action < 0.3) fades.cancel(root);
      fades.update(now);
    }
    // No new starts: by the last start plus its 0.6 s, every dissolve has ended.
    fades.update(lastStart + 600);
    expect(fades.size).toBe(0);
    for (const root of roots) for (const mesh of root.children as THREE.Mesh[]) {
      expect(isDissolveTwin(mesh.material as THREE.Material)).toBe(false);
      expect(mesh.userData.lodCoverage).toBeUndefined();
      expect(drawnCoverage(mesh)).toBe(1);
    }
    expect(roots.flatMap(r => r.children as THREE.Mesh[]).map(m => m.castShadow)).toEqual(roots.flatMap(() => [true, false, true]));
  });

  it('a copy of a fading mesh (pool, batch, clone) draws whole: only a running dissolve has partial coverage', async () => {
    const base = new THREE.MeshStandardNodeMaterial({ name: 'leafy', alphaTest: 0.5 });
    const root = new THREE.Group(), mesh = new THREE.Mesh(new THREE.BoxGeometry(), base); root.add(mesh);
    await precompileDissolve([root], [0], () => {});
    const fades = new LodFades();
    fades.start(root, 0, 1, undefined, 0, 0.6); fades.update(300);
    expect(drawnCoverage(mesh)).toBeCloseTo(0.5, 5);
    // What a pool or batch did mid-fade: copied the twin and the userData.
    const copy = mesh.clone();
    expect(copy.material).toBe(dissolveTwin(base));
    expect(copy.userData.lodCoverage).toBeCloseTo(0.5, 5);
    expect(drawnCoverage(copy)).toBe(1);
    expect(stableMaterial(copy.material as THREE.Material)).toBe(base);
    fades.update(600);
    expect([mesh.material, drawnCoverage(mesh)]).toEqual([base, 1]);
  });
});

describe('crown sun gaps', () => {
  // The shadow proxies' leaves (NatureMaterials.crownShadow) keep a fragment where the noise at its sun ray's ground
  // point is at least the cut for the crown's openness (crownGapCurve, a cubic in the openness): a closed canopy lets
  // that share of the sun through; where crowns overlap along the ray the densest one decides (the gap is the stand's).
  const curve = (p: number) => { const [c0, c1, c2, c3] = crownGapCurve(), s = crownSunShare(p); return c0 + s * (c1 + s * (c2 + s * c3)); };
  const open = (opens: number[]) => {
    let lit = 0, n = 0;
    for (let i = 0; i < 120; i++) for (let j = 0; j < 120; j++, n++) {
      const x = 1000 + i * 0.53, z = -2000 + j * 0.47;
      if (opens.every(p => crownGapNoise(x, z) < curve(p))) lit++;
    }
    return lit / n;
  };
  it('opens each crown\'s measured share of a closed canopy away from the calibration grid', () => {
    for (const p of [0.03, 0.08, 0.13, UNMEASURED_OPENNESS, 0.3, 0.62]) expect(Math.abs(open([p]) - crownSunShare(p))).toBeLessThan(0.04);
    // Calibrated at Puolanpuisto (grove crowns 0.21, sunlit lawn 27.5 %); an unmeasured crown keeps the forests' 25 %.
    expect(crownSunShare(0.21)).toBeCloseTo(0.275, 2);
    expect(crownSunShare(UNMEASURED_OPENNESS)).toBeCloseTo(0.25, 2);
  });
  it('keeps the densest share where crowns overlap along the sun', () => {
    expect(open([0.21, 0.21, 0.21])).toBe(open([0.21]));
    expect(open([0.13, 0.4])).toBe(open([0.13]));
  });
  it('fills dense crowns and leaves open ones as the models draw them', () => {
    expect(crownFill(UNMEASURED_OPENNESS)).toBe(1);
    expect(crownFill(0.62)).toBe(1);
    expect(crownFill(CROWN_GAPS.dense)).toBe(CROWN_GAPS.fill);
    expect(crownFill(0.13)).toBeGreaterThan(1.5);
  });
  it('carries the openness in the tint below the 8-bit colour step', () => {
    for (const red of [0.7, 0.93, 1.0, 1.21]) for (const p of [0, 0.13, 0.25, 0.62, 1]) {
      const encoded = encodeOpenness(red, p);
      expect(Math.abs(encoded - red)).toBeLessThan(1 / OPEN_STEP);
      expect(decodeOpenness(Math.fround(encoded))).toBeCloseTo(p, 3);
    }
    const tint = natureTint('lidar:1:2', undefined, 0.13), plain = natureTint('lidar:1:2');
    expect(decodeOpenness(tint[0])).toBeCloseTo(0.13, 3); expect(decodeOpenness(plain[0])).toBeCloseTo(UNMEASURED_OPENNESS, 3);
    expect([tint[1], tint[2]]).toEqual([plain[1], plain[2]]);
    for (const untinted of [0, 0.5, 1]) expect(decodeOpenness(untinted)).toBe(UNMEASURED_OPENNESS);
  });
  it('makes flecks of about the sun-fleck scale, not single-texel speckle', () => {
    // Along a 50 m line the open/closed state changes about once per fleck, not per 10 cm sample.
    let changes = 0, last = crownGapNoise(0, 0) < crownGapCut();
    for (let k = 1; k <= 500; k++) { const now = crownGapNoise(k * 0.1, 3.3) < crownGapCut(); if (now !== last) changes++; last = now; }
    expect(changes).toBeGreaterThan(20);
    expect(changes).toBeLessThan(120);
  });
});
