// Hemi-octahedral tree impostors: the runtime (world/nature/impostor.ts) mirrors the bake's view layout
// (scripts/nature/octahedral.mjs), and nature.glb carries one quad per tree with its slot and the baked depth.
import { describe, expect, it } from 'vitest';
import { IMPOSTOR_BLOCKS, IMPOSTOR_FRAMES, IMPOSTOR_MARGIN } from '../../src/world/nature/impostor';
import { BLOCKS, FRAMES, MARGIN, atlasSize, hemiOctDecode, hemiOctEncode, viewFrame } from '../../scripts/nature/octahedral.mjs';
import { readFileSync } from 'node:fs';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptDecoder } from 'meshoptimizer';
import { natureGlb, natureTextureMips } from './natureTextures';

describe('octahedral impostor layout', () => {
  it('matches between the bake and the runtime', () => {
    expect([IMPOSTOR_FRAMES, IMPOSTOR_MARGIN, IMPOSTOR_BLOCKS]).toEqual([FRAMES, MARGIN, BLOCKS]);
  });

  it('round-trips view directions over the upper hemisphere and frames each view upright', () => {
    for (let i = 0; i < FRAMES; i++) for (let j = 0; j < FRAMES; j++) {
      const g = [i / (FRAMES - 1), j / (FRAMES - 1)], d = hemiOctDecode(g[0], g[1]) as [number, number, number];
      expect(Math.hypot(...d)).toBeCloseTo(1, 6); expect(d[1]).toBeGreaterThanOrEqual(-1e-9);
      const back = hemiOctEncode(d);
      expect(back[0]).toBeCloseTo(g[0], 6); expect(back[1]).toBeCloseTo(g[1], 6);
      const view = viewFrame(i, j, 5, 20);
      // An orthonormal frame with a horizontal right axis and an up axis that never points down.
      expect(view.right[1]).toBe(0); expect(view.up[1]).toBeGreaterThanOrEqual(-1e-9);
      const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
      expect(dot(view.right, view.toward)).toBeCloseTo(0, 9); expect(dot(view.up, view.toward)).toBeCloseTo(0, 9);
    }
    // The square's border is the horizon; its centre looks straight down.
    expect(hemiOctDecode(0, 0.5)[1]).toBeCloseTo(0, 9); expect(hemiOctDecode(0.5, 0.5)[1]).toBeCloseTo(1, 9);
    // Below the horizon a direction takes the horizon's view.
    expect(hemiOctEncode([0.6, -0.3, 0.8])).toEqual(hemiOctEncode([0.6, 0, 0.8]));
  });
});

describe('nature.glb impostors', () => {
  it('ends every tree with one quad carrying its bounding cylinder and its own atlas slot', async () => {
    await MeshoptDecoder.ready;
    const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder });
    const doc = await io.readBinary(new Uint8Array(readFileSync(new URL('../../public/assets/nature/nature.glb', import.meta.url))));
    const { json } = natureGlb(), heights = new Map<string, number>();
    for (const node of json.nodes) if (node.extras?.kind === 'tree') heights.set(node.extras.asset, 0);
    const slots = new Set<number>();
    for (const node of doc.getRoot().listNodes()) {
      const extras = node.getExtras() as { asset?: string; kind?: string; lod?: number };
      if (extras.kind !== 'tree' || extras.lod !== 2) continue;
      const primitives = node.getMesh()!.listPrimitives();
      expect(primitives).toHaveLength(1);
      expect(primitives[0].getMaterial()!.getName()).toBe('nature_impostor');
      // Position, corner and cylinder only: the vertex stage's 16 attributes also carry the instancing (impostor.ts).
      expect(primitives[0].listSemantics().sort()).toEqual(['POSITION', 'TEXCOORD_0', '_IMPOSTOR']);
      const info = primitives[0].getAttribute('_IMPOSTOR')!;
      expect(info.getCount()).toBe(4);
      const [R, top, slot] = info.getElement(0, []) as number[];
      // The cylinder holds the fitted crown: wider than a trunk, as tall as the model.
      expect(R).toBeGreaterThan(2); expect(top).toBeGreaterThan(8); expect(top).toBeLessThan(30);
      slots.add(slot);
    }
    expect([...slots].sort((a, b) => a - b)).toEqual([...Array(heights.size).keys()]);
  });

  it('bakes 8 x 8 views per tree with the depth towards the viewer surviving compression', async () => {
    const [albedo] = await natureTextureMips('nature_impostor', 'baseColor', 1);
    const [normal] = await natureTextureMips('nature_impostor', 'normal', 1);
    expect({ width: albedo.width, height: albedo.height }).toEqual(atlasSize(16));
    expect([normal.width * 2, normal.height * 2]).toEqual([albedo.width, albedo.height]);
    // Covered texels (albedo alpha over the cut) lie mostly on the viewer's side of the tree's middle: a view sees the
    // near half of a crown. Their depth spreads over the cylinder (not one flat value).
    let n = 0, sum = 0, sq = 0;
    for (let y = 0; y < normal.height; y++) for (let x = 0; x < normal.width; x++) {
      const a = albedo.rgba[((y * 2) * albedo.width + x * 2) * 4 + 3];
      if (a < 128) continue;
      const z = normal.rgba[(y * normal.width + x) * 4 + 3] / 255;
      n++; sum += z; sq += z * z;
    }
    const mean = sum / n, spread = Math.sqrt(sq / n - mean * mean);
    expect(mean).toBeGreaterThan(0.55); expect(mean).toBeLessThan(0.8);
    expect(spread).toBeGreaterThan(0.05);
  }, 30000);
});
