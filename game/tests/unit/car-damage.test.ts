import './fleet-assets.fixture';
import { describe, expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { applyHit, DamageState, damageLayout, decodeDamage, encodeDamage, headOnLoss, nodeIndex, validDamageWire, DAMAGE_NODES } from '../../src/vehicle/damage';
import { fleetDamageBox, FLEET_BY_ID, PLAYER_MODEL } from '../../src/actors/fleet/catalog';
import { loadAuthoredFleetParts } from '../../src/actors/fleet/authored';
import { cageWeights, DamageRig, deformedBounds, modelLayout, skinToCage } from '../../src/actors/fleet/damageRig';

const golf = FLEET_BY_ID.get(PLAYER_MODEL)!;
const layout = damageLayout(fleetDamageBox(golf));
// Wreckfest 2 numbers (damage-study.md) are for a 4.42 m car; the crumple scales with the car's length.
const scale = golf.length / 4.42, cm = (m: number) => m * 100;

describe('crumple model against the Wreckfest 2 table', () => {
  test('single head-on hits: none at walking pace, ~20 cm at 50-60 km/h, 33 at 70, 42 at 80', () => {
    expect(cm(headOnLoss(layout, 30))).toBeLessThan(3); // the soft bumper layer only
    expect(cm(headOnLoss(layout, 15))).toBeLessThan(.01);
    for (const [kmh, wreckfest, tolerance] of [[52, 21, 5], [61.5, 24, 5], [71.5, 33.3, 3], [81, 42, 3]] as const)
      expect(Math.abs(cm(headOnLoss(layout, kmh)) - wreckfest * scale), `${kmh} km/h`).toBeLessThan(tolerance);
  });
  test('repeat hits saturate at the hard stop: six at 61.5 km/h go 28 → 46 cm, eight at 42.5 about equal one at 70', () => {
    const six = [1, 2, 3, 4, 5, 6].map(n => cm(headOnLoss(layout, 61.5, n)));
    [28, 32, 38, 42, 44, 46].forEach((wreckfest, i) => expect(Math.abs(six[i] - wreckfest * scale), `hit ${i + 1}`).toBeLessThan(5));
    for (let i = 1; i < six.length; i++) expect(six[i] - six[i - 1]).toBeLessThan(i === 1 ? 10 : 6);
    expect(cm(headOnLoss(layout, 61.5, 20))).toBeLessThanOrEqual(cm(.105 * golf.length + .03) + .01);
    expect(Math.abs(cm(headOnLoss(layout, 42.5, 8)) - cm(headOnLoss(layout, 70)))).toBeLessThan(5);
  });
  test('the cabin stays nearly rigid and the cage never folds through itself', () => {
    const state = new DamageState(layout);
    for (let i = 0; i < 12; i++) applyHit(state, { dv: [0, 0, -85 / 3.6] });
    for (let i = 0; i < 12; i++) applyHit(state, { dv: [0, 0, 85 / 3.6] });
    const cabin = [3, 4, 5];
    for (const s of cabin) for (const h of [0, 1]) for (const l of [0, 1, 2]) {
      const i = nodeIndex(s, h, l) * 3;
      expect(Math.hypot(state.offsets[i], state.offsets[i + 1], state.offsets[i + 2]), `station ${s}`).toBeLessThan(.016);
    }
    // Stations keep their order along the length after the worst crumple at both ends.
    for (const h of [0, 1]) for (const l of [0, 1, 2]) for (let s = 1; s < layout.stations.length; s++) {
      const z = (k: number) => layout.stations[k] + state.offsets[nodeIndex(k, h, l) * 3 + 2];
      expect(z(s) - z(s - 1), `stations ${s - 1}-${s}`).toBeGreaterThan(.02);
    }
  });
  test('a pole dents deeper under it than beside it; a side hit dents the doors, not the far side', () => {
    const pole = new DamageState(layout);
    applyHit(pole, { dv: [0, 0, -60 / 3.6], at: [0, .6, 2], halfWidth: .15 });
    const front = (l: number) => -pole.offsets[nodeIndex(8, 0, l) * 3 + 2];
    expect(front(1)).toBeGreaterThan(front(0) * 1.5);
    const side = new DamageState(layout);
    applyHit(side, { dv: [-50 / 3.6, 0, 0], at: [1, .6, 0] }); // pushed towards -X: the +X side was hit
    const x = (l: number) => side.offsets[nodeIndex(4, 1, l) * 3];
    expect(x(2)).toBeLessThan(-.08);
    expect(Math.abs(x(0))).toBeLessThan(.01);
  });
  test('light hits leave no shape and count no hit; a landing from below does nothing', () => {
    const state = new DamageState(layout);
    expect(applyHit(state, { dv: [0, 0, -10 / 3.6] })).toBe(0);
    expect(applyHit(state, { dv: [0, 60 / 3.6, 0] })).toBe(0);
    expect(state.hits).toBe(0);
    expect(state.offsets.every(v => v === 0)).toBe(true);
  });
});

describe('compact state', () => {
  test('round-trips offsets (5 mm), bins, hits and energy; rejects malformed input', () => {
    const state = new DamageState(layout);
    applyHit(state, { dv: [-20 / 3.6, 0, -70 / 3.6] });
    applyHit(state, { dv: [0, 0, 55 / 3.6], halfWidth: .2, at: [-.5, .5, -2] });
    const wire = encodeDamage(state), copy = new DamageState(layout);
    expect(wire.length).toBeLessThan(300);
    expect(decodeDamage(wire, copy)).toBe(true);
    expect(copy.hits).toBe(2);
    expect(Math.abs(copy.energy - state.energy)).toBeLessThan(64);
    for (let i = 0; i < DAMAGE_NODES * 3; i++) expect(Math.abs(copy.offsets[i] - state.offsets[i])).toBeLessThanOrEqual(.0025 + 1e-6);
    for (let i = 0; i < state.bins.length; i++) expect(Math.abs(copy.bins[i] - state.bins[i])).toBeLessThanOrEqual(4);
    expect(encodeDamage(copy)).toBe(wire);
    expect(validDamageWire(wire)).toBe(true);
    for (const bad of [null, 3, '', 'not base64!', wire.slice(4), `${wire}AAAA`]) expect(validDamageWire(bad), String(bad)).toBe(false);
  });
});

describe('cage skinning of the real starter car', () => {
  test('weights: at most four nodes summing to one, exact for an affine offset field', () => {
    const index = [0, 0, 0, 0], weight = [0, 0, 0, 0];
    for (const [x, y, z] of [[0, .5, 0], [.3, 1.1, 1.9], [-.9, .1, -2.1], [.757, .997, 1.53]]) {
      cageWeights(layout, x, y, z, index, weight);
      expect(weight.reduce((a, b) => a + b)).toBeCloseTo(1, 6);
      expect(weight.every(w => w >= -1e-9)).toBe(true);
      // An affine field u(p) = A p is reproduced inside the cage.
      const u = (i: number) => [layout.nodes[i * 3] * .1, layout.nodes[i * 3 + 2] * -.05, layout.nodes[i * 3 + 1] * .2];
      const blend = [0, 1, 2].map(k => index.reduce((s, n, j) => s + weight[j] * u(n)[k], 0));
      const inside = (v: number, a: ArrayLike<number>) => Math.max(a[0], Math.min(a[a.length - 1], v));
      const p = [inside(x, layout.laterals), inside(y, layout.levels), inside(z, layout.stations)];
      expect(blend[0]).toBeCloseTo(p[0] * .1, 5); expect(blend[1]).toBeCloseTo(p[2] * -.05, 5); expect(blend[2]).toBeCloseTo(p[1] * .2, 5);
    }
  });
  test('an 80 km/h wall hit shortens the drawn Golf by the model depth; the cabin and the rear stay put', async () => {
    const parts = (await loadAuthoredFleetParts(PLAYER_MODEL, 0))!;
    const cage = modelLayout(golf);
    skinToCage(parts.body, cage);
    const skinIndex = parts.body.getAttribute('skinIndex'), skinWeight = parts.body.getAttribute('skinWeight');
    expect(skinIndex.count).toBe(parts.body.getAttribute('position').count);
    for (let i = 0; i < skinWeight.count; i += 97) {
      expect([0, 1, 2, 3].reduce((s, k) => s + skinWeight.getComponent(i, k), 0)).toBeCloseTo(1, 5);
      for (let k = 0; k < 4; k++) expect(skinIndex.getComponent(i, k)).toBeLessThan(DAMAGE_NODES);
    }
    const mesh = new THREE.SkinnedMesh(parts.body, new THREE.MeshBasicMaterial());
    const rig = new DamageRig(cage);
    rig.bind(mesh);
    rig.pose();
    const fresh = deformedBounds(mesh);
    expect(fresh.max.z).toBeCloseTo(parts.body.boundingBox!.max.z, 4);
    const state = new DamageState(cage);
    applyHit(state, { dv: [0, 0, -80 / 3.6] });
    rig.show(encodeDamage(state));
    const hit = deformedBounds(mesh);
    const loss = fresh.max.z - hit.max.z;
    expect(Math.abs(loss - headOnLoss(cage, 80))).toBeLessThan(.03);
    expect(Math.abs(hit.min.z - fresh.min.z)).toBeLessThan(.005);
    // Bulge: the crushed front grows a little across and up (Wreckfest 2: 1-3 cm).
    expect(hit.max.x - hit.min.x - (fresh.max.x - fresh.min.x)).toBeGreaterThanOrEqual(-.005);
    // Cabin vertices (between the A and C pillars) move under 1.5 cm.
    const position = parts.body.getAttribute('position'), v = new THREE.Vector3();
    let cabin = 0;
    for (let i = 0; i < position.count; i++) {
      v.fromBufferAttribute(position, i);
      if (Math.abs(v.z) > .6) continue;
      const moved = mesh.applyBoneTransform(i, v.clone()).distanceTo(v);
      cabin = Math.max(cabin, moved);
    }
    expect(cabin).toBeLessThan(.015);
    rig.show(undefined);
    expect(deformedBounds(mesh).max.z).toBeCloseTo(fresh.max.z, 5);
  }, 30_000);
});

describe('the physics worker owns the damage state', () => {
  test('a dent reaches the published state, also of a sleeping car, and decodes to the same shape', async () => {
    const R = await import('@dimforge/rapier3d');
    const { PhysicsWorld } = await import('../../src/physics/world');
    const { modelDamageBox, modelTuning } = await import('../../src/vehicle/modelTuning');
    const pw = new PhysicsWorld(R);
    pw.world.createCollider(R.ColliderDesc.cuboid(50, .5, 50).setTranslation(0, -.5, 0));
    pw.spawnVehicle({ x: 0, y: .8, z: 0 }, 0, { tuning: modelTuning(PLAYER_MODEL), damage: { box: modelDamageBox(PLAYER_MODEL)! } });
    const idle = { vx: 0, vz: 0, jump: false };
    for (let i = 0; i < 240; i++) pw.fixedStep(idle, null);
    expect(pw.vehicleStates()[0].damage).toBeUndefined();
    const sleeping = pw.vehicle!.body.isSleeping();
    pw.damageVehicle('player', { dv: [0, 0, -70 / 3.6] });
    const wire = pw.vehicleStates()[0].damage!;
    expect(typeof wire).toBe('string');
    expect(pw.vehicle!.body.isSleeping()).toBe(sleeping);
    const drawn = new DamageState(layout);
    expect(decodeDamage(wire, drawn)).toBe(true);
    expect(cm(-drawn.offsets[nodeIndex(8, 0, 1) * 3 + 2])).toBeCloseTo(cm(headOnLoss(layout, 70)), 0);
    // A respawn of the same car keeps its damage; a car without a cage ignores dents.
    pw.spawnVehicle({ x: 3, y: .8, z: 0 }, 0, { tuning: modelTuning(PLAYER_MODEL) });
    expect(pw.vehicleStates()[0].damage).toBe(wire);
    pw.spawnVehicle({ x: 10, y: .8, z: 0 }, 0, { id: 'plain' });
    pw.damageVehicle('plain', { dv: [0, 0, -70 / 3.6] });
    expect(pw.vehicleStates().find(v => v.id === 'plain')!.damage).toBeUndefined();
    pw.damageVehicle('player', null);
    expect(pw.vehicleStates()[0].damage).toBeUndefined();
    pw.world.free();
  });
});
