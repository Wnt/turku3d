import * as THREE from 'three/webgpu';
import type { CarVisual } from '../actors/car';
import type { PhysicsClient } from '../physics/client';
import type { DamageHit } from '../vehicle/damage';
import { rigOf } from '../actors/fleet/damageRig';
import type { ParkedCarVisuals } from '../actors/fleet/visuals';
import { fleetDamageMaterial, fleetGlassDamageMaterial, fleetGlassMaterial, fleetMaterial } from '../actors/fleet/material';
import { PLAYER_COLOR, PLAYER_MODEL } from '../actors/fleet/catalog';
import { applyHit, DamageState, encodeDamage } from '../vehicle/damage';
import { mechanics, type Mechanics } from '../vehicle/mechanics';

const deg = (rad: number) => Math.round(rad * 18000 / Math.PI) / 100;
const levels = (m: Mechanics) => ({ cooling: m.cooling, engine: m.engine, steering: m.steering, suspension: m.suspension, zones: m.zones,
  power: Math.round(m.power * 1000) / 1000, steerDeg: deg(m.steer), toeDeg: m.toe.map(deg), steam: m.steam, smoke: m.smoke });

type Side = 'front' | 'rear' | 'left' | 'right' | 'roof' | 'front-left' | 'front-right' | 'rear-left' | 'rear-right';
/** Push directions in the fleet model frame (+Z forward, +X the car's left): the car is pushed away from the side hit. */
const PUSH: Record<Side, [number, number, number]> = {
  front: [0, 0, -1], rear: [0, 0, 1], left: [-1, 0, 0], right: [1, 0, 0], roof: [0, -1, 0],
  'front-left': [-Math.SQRT1_2, 0, -Math.SQRT1_2], 'front-right': [Math.SQRT1_2, 0, -Math.SQRT1_2],
  'rear-left': [-Math.SQRT1_2, 0, Math.SQRT1_2], 'rear-right': [Math.SQRT1_2, 0, Math.SQRT1_2],
};

/** `window.__game.damage`: the starter car's body damage (vehicle/README.md "Body damage"). `bounds()` and `state()`
 *  always; with developer tools (localhost, ?dev=1) also `dent()`, a hit as if the car struck a wall at `kmh`,
 *  `clear()`, a fresh body for measurements (the game itself has no repair), and `look()`, a camera held around the car
 *  for same-pose screenshots. `frame(camera)` runs before each render (play.ts). */
export function carDamageConsole(physics: PhysicsClient, car: CarVisual, dev: boolean, scene: THREE.Object3D, fleet?: ParkedCarVisuals) {
  const benched: THREE.Object3D[] = [];
  let view: { yaw: number; pitch: number; distance: number; height: number; remote: boolean } | null = null;
  const forward = new THREE.Vector3(), target = new THREE.Vector3();
  /** Other players' cars drawn here (net/remotes.ts, fleet/visuals.ts actors named remote/...), with their damage. */
  const remoteCars = () => {
    const cars: THREE.Mesh[] = [];
    scene.traverse(o => { if (o.name.startsWith('remote/') && (o as THREE.Mesh).isMesh && rigOf(o) && o.visible) cars.push(o as THREE.Mesh); });
    return cars;
  };
  const inspect = {
    /** Crumple from the drawn, deformed body against the fresh one (cm): length lost at each end, growth across. */
    bounds() {
      const b = car.bounds();
      if (!b) return null;
      const cm = (v: number) => Math.round(v * 1000) / 10;
      return { frontLoss: cm(b.fresh.max.z - b.deformed.max.z), rearLoss: cm(b.deformed.min.z - b.fresh.min.z),
        lengthLoss: cm((b.fresh.max.z - b.fresh.min.z) - (b.deformed.max.z - b.deformed.min.z)),
        widthGain: cm((b.deformed.max.x - b.deformed.min.x) - (b.fresh.max.x - b.fresh.min.x)),
        heightGain: cm((b.deformed.max.y - b.deformed.min.y) - (b.fresh.max.y - b.fresh.min.y)),
        deformed: [b.deformed.min.toArray(), b.deformed.max.toArray()], fresh: [b.fresh.min.toArray(), b.fresh.max.toArray()] };
    },
    /** The drawn damage state: hits, energy and the largest node offset (cm). */
    state() {
      const s = car.rig?.state;
      if (!s) return null;
      let max = 0;
      for (let i = 0; i < s.offsets.length; i += 3) max = Math.max(max, Math.hypot(s.offsets[i], s.offsets[i + 1], s.offsets[i + 2]));
      return { hits: s.hits, energy: Math.round(s.energy), revision: s.revision, maxOffsetCm: Math.round(max * 1000) / 10, bins: Array.from(s.bins, Math.round) };
    },
    /** Remote cars in view range with their drawn damage: hits and the crumple from their deformed bounds (cm). */
    remotes() {
      return remoteCars().map(mesh => {
        const rig = rigOf(mesh)!, geometry = mesh.geometry;
        if (!geometry.boundingBox) geometry.computeBoundingBox();
        const fresh = geometry.boundingBox!, drawn = rig.bounds(geometry), cm = (v: number) => Math.round(v * 1000) / 10;
        return { name: mesh.name, hits: rig.state.hits, slot: rig.slot, frontLoss: cm(fresh.max.z - drawn.max.z), rearLoss: cm(drawn.min.z - fresh.min.z),
          position: mesh.getWorldPosition(new THREE.Vector3()).toArray().map(v => Math.round(v * 10) / 10), mechanics: levels(mechanics(rig.state)) };
      });
    },
    /** The starter car's drawn mechanical state (vehicle/mechanics.ts): levels, power and the pull (degrees). */
    mechanics() { const s = car.rig?.state; return s ? levels(mechanics(s)) : null; },
    /** Engine-bay sprites alive in the shared smoke draw (vehicle/tyreEffects.ts): white steam and dark smoke. */
    smoke() {
      const mesh = scene.getObjectByName('tyre-smoke') as THREE.InstancedMesh | undefined;
      if (!mesh) return null;
      const opacity = mesh.geometry.getAttribute('tyreOpacity'), shade = mesh.geometry.getAttribute('tyreShade');
      let steam = 0, smoke = 0;
      for (let i = 0; i < opacity.count; i++) if (opacity.getX(i) > 0) { if (shade.getX(i) > 1) steam++; else if (shade.getX(i) < .5) smoke++; }
      return { steam, smoke };
    },
  };
  if (!dev) return { ...inspect, frame(_camera: THREE.PerspectiveCamera) {} };
  return { ...inspect,
    /** Hold the camera at `yaw` degrees around the car from its nose (0 front, 90 its left, 180 rear), `pitch` degrees
     *  up, `distance` metres from a point `height` m above the chassis centre; null hands the camera back. */
    look(next: { yaw?: number; pitch?: number; distance?: number; height?: number; remote?: boolean } | null) {
      view = next && { yaw: next.yaw ?? 35, pitch: next.pitch ?? 12, distance: next.distance ?? 6.5, height: next.height ?? .1, remote: !!next.remote };
    },
    frame(camera: THREE.PerspectiveCamera) {
      if (!view) return;
      // The starter car's chassis frame faces +X; a remote actor's mesh faces +Z (its +X is the car's left).
      const remote = view.remote ? remoteCars()[0] : null;
      if (view.remote ? !remote : !car.root.visible) return;
      if (remote) forward.set(0, 0, 1).applyQuaternion(remote.getWorldQuaternion(new THREE.Quaternion())).setY(0).normalize();
      else forward.set(1, 0, 0).applyQuaternion(car.root.quaternion).setY(0).normalize();
      const heading = Math.atan2(-forward.z, forward.x), yaw = heading + view.yaw * Math.PI / 180, pitch = view.pitch * Math.PI / 180;
      if (remote) target.copy(remote.getWorldPosition(new THREE.Vector3())).setY(remote.getWorldPosition(new THREE.Vector3()).y + .6 + view.height);
      else target.copy(car.root.position).setY(car.root.position.y + view.height);
      camera.position.set(target.x + Math.cos(yaw) * Math.cos(pitch) * view.distance, target.y + Math.sin(pitch) * view.distance,
        target.z - Math.sin(yaw) * Math.cos(pitch) * view.distance);
      camera.lookAt(target); camera.updateMatrixWorld();
    },
    /** "Dent here": the car hit `at` with a velocity change of `kmh`; `halfWidth` (m) narrows the footprint from a
     *  wall to e.g. a pole (0.15), `offset` (m) moves it along the face (+X / forward). */
    dent({ kmh = 50, at = 'front', halfWidth, offset = 0 }: { kmh?: number; at?: Side; halfWidth?: number; offset?: number } = {}) {
      const push = PUSH[at], v = kmh / 3.6;
      // The contact: on the side named, else `offset` along the face.
      const x = at.includes('left') ? 1 : at.includes('right') ? -1 : offset, z = at.startsWith('front') ? 2.5 : at.startsWith('rear') ? -2.5 : offset;
      const hit: DamageHit = { dv: [push[0] * v, push[1] * v, push[2] * v], at: [x, .6, z], ...(halfWidth ? { halfWidth } : {}) };
      physics.damageVehicle('player', hit);
      return hit;
    },
    clear() { physics.setVehicleDamage('player', null); },
    /** Frame-cost bench: `count` extra starter cars in rows in front of the starter car's nose, drawn as
     *  damage-capable actors with a heavy crumple (`kind` 'damaged'), undamaged ('capable'), or as the same actors
     *  (body, glazing, hubs) with the plain fleet materials and no cage ('plain'); count 0 removes them. Returns how
     *  many are placed. */
    bench({ count = 20, kind = 'damaged' }: { count?: number; kind?: 'damaged' | 'capable' | 'plain' } = {}) {
      for (const o of benched.splice(0)) o.removeFromParent();
      if (!count || !fleet || !car.root.visible) return 0;
      const sample = fleet.remoteModel(`${PLAYER_MODEL}/${PLAYER_COLOR}`);
      if (!sample) return 0;
      const state = new DamageState(rigOf(sample)!.layout);
      applyHit(state, { dv: [0, 0, -80 / 3.6] }); applyHit(state, { dv: [-.3 * 50 / 3.6, 0, .95 * 50 / 3.6], at: [.8, .6, -2] });
      const wire = encodeDamage(state);
      forward.set(1, 0, 0).applyQuaternion(car.root.quaternion).setY(0).normalize();
      const side = new THREE.Vector3(-forward.z, 0, forward.x), yaw = Math.atan2(forward.x, forward.z);
      for (let i = 0; i < count; i++) {
        const mesh = fleet.remoteModel(`${PLAYER_MODEL}/${PLAYER_COLOR}`)!;
        if (kind === 'plain') mesh.traverse(o => { const m = o as THREE.Mesh;
          if (m.material === fleetDamageMaterial()) m.material = fleetMaterial(); else if (m.material === fleetGlassDamageMaterial()) m.material = fleetGlassMaterial(); });
        mesh.name = `bench/${i}`;
        const row = Math.floor(i / 5), column = i % 5 - 2;
        mesh.position.copy(car.root.position).addScaledVector(forward, 7 + row * 5.5).addScaledVector(side, column * 2.6);
        mesh.position.y -= .75; mesh.rotation.y = yaw;
        scene.add(mesh); benched.push(mesh);
        if (kind === 'damaged') rigOf(mesh)!.show(wire);
      }
      return benched.length;
    },
  };
}
