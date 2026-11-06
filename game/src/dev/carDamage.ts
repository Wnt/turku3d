import * as THREE from 'three/webgpu';
import type { CarVisual } from '../actors/car';
import type { PhysicsClient } from '../physics/client';
import type { DamageHit } from '../vehicle/damage';

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
export function carDamageConsole(physics: PhysicsClient, car: CarVisual, dev: boolean) {
  let view: { yaw: number; pitch: number; distance: number; height: number } | null = null;
  const forward = new THREE.Vector3(), target = new THREE.Vector3();
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
  };
  if (!dev) return { ...inspect, frame(_camera: THREE.PerspectiveCamera) {} };
  return { ...inspect,
    /** Hold the camera at `yaw` degrees around the car from its nose (0 front, 90 its left, 180 rear), `pitch` degrees
     *  up, `distance` metres from a point `height` m above the chassis centre; null hands the camera back. */
    look(next: { yaw?: number; pitch?: number; distance?: number; height?: number } | null) {
      view = next && { yaw: next.yaw ?? 35, pitch: next.pitch ?? 12, distance: next.distance ?? 6.5, height: next.height ?? .1 };
    },
    frame(camera: THREE.PerspectiveCamera) {
      if (!view || !car.root.visible) return;
      // The chassis frame faces +X; yaw turns from the nose towards the car's left (+Z of the chassis, the fleet +X).
      forward.set(1, 0, 0).applyQuaternion(car.root.quaternion).setY(0).normalize();
      const heading = Math.atan2(-forward.z, forward.x), yaw = heading + view.yaw * Math.PI / 180, pitch = view.pitch * Math.PI / 180;
      target.copy(car.root.position).setY(car.root.position.y + view.height);
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
    clear() { physics.damageVehicle('player', null); },
  };
}
