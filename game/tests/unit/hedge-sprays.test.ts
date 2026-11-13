import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { growHedges, growSprays, growTufts, type SprayParams, type TuftParams } from '../../src/world/location/families/sprays';

const PARAMS: SprayParams = {
  cell: [0.5, 0.75, 0.5, 0.75], tone: [0.95, 0], vary: [0.1, 0.035], dense: 1, share: 1, grow: 1,
  size: [[0.26, 0.36], [0.4, 0.58]], tilt: [[6, 20], [14, 40]], stand: [15, 60], lift: [0.03, 0.1], wind: 0.04,
};

/** A 10 m wall of a hedge (side, 1.5 m tall, density 20), its flat top (1 m wide, top) and an underside. */
function body() {
  const p = [0, 0, 0, 10, 0, 0, 0, 1.5, 0, 10, 1.5, 0, /* top */ 0, 1.5, 0, 10, 1.5, 0, 0, 1.5, -1, 10, 1.5, -1, /* under */ 0, 0, 0, 10, 0, 0, 0, 0, -1, 10, 0, -1];
  const n = [...Array(4)].flatMap(() => [0, 0, 1]).concat([...Array(4)].flatMap(() => [0, 1, 0]), [...Array(4)].flatMap(() => [0, -1, 0]));
  const spray = [...Array(4)].flatMap(() => [20, 1, 0]).concat([...Array(4)].flatMap(() => [28, 1, 1]), [...Array(4)].flatMap(() => [20, 1, -1]));
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(p, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(n, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(new Array(36).fill(0.8), 3));
  geometry.setAttribute('_spray', new THREE.Float32BufferAttribute(spray, 3));
  geometry.setIndex([0, 1, 2, 1, 3, 2, 4, 6, 5, 5, 6, 7, 8, 9, 10, 9, 11, 10]);
  return geometry;
}

test('a hedge body grows its sprays by area and place, the same at every load', () => {
  const cards = growSprays(body(), PARAMS)!;
  const count = cards.getIndex()!.count / 6;
  // 15 m² of side at 20 and 10 m² of top at 28: about 580 sprays; none on the underside.
  expect(count).toBeGreaterThan(520);
  expect(count).toBeLessThan(640);
  const y = cards.getAttribute('position');
  let low = Infinity;
  for (let k = 0; k < y.count; k++) low = Math.min(low, y.getY(k));
  expect(low).toBeGreaterThan(-0.4);
  expect(Array.from(growSprays(body(), PARAMS)!.getAttribute('position').array)).toEqual(Array.from(y.array));
  const far = growSprays(body(), { ...PARAMS, share: 0.15, grow: 2.3 })!;
  expect(far.getIndex()!.count / 6).toBeLessThan(count * 0.2);
  for (const name of ['uv', 'color', '_wind', 'normal']) expect(cards.getAttribute(name).count).toBe(y.count);
});

test('a loaded model gets a spray mesh beside each hedge body, drawn in the foliage family', () => {
  const material = new THREE.MeshStandardMaterial();
  material.userData = { turku: { family: 'hedge', params: { sprays: PARAMS } } };
  const scene = new THREE.Group();
  scene.add(new THREE.Mesh(body(), material));
  expect(growHedges(scene)).toBeGreaterThan(500);
  const leaves = scene.children[1] as THREE.Mesh;
  expect((leaves.material as THREE.Material).userData.turku.family).toBe('foliage');
});

test('unmown ground grows its plants by area and is not drawn itself', () => {
  const cell = [0, 0.25, 0, 0.25] as [number, number, number, number];
  const params: TuftParams = { share: 1, grow: 1, vary: [0.1, 0.035], plants: [
    [{ cell, density: 3.2, across: [0.35, 0.65], tone: [0.8, 0.03] }, { cell, density: 0.5, across: [0.7, 1.1], tone: [0.9, 0.06] }],
    [{ cell, density: 1.2, across: [0.3, 0.5], tone: [0.68, 0] }, { cell, density: 0.2, across: [0.45, 0.8], tone: [0.78, 0] }]] };
  const ground = new THREE.BufferGeometry();
  ground.setAttribute('position', new THREE.Float32BufferAttribute([0, 5, 0, 10, 5, 0, 0, 5, 10, 10, 5, 10], 3));
  ground.setAttribute('_tuft', new THREE.Float32BufferAttribute([0, 0, 0, 0], 1));
  ground.setIndex([0, 2, 1, 1, 2, 3]);
  const cards = growTufts(ground.clone(), params)!;
  expect(cards.getIndex()!.count / 6).toBeGreaterThan(330);
  expect(cards.getIndex()!.count / 6).toBeLessThan(410);
  const material = new THREE.MeshStandardMaterial();
  material.userData = { turku: { family: 'tufts', params: { tufts: params } } };
  const scene = new THREE.Group();
  scene.add(new THREE.Mesh(ground, material));
  expect(growHedges(scene)).toBeGreaterThan(330);
  expect(scene.children.map(child => child.name)).toEqual(['sprays']);
});
