import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import * as R from '@dimforge/rapier3d';
import fs from 'node:fs';
import { TyreEffects } from '../../src/vehicle/tyreEffects';
import { longitudinalSlip, acceptsTyreMarks, tyreStrength } from '../../src/vehicle/tyreSlip';
import { PhysicsWorld } from '../../src/physics/world';
import { GameplayAudio } from '../../src/audio/gameplay';
import type { AudioEvent } from '../../src/audio/mix';
import type { WheelState } from '../../src/physics/protocol';
import { MAT } from '../../src/shared/col/materials';

const q = { x: 0, y: 0, z: 0, w: 1 }, pos = { x: 0, y: 1, z: 0 };
function wheel(material: number = MAT.road): WheelState {
  return { pos: { x: -1, y: -0.6, z: 0.8 }, contact: true, suspension: 0.3, steer: 0, rotation: 0,
    tyre: { point: { x: -1, y: -1, z: 0.8 }, normal: { x: 0, y: 1, z: 0 }, material, lateral: 6, longitudinal: 0 } };
}
const meshes = (fx: TyreEffects) => fx.root.children as THREE.Mesh[];
function advance(fx: TyreEffects, x: number, w: WheelState, dt = 1 / 30, slot = 0) {
  fx.begin(dt); fx.vehicle(slot, { ...pos, x }, q, [w], 'car', dt); fx.end(new THREE.PerspectiveCamera());
}

test('shared slip signal rejects soft surfaces, airborne wheels, rest and malformed remote data', () => {
  for (const material of [MAT.road, MAT.sidewalk, MAT.paving_slab, MAT.cobblestone, MAT.bridge_deck]) {
    expect(acceptsTyreMarks(material)).toBe(true); expect(tyreStrength(wheel(material))).toBeGreaterThan(0);
  }
  for (const material of [MAT.unknown, MAT.grass, MAT.gravel, MAT.soil, MAT.sand, MAT.water, MAT.building]) {
    expect(tyreStrength(wheel(material))).toBe(0);
  }
  const w = wheel(); w.contact = false; expect(tyreStrength(w)).toBe(0);
  w.contact = true; w.tyre!.lateral = 0; expect(tyreStrength(w)).toBe(0);
  w.tyre!.longitudinal = 8; expect(tyreStrength(w)).toBe(1);
  w.tyre!.point.y = NaN; expect(tyreStrength(w)).toBe(0);
  expect(longitudinalSlip(12, 100, 30)).toBeCloseTo(8.4);
  expect(longitudinalSlip(12, 20, 30)).toBe(0);
  expect(longitudinalSlip(0, 100, 30)).toBe(0);
});

test('strips follow contact slopes and break across grass, lost contact and teleports', () => {
  const fx = new TyreEffects(new THREE.Group(), 'low'), w = wheel();
  w.tyre!.normal = { x: -Math.SQRT1_2, y: Math.SQRT1_2, z: 0 };
  advance(fx, 0, w); w.tyre!.point.y += 0.3; advance(fx, 0.3, w);
  const geometry = meshes(fx)[0].geometry;
  const strength = geometry.getAttribute('tyreStrength'), p = geometry.getAttribute('position');
  expect(strength.getX(0)).toBeGreaterThan(0);
  expect(p.getY(0)).toBeCloseTo(0.018 * Math.SQRT1_2);
  expect(p.getY(2)).toBeCloseTo(0.3 + 0.018 * Math.SQRT1_2);
  // Two edges share the previous endpoint, instead of separate tyre-stamp squares.
  advance(fx, 0.6, w);
  expect(p.getX(4)).toBe(p.getX(2)); expect(p.getZ(4)).toBe(p.getZ(2));
  w.tyre!.material = MAT.grass; advance(fx, 0.9, w);
  w.tyre!.material = MAT.road; advance(fx, 1.2, w);
  expect(strength.getX(8)).toBe(0);
  advance(fx, 10, w); expect(strength.getX(8)).toBe(0);
  advance(fx, 10.3, w); expect(strength.getX(8)).toBeGreaterThan(0);
  w.contact = false; advance(fx, 10.6, w);
  w.contact = true; advance(fx, 10.9, w); expect(strength.getX(12)).toBe(0);
});

test('effect buffers and shared materials stay fixed while rings recycle; quality scales smoke', () => {
  const low = new TyreEffects(new THREE.Group(), 'low'), high = new TyreEffects(new THREE.Group(), 'high');
  const lowMeshes = meshes(low), geometry = lowMeshes.map(m => m.geometry), materials = lowMeshes.map(m => m.material);
  for (let i = 0; i < 14; i++) { advance(low, i * 0.2, wheel()); advance(high, i * 0.2, wheel()); }
  const live = (fx: TyreEffects) => Array.from(meshes(fx)[1].geometry.getAttribute('tyreOpacity').array).filter(v => v > 0).length;
  expect(live(high)).toBeGreaterThan(live(low));
  const capacity = geometry[0].getAttribute('position').count;
  for (let i = 14; i < 4300; i++) advance(low, i * 0.2, wheel());
  expect(meshes(low).map(m => m.geometry)).toEqual(geometry);
  expect(meshes(low).map(m => m.material)).toEqual(materials);
  expect(geometry[0].getAttribute('position').count).toBe(capacity);
  expect((meshes(low)[1] as THREE.InstancedMesh).count).toBe(384);
  expect(geometry[0].getAttribute('tyreOrder').getX(0)).toBeGreaterThan(4096);
  low.begin(4); low.end(new THREE.PerspectiveCamera()); expect(live(low)).toBe(0);
  const slots = Array.from({ length: 32 }, () => low.reserve());
  expect(new Set(slots).size).toBe(32); expect(low.reserve()).toBe(-1);
  low.release(slots[0]); expect(low.reserve()).toBe(slots[0]);
});

test('worker resolves actual wheel material and contact, measures rear handbrake and lateral slip, clears airborne', async () => {
  const pw = new PhysicsWorld(R);
  try {
    const bytes = fs.readFileSync(new URL('../../public/dev-tiles/collision/t0_0_0/c_2_3.col', import.meta.url));
    await pw.loadTile('road', bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
    pw.spawnVehicle({ x: 174, y: 2.8, z: 243 }, 0);
    const idle = { vx: 0, vz: 0, jump: false }, coast = { throttle: 0, brake: 0, steer: 0, handbrake: false };
    for (let i = 0; i < 90; i++) pw.fixedStep(idle, coast);
    expect(pw.vehicleState()!.wheels.every(w => tyreStrength(w) === 0)).toBe(true);
    pw.vehicle!.body.setLinvel({ x: 12, y: 0, z: 4 }, true);
    pw.fixedStep(idle, { ...coast, handbrake: true });
    const state = pw.vehicleState()!;
    expect(state.wheels.every(w => w.tyre?.material === MAT.road)).toBe(true);
    expect(state.wheels[2].tyre!.longitudinal).toBeGreaterThan(5);
    expect(Math.abs(state.wheels[2].tyre!.lateral)).toBeGreaterThan(3);
    expect(state.wheels[0].tyre!.longitudinal).toBe(0);
    const contact = new THREE.Vector3().copy(state.wheels[2].tyre!.point).applyQuaternion(new THREE.Quaternion().copy(state.rot)).add(state.pos);
    expect(contact.y).toBeCloseTo(2, 2);
    pw.resetVehicle({ x: 174, y: 20, z: 243 }, 0);
    pw.fixedStep(idle, { ...coast, handbrake: true });
    expect(pw.vehicleState()!.wheels.every(w => !w.contact && !w.tyre)).toBe(true);
  } finally { pw.world.free(); }
});

test('screech audio uses exactly the same contact/material slip strength as the effects', () => {
  const events: AudioEvent[] = [], audio = new GameplayAudio(event => events.push(event)), w = wheel();
  const frame = { tick: 1, time: 1, paused: false, mode: 'car' as const, connected: false, character: null,
    vehicle: { pos, rot: q, linvel: { x: 10, y: 0, z: 5 }, speed: 11, forwardSpeed: 10, wheels: [w], curbStrike: null },
    input: { throttle: 1, steer: 1, brake: 0, handbrake: true }, position: pos,
    forward: { x: 1, y: 0, z: 0 }, up: { x: 0, y: 1, z: 0 } };
  audio.update(frame);
  expect(events.findLast(e => e.type === 'car')).toMatchObject({ slip: tyreStrength(w) });
  w.tyre!.material = MAT.grass; audio.update(frame);
  expect(events.findLast(e => e.type === 'car')).toMatchObject({ slip: 0 });
});
