import { expect, it } from 'vitest';
import * as THREE from 'three/webgpu';
import { CROWN_LIMITS, LIDAR_CROWNS, cards, crownFactor, crownMode, crownShape, crownWidening, fitCrown } from '../../src/world/nature/crowns';
import type { NatureAsset } from '../../src/world/nature/library';

const material = (name: string) => Object.assign(new THREE.MeshBasicMaterial(), { name });

/** A 20 m tree: a 0.4 m trunk to the top, and leaf vertices filling a crown from 6 m to 19 m out to 0.75 of a 5 m radius. */
function tree(species: string): NatureAsset {
  const leaves: number[] = [];
  for (let i = 0; i < 400; i++) {
    const a = i * 2.39996, y = 6 + 13 * ((i * 0.618034) % 1), r = 3.75 * Math.sqrt(((i * 0.414214) % 1));
    leaves.push(Math.cos(a) * r, y, Math.sin(a) * r);
  }
  const trunk: number[] = [];
  for (let y = 0; y <= 20; y += 2) for (let k = 0; k < 6; k++) trunk.push(0.4 * Math.cos(k), y, 0.4 * Math.sin(k));
  const geometry = (values: number[]) => new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(new Float32Array(values), 3));
  const leafGeometry = geometry(leaves), barkGeometry = geometry(trunk);
  const impostor = geometry([-5, 0, 0, 5, 0, 0, -5, 20, 0, 5, 20, 0]);
  const part = (g: THREE.BufferGeometry, name: string) => ({ geometry: g, material: material(name) as unknown as THREE.MeshStandardNodeMaterial, triangles: 0 });
  return { name: `tree_${species}`, kind: 'tree', species, height: 20, radius: 5, lods: [
    { parts: [part(barkGeometry, 'nature-bark'), part(leafGeometry, 'nature-leaves')], triangles: 0 },
    // The reduced level shares the full level's leaf attribute: it must be reshaped once.
    { parts: [part(new THREE.BufferGeometry().setAttribute('position', leafGeometry.getAttribute('position')), 'nature-leaves@L1')], triangles: 0 },
    { parts: [part(impostor, 'nature-impostor')], triangles: 0 },
  ] };
}

it('measures the model foliage and widens each fifth of the crown to the lidar profile', () => {
  const asset = tree('linden');
  const shape = crownShape(asset)!;
  expect(shape.base).toBeCloseTo(0.3, 1); expect(shape.top).toBeCloseTo(0.95, 1);
  for (const r of shape.profile) expect(r).toBeGreaterThan(0.6), expect(r).toBeLessThan(0.75);
  const widening = crownWidening(asset, shape);
  widening.forEach((k, i) => expect(k).toBeCloseTo(Math.min(CROWN_LIMITS.max, LIDAR_CROWNS.linden[i] / shape.profile[i]), 6));
  // Below the crown base and in the trunk core nothing moves; inside the crown the fifth's factor applies.
  expect(crownFactor(0.1, 0.5, shape, widening)).toBe(1);
  expect(crownFactor(0.6, 0.02, shape, widening)).toBe(1);
  expect(crownFactor(shape.base + (shape.top - shape.base) * 0.5, 0.5, shape, widening)).toBeCloseTo(widening[2], 6);
});

it('reshapes every LOD once, keeps the trunk under the crown and reports the new extent', () => {
  const asset = tree('maple');
  const shape = crownShape(asset)!, widening = crownWidening(asset, shape);
  const leaves = asset.lods[0].parts[1].geometry.getAttribute('position');
  const before = Array.from(leaves.array as Float32Array);
  const bark = Array.from(asset.lods[0].parts[0].geometry.getAttribute('position').array as Float32Array);
  const extent = fitCrown(asset)!;
  const after = leaves.array as Float32Array;
  for (let i = 0; i < leaves.count; i++) {
    const k = crownFactor(before[i * 3 + 1] / 20, Math.hypot(before[i * 3], before[i * 3 + 2]) / 5, shape, widening);
    expect(after[i * 3]).toBeCloseTo(before[i * 3] * k, 4);
    expect(after[i * 3 + 1]).toBe(before[i * 3 + 1]);
  }
  // The trunk (radius 0.4 m = 0.08 of the radius) keeps its thickness below the crown base.
  const trunk = asset.lods[0].parts[0].geometry.getAttribute('position').array as Float32Array;
  for (let i = 0; i < trunk.length; i += 3) if (trunk[i + 1] < 4) expect(trunk[i]).toBeCloseTo(bark[i], 6);
  // The impostor widens by the mean factor; the shared reduced-level attribute was not reshaped twice.
  const mean = widening.reduce((a, b) => a + b, 0) / 5;
  expect((asset.lods[2].parts[0].geometry.getAttribute('position').array as Float32Array)[3]).toBeCloseTo(5 * mean, 5);
  expect(extent).toBeGreaterThan(5); expect(extent).toBeLessThan(5 * CROWN_LIMITS.max);
  // The fitted foliage reaches the lidar's 90th-percentile radius per fifth.
  const fitted = crownShape(asset)!;
  fitted.profile.forEach((r, i) => expect(r).toBeCloseTo(LIDAR_CROWNS.maple[i], 1));
});

it('keeps conifer tapers and leaves shrubs and fences alone', () => {
  const spruce = tree('spruce');
  const widening = crownWidening(spruce, crownShape(spruce)!);
  expect(new Set(widening.map(k => k.toFixed(6))).size).toBe(1);
  expect(fitCrown({ ...tree('lilac'), kind: 'shrub' })).toBeNull();
});

it('spreads leaf cards at their own size on fill-rate bound tiers', () => {
  expect(crownMode('medium')).toBe('stretch'); expect(crownMode('high')).toBe('stretch');
  expect(crownMode('reduced')).toBe('spread'); expect(crownMode('low')).toBe('spread'); expect(crownMode('mobile')).toBe('spread');
  // Two leaf cards (quads) in the crown: each moves out with its centre and keeps its size.
  const quad = (x: number, y: number) => [x - 0.2, y, 0, x + 0.2, y, 0, x - 0.2, y + 0.4, 0, x + 0.2, y + 0.4, 0];
  const asset = tree('birch');
  const leaves = asset.lods[0].parts[1].geometry;
  const extra = new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(new Float32Array([...quad(2, 10), ...quad(-3, 14)]), 3));
  extra.setIndex([0, 1, 2, 2, 1, 3, 4, 5, 6, 6, 5, 7]);
  expect(Array.from(new Set(cards(extra)))).toHaveLength(2);
  asset.lods[0].parts.push({ geometry: extra, material: leaves.userData.material ?? asset.lods[0].parts[1].material, triangles: 4 });
  const shape = crownShape(asset)!, widening = crownWidening(asset, shape);
  fitCrown(asset, new WeakSet(), 'spread');
  const p = extra.getAttribute('position');
  expect(p.getX(1) - p.getX(0)).toBeCloseTo(0.4, 5);
  expect((p.getX(0) + p.getX(1)) / 2).toBeCloseTo(2 * crownFactor(10.2 / 20, 2 / 5, shape, widening), 4);
});
