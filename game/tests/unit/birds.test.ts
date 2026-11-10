import { expect, test } from 'vitest';
import { BIRD_STRIDE, DRAWN_SIZE, SPECIES, activity, birdConditions, groupAt, groupsNear, sampleBirds, type BirdView, type Group } from '../../src/world/birds/flocks';

// Engine metres (x east, z south): the riverside by the office, and four blocks inland.
const QUAY: BirdView = { x: 250, y: 4, z: 300, ground: 2 }, INLAND: BirdView = { x: 900, y: 12, z: -2600, ground: 10 };
const DAY = birdConditions(35, 0), NIGHT = birdConditions(-20, 0), DUSK = birdConditions(2, 0);
const T = 1_794_300_000;
const index = (name: string) => SPECIES.findIndex(s => s.name === name);
function sample(view: BirdView, t: number, conditions = DAY, max = 2000, city = 'turku') {
  const out = new Float32Array(max * BIRD_STRIDE), groups = groupsNear(view, new Map(), city);
  return { out, groups, n: sampleBirds(groups, view, t, conditions, out, max) };
}
const only = (groups: Group[], name: string) => groups.filter(g => g.species === index(name));

test('the same second shows the same birds to everyone, and they move like birds', () => {
  const a = sample(QUAY, T), b = sample(QUAY, T);
  expect(a.n).toBeGreaterThan(10); expect(a.n).toBeLessThan(384);
  expect(Array.from(a.out.subarray(0, a.n * BIRD_STRIDE))).toEqual(Array.from(b.out.subarray(0, b.n * BIRD_STRIDE)));
  for (let i = 0; i < a.n; i++) {
    const o = i * BIRD_STRIDE, [x, y, z, fx, fy, fz, bank, span, phase, beat] = a.out.subarray(o, o + 10);
    expect(Math.hypot(fx, fy, fz)).toBeCloseTo(1, 4); expect(Math.abs(fy)).toBeLessThan(0.5); expect(Math.abs(bank)).toBeLessThanOrEqual(1.1001);
    expect(y - QUAY.ground).toBeGreaterThan(2); expect(span).toBeGreaterThan(0); expect(span).toBeLessThan(2.6 * DRAWN_SIZE);
    expect(phase).toBeGreaterThanOrEqual(0); expect(phase).toBeLessThan(6.2832); expect(beat).toBeGreaterThanOrEqual(0);
    expect(Math.hypot(x - QUAY.x, y - QUAY.y, z - QUAY.z)).toBeLessThan(1500);
  }
});

test('each species flies at about its own air speed, on loops that turn smoothly', () => {
  for (let s = 0; s < SPECIES.length; s++) {
    const species = SPECIES[s];
    let group: Group | null = null;
    for (let ix = -40; ix < 40 && !group; ix++) for (let iz = -40; iz < 40 && !group; iz++) group = groupAt(s, ix, iz, 'espoo');
    expect(group, species.name).not.toBeNull();
    const view: BirdView = { x: group!.x, y: 60, z: group!.z, ground: 0 }, out = new Float32Array(64 * BIRD_STRIDE), next = new Float32Array(64 * BIRD_STRIDE);
    // A line's birds are near the anchor once per period: find such a second.
    let t = T, n = 0;
    for (let k = 0; k < 400 && n === 0; k++) n = sampleBirds([group!], view, ++t, DAY, out, 64);
    expect(n, species.name).toBeGreaterThan(0);
    expect(sampleBirds([group!], view, t + 0.5, DAY, next, 64), species.name).toBe(n);
    const speed = Math.hypot(next[0] - out[0], next[1] - out[1], next[2] - out[2]) / 0.5;
    expect(speed, species.name).toBeGreaterThan(species.speed * 0.4); expect(speed, species.name).toBeLessThan(species.speed * 1.9);
  }
});

test('gulls gather over the river; night grounds every bird and dusk gathers the jackdaws', () => {
  const quay = only(groupsNear(QUAY, new Map(), 'turku'), 'herring gull'), inland = only(groupsNear(INLAND, new Map(), 'turku'), 'herring gull');
  expect(quay.length).toBeGreaterThan(inland.length * 2); expect(quay.length).toBeGreaterThanOrEqual(3);
  // On the quay a gull is always close: several within 150 m, over the water and below the roofs.
  const near = sample(QUAY, T), close: number[] = [];
  for (let i = 0; i < near.n; i++) { const o = i * BIRD_STRIDE; if (near.out[o + 11] > 0.8 && Math.hypot(near.out[o] - QUAY.x, near.out[o + 2] - QUAY.z) < 150) close.push(near.out[o + 1] - QUAY.ground); }
  expect(close.length).toBeGreaterThanOrEqual(4); expect(Math.max(...close)).toBeLessThan(32);
  expect(sample(QUAY, T, NIGHT).n).toBe(0);
  const jackdaw = SPECIES[index('jackdaw')];
  expect(activity(jackdaw, DUSK)).toBeGreaterThan(activity(jackdaw, DAY) + 0.3); expect(activity(jackdaw, NIGHT)).toBe(0);
  // Rain grounds the swifts and thins the rest.
  const wet = birdConditions(35, 1);
  expect(activity(SPECIES[index('common swift')], wet)).toBeLessThan(0.15); expect(sample(QUAY, T, wet).n).toBeLessThan(sample(QUAY, T).n);
});

test('geese fly in a V behind their leader, and the sample never exceeds its budget', () => {
  const s = index('barnacle goose');
  let group: Group | null = null;
  for (let ix = -30; ix < 30 && !group; ix++) for (let iz = -30; iz < 30 && !group; iz++) { const g = groupAt(s, ix, iz, 'turku'); if (g && g.members.length >= 9) group = g; }
  expect(group).not.toBeNull();
  const view: BirdView = { x: group!.x, y: 100, z: group!.z, ground: 0 }, out = new Float32Array(64 * BIRD_STRIDE);
  let t = T, n = 0;
  for (let k = 0; k < 400 && n < group!.members.length; k++) n = sampleBirds([group!], view, ++t, DAY, out, 64);
  expect(n).toBe(group!.members.length);
  // Along the heading every bird is at or behind the leader, and the two arms open to both sides.
  const f = [out[3], out[5]], sides = new Set<number>();
  for (let i = 1; i < n; i++) {
    const dx = out[i * BIRD_STRIDE] - out[0], dz = out[i * BIRD_STRIDE + 2] - out[2];
    expect(dx * f[0] + dz * f[1]).toBeLessThan(1.5); sides.add(Math.sign(dx * f[1] - dz * f[0]));
    expect(Math.hypot(dx, dz)).toBeLessThan(40);
  }
  expect(sides.size).toBe(2);
  expect(sample(QUAY, T, DAY, 5).n).toBe(5);
});
