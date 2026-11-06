import './fleet-assets.fixture';
import { expect, test } from 'vitest';
import * as THREE from 'three/webgpu';
import { playerRig } from '../fixtures/playerRig';
import { FLEET } from '../../src/actors/fleet/catalog';
import { BODY, crown, steerFraction, steeringAngle, type DriverLayout } from '../../src/actors/driver/layout';
import { Drivers, fleetDriverLayout } from '../../src/actors/driver/drivers';
import { DriverRig, DriverTemplate } from '../../src/actors/driver/rig';
import { TIERS } from '../../src/actors/driver/model';
import { ownWheelBox, withoutOwnWheel } from '../../src/actors/driver/ownWheel';
import { AmbientDrivers, bakePose } from '../../src/actors/driver/ambient';
import { loadAuthoredFleetParts } from '../../src/actors/fleet/authored';
import ANCHORS from '../../src/actors/driver/anchors.json';
import { DriverMotion, type MotionPose } from '../../src/actors/driver/motion';

const DEG = Math.PI / 180;
const still: MotionPose = { pitch: 0, roll: 0, bump: 0, headYaw: 0, headPitch: 0, reverse: 0, horn: 0, gLon: 0, gLat: 0 };

async function template() {
  const gltf = await playerRig();
  return new DriverTemplate(gltf.scene as unknown as THREE.Object3D, gltf.animations as unknown as THREE.AnimationClip[]);
}

test('the seated body constants match the shipped character in its Driving_Loop pose', async () => {
  const t = await template();
  const rig = new DriverRig(t), frame = new THREE.Group();
  frame.add(rig.model); frame.updateMatrixWorld(true);
  const at = (name: string) => rig.model.getObjectByName(name)!.getWorldPosition(new THREE.Vector3()).sub(t.hips);
  const close = (v: THREE.Vector3, expected: number[], tolerance: number) =>
    expected.forEach((e, i) => expect(Math.abs(v.getComponent(i) - e), `${v.toArray()} vs ${expected}`).toBeLessThan(tolerance));
  close(at('DEF-upper_armL'), BODY.shoulder, 0.03);
  close(at('DEF-spine001'), BODY.lumbar, 0.01);
  expect(at('DEF-forearmL').distanceTo(at('DEF-upper_armL'))).toBeCloseTo(BODY.upperArm, 2);
  expect(at('DEF-handL').distanceTo(at('DEF-forearmL'))).toBeCloseTo(BODY.forearm, 2);
  expect(Math.abs(at('DEF-head').y + 0.22 - BODY.crown[1])).toBeLessThan(0.02);
});

test('every fleet model seats its driver under the roof with both hands on the rim at rest and at full lock', async () => {
  const t = await template();
  const rig = new DriverRig(t), frame = new THREE.Group();
  frame.add(rig.model);
  const worst: { id: string; angle: number; error: number }[] = [];
  for (const model of FLEET) {
    const layout = fleetDriverLayout(model.id);
    expect(crown(layout.hips, layout.recline)[1], model.id).toBeLessThan(model.height - 0.05);
    expect(layout.hips[1], model.id).toBeGreaterThan(0.3);
    expect(Math.abs(layout.hips[2]), model.id).toBeLessThan(model.length / 2);
    for (const angle of [0, layout.wheel.lock / 2, -layout.wheel.lock / 2, layout.wheel.lock, -layout.wheel.lock]) {
      rig.pose(frame, layout, still, angle);
      worst.push({ id: model.id, angle: Math.round(angle / DEG), error: Math.max(rig.reach.left, rig.reach.right) });
    }
  }
  worst.sort((a, b) => b.error - a.error);
  console.log('worst wrist errors (m)', JSON.stringify(worst.slice(0, 6)));
  expect(worst[0].error).toBeLessThan(0.015);
});

test('the hands follow the rim as the wheel turns from the rest grips at 10 and 2; the right hand presses the horn', async () => {
  const t = await template();
  const rig = new DriverRig(t), frame = new THREE.Group();
  frame.add(rig.model);
  const layout = fleetDriverLayout('golf7');
  rig.pose(frame, layout, still, 0);
  const hand = (side: 'L' | 'R') => rig.model.getObjectByName(`DEF-hand${side}`)!.getWorldPosition(new THREE.Vector3());
  const c = new THREE.Vector3(...layout.wheel.centre);
  const left0 = hand('L'), right0 = hand('R');
  expect(left0.x).toBeGreaterThan(c.x + 0.1); expect(right0.x).toBeLessThan(c.x - 0.1);
  rig.pose(frame, layout, still, layout.wheel.lock);
  // Full lock left (95°): the outer (right) hand over the top at about 11 o'clock, the left one low on the left.
  expect(hand('R').x).toBeGreaterThan(c.x); expect(hand('R').y).toBeGreaterThan(hand('L').y + 0.15);
  expect(Math.max(rig.reach.left, rig.reach.right)).toBeLessThan(0.015);
  rig.pose(frame, layout, { ...still, horn: 1 }, 0);
  // The horn: the right hand on the hub, the left one stays on its grip.
  expect(hand('R').distanceTo(c)).toBeLessThan(0.13);
  expect(hand('L').distanceTo(left0)).toBeLessThan(0.005);
});

test('every driver is one instance of one draw: its seat-frame bones in its layer, its detail tier, culled one by one', async () => {
  const gltf = await playerRig(), drivers = new Drivers(), scene = new THREE.Scene();
  drivers.setCharacter(gltf.scene as unknown as THREE.Object3D, gltf.animations as unknown as THREE.AnimationClip[], undefined, scene);
  const batch = drivers.mesh!;
  expect(batch.parent).toBe(scene);
  const car = (x: number, z: number) => { const c = new THREE.Group(); c.position.set(x, 0, z); c.rotation.y = 0.4; scene.add(c); return c; };
  const near = car(0, 6), behind = car(0, -12), far = car(0, 90);
  const camera = new THREE.PerspectiveCamera(60, 1.6, 0.1, 500); camera.position.set(0, 2, 0); camera.lookAt(0, 1, 10); scene.add(camera);
  const layout = fleetDriverLayout('golf7'), input = { velocity: { x: 0, y: 0, z: 0 } };
  for (let frame = 0; frame < 3; frame++) {
    for (const [key, c] of [['near', near], ['behind', behind], ['far', far]] as const) drivers.seat(key, c, layout, input);
    drivers.finish(camera, frame * 16);
  }
  // Only the one in view within 60 m is drawn, at its seat with its distance's tier (6 m: the face too).
  expect(batch.count).toBe(1);
  const seat = new THREE.Matrix4(); batch.getMatrixAt(0, seat);
  seat.elements.forEach((e, i) => expect(e).toBeCloseTo(near.matrixWorld.elements[i], 6));
  expect((batch.geometry.getAttribute('driverTier') as THREE.BufferAttribute).count).toBe(batch.geometry.getAttribute('position').count);
  // Its layer's bones skin a vertex where its rig puts it: CPU skinning through the bone texture data.
  const snapshot = drivers.snapshot().drivers.find(d => d.key === 'near')!;
  expect(snapshot.drawn).toBe(true);
  const rig = new DriverRig(new DriverTemplate(gltf.scene as unknown as THREE.Object3D, gltf.animations as unknown as THREE.AnimationClip[])), local = new THREE.Group();
  local.add(rig.model);
  const bones = new Float32Array(t0Stride(rig)); rig.pose(local, layout, still, 0); rig.writeBones(bones);
  const geometry = rig.mesh.geometry, joints = geometry.getAttribute('skinIndex'), weights = geometry.getAttribute('skinWeight');
  const hand = geometry.index!.getX(100), p = new THREE.Vector3().fromBufferAttribute(geometry.getAttribute('position') as THREE.BufferAttribute, hand);
  const blended = new THREE.Vector3();
  for (let k = 0; k < 4; k++) blended.addScaledVector(p.clone().applyMatrix4(new THREE.Matrix4().fromArray(bones, joints.getComponent(hand, k) * 16)), weights.getComponent(hand, k));
  expect(blended.distanceTo(rig.mesh.applyBoneTransform(hand, p.clone()).applyMatrix4(rig.mesh.matrixWorld))).toBeLessThan(1e-5);
  expect(drivers.snapshot().drivers.filter(d => d.drawn).map(d => d.key).sort()).toEqual(['behind', 'near']);
});

const t0Stride = (rig: DriverRig) => rig.mesh.skeleton.bones.length * 16;

test('the merged driver: body, wheel, legs and face in index tiers; the wheel bone puts the rim on the layout and turns it', async () => {
  const t = await template();
  const rig = new DriverRig(t), frame = new THREE.Group();
  frame.add(rig.model);
  const meshes: THREE.Mesh[] = [];
  rig.model.traverse(o => { if ((o as THREE.Mesh).isMesh) meshes.push(o as THREE.Mesh); });
  expect(meshes).toEqual([rig.mesh]);
  const ends = t.model.tierEnds;
  expect(ends).toHaveLength(TIERS);
  for (let i = 1; i < TIERS; i++) expect(ends[i]).toBeGreaterThan(ends[i - 1]);
  expect(ends[TIERS - 1]).toBe(rig.mesh.geometry.index!.count);
  const tier = rig.mesh.geometry.getAttribute('driverTier');
  for (let k = 0; k < TIERS; k++) expect(tier.getX(rig.mesh.geometry.index!.getX(k ? ends[k - 1] : 0)), `tier ${k}`).toBe(k);
  // The wheel's vertices, skinned on the CPU, in the seat frame: on a ring of the rim's radius about the layout centre.
  const index = rig.mesh.geometry.index!, wheel = new Set<number>();
  for (let i = ends[0]; i < ends[1]; i++) wheel.add(index.getX(i));
  const skinned = (vertex: number) => {
    const v = new THREE.Vector3().fromBufferAttribute(rig.mesh.geometry.getAttribute('position') as THREE.BufferAttribute, vertex);
    return rig.mesh.applyBoneTransform(vertex, v).applyMatrix4(rig.mesh.matrixWorld);
  };
  const layout = fleetDriverLayout('corolla-e180'), c = new THREE.Vector3(...layout.wheel.centre);
  // A marker: the rim's 12 o'clock in the unturned wheel (the highest wheel vertex of the unit model).
  const y = rig.mesh.geometry.getAttribute('position');
  const marker = [...wheel].reduce((best, v) => (y.getY(v) > y.getY(best) ? v : best));
  const rim = (angle: number) => {
    rig.pose(frame, layout, { ...still, bump: 0.03 }, angle); frame.updateMatrixWorld(true);
    let far = 0;
    for (const v of wheel) far = Math.max(far, skinned(v).distanceTo(c));
    return { far, top: skinned(marker) };
  };
  const rest = rim(0);
  expect(rest.far).toBeGreaterThan(layout.wheel.radius); expect(rest.far).toBeLessThan(layout.wheel.radius + 0.03);
  expect(Math.abs(rest.top.x - c.x)).toBeLessThan(0.02); expect(rest.top.y - c.y).toBeGreaterThan(0.12);
  // A quarter turn left (counter-clockwise as the driver sees it): 12 o'clock moves to the driver's left (+X).
  const left = rim(Math.PI / 2);
  expect(left.top.x - c.x).toBeGreaterThan(0.15); expect(Math.abs(left.top.y - c.y)).toBeLessThan(0.05);
  // A vehicle that turns its own wheel (the trophy truck) collapses the drawn one.
  rig.pose(frame, { ...layout, wheel: { ...layout.wheel, drawn: false } }, still, 0); frame.updateMatrixWorld(true);
  const points = [...wheel].map(skinned);
  expect(Math.max(...points.map(p => p.distanceTo(points[0])))).toBeLessThan(1e-6);
});

test('steering wheel angle: linear in the share of the lock, ±95° for cars (GTA), ±130° vans, ±180° buses', () => {
  const car = fleetDriverLayout('golf7'), van = fleetDriverLayout('euro-van'), bus = fleetDriverLayout('transit-bus');
  const at = (layout: DriverLayout, steer: number) => steeringAngle(layout.wheel, steerFraction(layout, [{ steer }, { steer }])) / DEG;
  expect(at(car, car.steerLock / 2)).toBeCloseTo(47.5, 5);
  expect(at(car, -car.steerLock)).toBeCloseTo(-95, 5);
  expect(at(car, -2 * car.steerLock)).toBeCloseTo(-95, 5);
  expect(at(van, van.steerLock)).toBeCloseTo(130, 5);
  expect(at(bus, bus.steerLock)).toBeCloseTo(180, 5);
  // 4WS trucks measure the inner wheel.
  expect(steerFraction({ steerLock: 0.7, steerFrom: 'inner' }, [{ steer: 0.7 }, { steer: 0.4 }])).toBeCloseTo(1, 5);
});

// A car moving along +Z (fleet frame = world) with the given speed history.
function drive(motion: DriverMotion, speed: (t: number) => number, until: number, from = 0, steer = 0, lateral?: (t: number) => number) {
  let pose = motion.pose;
  for (let t = from; t <= until + 1e-9; t += 1 / 60) {
    pose = motion.update({ t, v: { x: lateral?.(t) ?? 0, y: 0, z: speed(t) }, fwd: { x: 0, y: 0, z: 1 }, left: { x: 1, y: 0, z: 0 }, up: { x: 0, y: 1, z: 0 }, steer });
  }
  return pose;
}

test('braking at 1 g leans the torso forward 5°, with the measured 0.18 s response and no whiplash', () => {
  const m = new DriverMotion();
  const brake = (t: number) => t < 1 ? 22 : Math.max(0, 22 - 9.81 * (t - 1));
  drive(m, brake, 1);
  const early = drive(m, brake, 1.1, 1 + 1 / 60).pitch;
  const late = drive(m, brake, 1.9, 1.1 + 1 / 60).pitch;
  expect(late / DEG).toBeCloseTo(5, 1);
  expect(early / DEG).toBeLessThan(2.5);
  expect(m.pose.headPitch).toBeLessThan(0.5 * DEG);
});

test('a 50 km/h frontal crash pitches the head forward with the whiplash envelope and returns within 0.8 s', () => {
  const m = new DriverMotion();
  const v0 = 50 / 3.6, crash = (t: number) => t < 1 ? v0 : t < 1.1 ? v0 * (1 - (t - 1) / 0.1) : 0;
  drive(m, crash, 1);
  let peak = 0, peakAt = 0;
  for (let t = 1 + 1 / 60; t < 1.5; t += 1 / 60) {
    const p = drive(m, crash, t, t);
    if (p.headPitch > peak) { peak = p.headPitch; peakAt = t; }
  }
  expect(peak / DEG).toBeGreaterThan(22);
  expect(peak / DEG).toBeLessThanOrEqual(30.01);
  expect(peakAt - 1).toBeLessThan(0.35);
  expect(m.pose.pitch / DEG).toBeGreaterThan(5);
  const after = drive(m, crash, 2.0, 1.5);
  expect(after.headPitch / DEG).toBeLessThan(2);
});

test('a landing dips the hips up to 4 cm on the 3 Hz seat spring', () => {
  const m = new DriverMotion();
  let min = 0;
  for (let t = 0; t < 1.5; t += 1 / 60) {
    // Level, then 0.3 s of free fall, then the landing stops the fall within one frame.
    const vy = t < 0.5 ? 0 : t < 0.8 ? -9.81 * (t - 0.5) : 0;
    const p = m.update({ t, v: { x: 0, y: vy, z: 20 }, fwd: { x: 0, y: 0, z: 1 }, left: { x: 1, y: 0, z: 0 }, up: { x: 0, y: 1, z: 0 }, steer: 0 });
    min = Math.min(min, p.bump);
  }
  expect(min).toBeLessThan(-0.025);
  expect(min).toBeGreaterThanOrEqual(-0.04);
  expect(Math.abs(m.pose.bump)).toBeLessThan(0.005);
});

test('head looks into a turn (GTA: 42° at full lock, even at rest) and over the right shoulder when reversing', () => {
  const m = new DriverMotion();
  let p = m.pose;
  // Turning left at 30°/s with 30 % of the lock.
  for (let t = 0, yaw = 0; t < 1.5; t += 1 / 60, yaw += 30 * DEG / 60) {
    p = m.update({ t, v: { x: 8 * Math.sin(yaw), y: 0, z: 8 * Math.cos(yaw) }, fwd: { x: Math.sin(yaw), y: 0, z: Math.cos(yaw) },
      left: { x: Math.cos(yaw), y: 0, z: -Math.sin(yaw) }, up: { x: 0, y: 1, z: 0 }, steer: 0.3 });
  }
  expect(p.headYaw / DEG).toBeCloseTo(42 * 0.3 + 0.05 * 30, 0);
  expect(drive(new DriverMotion(), () => 0, 1.5, 0, -1).headYaw / DEG).toBeCloseTo(-42, 0);
  const r = new DriverMotion();
  const reversing = drive(r, () => -2, 1.2);
  expect(reversing.reverse).toBeGreaterThan(0.95);
  expect(reversing.headYaw).toBe(0);
  const stopping = drive(r, () => 0, 1.4, 1.2 + 1 / 60);
  expect(stopping.reverse).toBeGreaterThan(0.2);
  expect(drive(r, () => 0, 1.7, 1.4 + 1 / 60).reverse).toBe(0);
});

test('the horn hand blends to the hub over 0.15 s and back', () => {
  const m = new DriverMotion(), sample = (t: number, horn: boolean) => m.update({ t, v: { x: 0, y: 0, z: 0 },
    fwd: { x: 0, y: 0, z: 1 }, left: { x: 1, y: 0, z: 0 }, up: { x: 0, y: 1, z: 0 }, steer: 0, horn });
  let t = 0;
  for (; t < 0.1; t += 1 / 60) sample(t, true);
  expect(m.pose.horn).toBeGreaterThan(0.3); expect(m.pose.horn).toBeLessThan(1);
  for (; t < 0.5; t += 1 / 60) sample(t, true);
  expect(m.pose.horn).toBe(1);
  for (; t < 0.7; t += 1 / 60) sample(t, false);
  expect(m.pose.horn).toBe(0);
});

test('posture classes (GTA grips and recline for cars, a sports preset for low cars)', () => {
  const classes = new Map(FLEET.map(m => [m.id, fleetDriverLayout(m.id).cls]));
  expect(classes.get('carla-ford-mustang')).toBe('sports'); expect(classes.get('corolla-e180')).toBe('saloon');
  expect(classes.get('carla-nissan-micra')).toBe('hatch'); expect(classes.get('prado-2010')).toBe('suv');
  expect(classes.get('carla-tesla-cybertruck')).toBe('pickup'); expect(classes.get('euro-van')).toBe('van');
  expect(classes.get('transit-bus')).toBe('bus'); expect(classes.get('garbage-truck')).toBe('truck');
  expect(fleetDriverLayout('corolla-e180').wheel.grip / DEG).toBeCloseTo(58, 6);
  expect(fleetDriverLayout('carla-ford-mustang').recline / DEG).toBeCloseTo(27, 6);
});

test('one steering wheel: a driven model with a measured wheel leaves its own static one out (whole parts in its box only)', async () => {
  for (const id of Object.keys(ANCHORS)) {
    const box = ownWheelBox(id)!, body = (await loadAuthoredFleetParts(id, 0))!.body;
    const layout = fleetDriverLayout(id), [lo, hi] = box;
    // The rig's wheel sits where the model's was.
    layout.wheel.centre.forEach((c, k) => { expect(c, id).toBeGreaterThan(lo[k]); expect(c, id).toBeLessThan(hi[k]); });
    const cut = withoutOwnWheel(body, box), before = body.index!.count / 3, after = cut.index!.count / 3;
    expect(cut.getAttribute('position'), id).toBe(body.getAttribute('position'));
    // A rim, spokes and hub go (at least 60 triangles even after decimation); nothing else: a thousandth of the body at most
    // beyond the triangles that lie in the box.
    const p = body.getAttribute('position'), kept = new Set<string>();
    for (let i = 0; i < cut.index!.count; i += 3) kept.add([0, 1, 2].map(k => cut.index!.getX(i + k)).join());
    let outside = 0;
    for (let i = 0; i < body.index!.count; i += 3) {
      const corners = [0, 1, 2].map(k => body.index!.getX(i + k));
      if (kept.has(corners.join())) continue;
      if (!corners.every(v => [p.getX(v), p.getY(v), p.getZ(v)].every((a, k) => a >= lo[k] - 0.016 && a <= hi[k] + 0.016))) outside++;
    }
    expect(before - after, id).toBeGreaterThan(60);
    expect(outside, id).toBe(0);
  }
  expect(ownWheelBox('golf7')).toBeNull();
}, 30000);

test('ambient traffic drivers: the rest pose baked per posture class, one instanced draw per class at each model\'s H-point', async () => {
  const t = await template(), corolla = fleetDriverLayout('corolla-e180'), sedan = fleetDriverLayout('generic-sedan'), van = fleetDriverLayout('euro-van');
  expect(corolla.cls).toBe(sedan.cls); expect(van.cls).not.toBe(corolla.cls);
  // The baked pose is the rig's skinned rest pose (steering straight), without the drawn wheel.
  const baked = bakePose(t, corolla), rig = new DriverRig(t), frame = new THREE.Group();
  frame.add(rig.model); rig.pose(frame, corolla, still, 0); frame.updateMatrixWorld(true);
  const first = t.model.geometry.index!.getX(0);
  const skinned = rig.mesh.applyBoneTransform(first, new THREE.Vector3().fromBufferAttribute(t.model.geometry.getAttribute('position') as THREE.BufferAttribute, first))
    .applyMatrix4(rig.mesh.matrixWorld);
  expect(new THREE.Vector3().fromBufferAttribute(baked.getAttribute('position') as THREE.BufferAttribute, 0).distanceTo(skinned)).toBeLessThan(1e-4);
  const ends = t.model.tierEnds;
  expect(baked.index!.count).toBe(ends[TIERS - 1] - (ends[1] - ends[0]));
  const parent = new THREE.Group(), ambient = new AmbientDrivers(t, parent, async () => {});
  const at = (x: number) => new THREE.Matrix4().makeTranslation(x, 0, 0);
  ambient.update([{ layout: corolla, matrix: at(0) }, { layout: sedan, matrix: at(10) }, { layout: van, matrix: at(20) }]);
  expect(ambient.snapshot()).toEqual({ [corolla.cls]: 2, [van.cls]: 1 });
  expect(parent.children).toHaveLength(2);
  const saloon = parent.children.find(o => o.name.endsWith(corolla.cls)) as THREE.InstancedMesh, m = new THREE.Matrix4();
  saloon.getMatrixAt(1, m);
  // The generic sedan's instance: the class pose moved from the corolla's H-point to its own.
  expect(new THREE.Vector3().setFromMatrixPosition(m).toArray().map(v => +v.toFixed(4)))
    .toEqual([10 + sedan.hips[0] - corolla.hips[0], sedan.hips[1] - corolla.hips[1], sedan.hips[2] - corolla.hips[2]].map(v => +v.toFixed(4)));
  // Fewer cars next frame: the rest hide; more than the capacity: the batch grows.
  ambient.update([{ layout: van, matrix: at(0) }]);
  expect(ambient.snapshot()).toEqual({ [corolla.cls]: 0, [van.cls]: 1 }); expect(saloon.visible).toBe(false);
  ambient.update(Array.from({ length: 20 }, (_, i) => ({ layout: corolla, matrix: at(i * 5) })));
  expect(ambient.snapshot()[corolla.cls]).toBe(20);
  expect((parent.children.find(o => o.name.endsWith(corolla.cls)) as THREE.InstancedMesh).count).toBe(20);
});
