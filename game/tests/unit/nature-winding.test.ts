import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptDecoder } from 'meshoptimizer';
import { meshTree, SPECIES } from '../../scripts/nature/trees.mjs';

/** Twice the face area, signed by agreement with the three outward vertex normals. */
function faceAlignment(positions: ArrayLike<number>, normals: ArrayLike<number>, a: number, b: number, c: number) {
  a *= 3; b *= 3; c *= 3;
  const ux = positions[b] - positions[a], uy = positions[b + 1] - positions[a + 1], uz = positions[b + 2] - positions[a + 2];
  const vx = positions[c] - positions[a], vy = positions[c + 1] - positions[a + 1], vz = positions[c + 2] - positions[a + 2];
  return (uy * vz - uz * vy) * (normals[a] + normals[b] + normals[c])
    + (uz * vx - ux * vz) * (normals[a + 1] + normals[b + 1] + normals[c + 1])
    + (ux * vy - uy * vx) * (normals[a + 2] + normals[b + 2] + normals[c + 2]);
}

it.each([0, 1])('generated straight bark tubes face outward at LOD %s', lod => {
  const skeleton = {
    crown: { yc: 5, hc: 4, R: 2, y0: 1 }, anchors: [], conifer: false,
    branches: [{ level: 0, length: 10, phase: 0,
      pts: [0, 5, 10].map(y => ({ p: [0, y, 0], r: 0.5, sway: 0 })) }],
  };
  const { bark } = meshTree(skeleton, SPECIES.birch, lod, 123);
  expect(bark.indices.length).toBeGreaterThan(0);
  for (let i = 0; i < bark.positions.length; i += 3) {
    // The cylinder is centred on +Y with radius 0.5: shading normals must point away from that axis.
    expect(bark.positions[i] * bark.normals[i] + bark.positions[i + 2] * bark.normals[i + 2]).toBeCloseTo(0.5, 10);
  }
  for (let i = 0; i < bark.indices.length; i += 3) {
    expect(faceAlignment(bark.positions, bark.normals, ...bark.indices.slice(i, i + 3) as [number, number, number]), `triangle ${i / 3}`)
      .toBeGreaterThan(0);
  }
});

it('committed bark surfaces face their outward shading normals', async () => {
  await MeshoptDecoder.ready;
  const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder });
  const doc = await io.readBinary(new Uint8Array(readFileSync(new URL('../../public/assets/nature/nature.glb', import.meta.url))));
  let checked = 0;
  for (const mesh of doc.getRoot().listMeshes()) for (const primitive of mesh.listPrimitives()) {
    if (primitive.getMaterial()?.getName() !== 'nature_bark') continue;
    const positions = primitive.getAttribute('POSITION')!.getArray()!;
    const normals = primitive.getAttribute('NORMAL')!.getArray()!;
    const indices = primitive.getIndices()!.getArray()!;
    expect(indices.length).toBeGreaterThan(0);
    expect(indices.length % 3).toBe(0);
    for (let i = 0; i < normals.length; i += 3) {
      expect([normals[i], normals[i + 1], normals[i + 2]].every(Number.isFinite)).toBe(true);
      expect(Math.hypot(normals[i], normals[i + 1], normals[i + 2])).toBeGreaterThan(0);
    }
    let alignment = 0;
    for (let i = 0; i < indices.length; i += 3) alignment += faceAlignment(positions, normals, indices[i], indices[i + 1], indices[i + 2]);
    // Curved branch rings can fold locally; the whole bark surface must retain its exterior winding.
    expect(alignment, mesh.getName()).toBeGreaterThan(0);
    checked++;
  }
  expect(checked).toBeGreaterThan(0);
});
