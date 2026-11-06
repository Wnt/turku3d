import * as THREE from 'three/webgpu';
import type { CarVisual } from '../actors/car';
import type { PhysicsClient } from '../physics/client';
import type { DamageHit } from '../vehicle/damage';
import { rigOf } from '../actors/fleet/damageRig';
import type { ParkedCarVisuals } from '../actors/fleet/visuals';
import { fleetDamageMaterial, fleetGlassDamageMaterial, fleetGlassMaterial, fleetMaterial } from '../actors/fleet/material';
import { PLAYER_COLOR, PLAYER_MODEL } from '../actors/fleet/catalog';
import { applyHit, DamageState, encodeDamage, PARTS, WINDOWS } from '../vehicle/damage';
import type { DebrisField } from '../actors/fleet/debris';
import type { GlassShards } from '../actors/fleet/glassShards';
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
/** Part states by name (vehicle/damage.ts PARTS): 0 on, 1 loose, 2 off. */
const partsOf = (s: DamageState) => Object.fromEntries(PARTS.map((name, i) => [name, s.parts[i]]));
/** Glass stages by window (vehicle/damage.ts WINDOWS): 0 intact, 1 cracked, 2 crazed, 3 shattered. */
const glassOf = (s: DamageState) => Object.fromEntries(WINDOWS.map((name, i) => [name, s.glass[i]]));

export function carDamageConsole(physics: PhysicsClient, car: CarVisual, dev: boolean, scene: THREE.Object3D, fleet?: ParkedCarVisuals, debris?: DebrisField,
  shards?: GlassShards) {
  const benched: THREE.Object3D[] = [], benchWrecks: THREE.Object3D[] = [];
  let benchWreck: string | null = null;
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
      return { hits: s.hits, energy: Math.round(s.energy), revision: s.revision, maxOffsetCm: Math.round(max * 1000) / 10, bins: Array.from(s.bins, Math.round),
        parts: partsOf(s), partLoad: Array.from(s.partLoad, Math.round), glass: glassOf(s), glassLoad: Array.from(s.glassLoad, Math.round) };
    },
    /** Remote cars in view range with their drawn damage: hits and the crumple from their deformed bounds (cm). */
    remotes() {
      return remoteCars().map(mesh => {
        const rig = rigOf(mesh)!, geometry = mesh.geometry;
        if (!geometry.boundingBox) geometry.computeBoundingBox();
        const fresh = geometry.boundingBox!, drawn = rig.bounds(geometry), cm = (v: number) => Math.round(v * 1000) / 10;
        return { name: mesh.name, hits: rig.state.hits, slot: rig.slot, frontLoss: cm(fresh.max.z - drawn.max.z), rearLoss: cm(drawn.min.z - fresh.min.z),
          position: mesh.getWorldPosition(new THREE.Vector3()).toArray().map(v => Math.round(v * 10) / 10), mechanics: levels(mechanics(rig.state)),
          parts: partsOf(rig.state), glass: glassOf(rig.state),
          surface: { worn: rig.state.surfaceWear.filter(v => v > .05).length, maxWear: Math.round(Math.max(...rig.state.surfaceWear) * 100) / 100 } };
      });
    },
    /** Car part debris lying around now (actors/fleet/debris.ts). */
    debris() { return debris?.list() ?? []; },
    /** Glass shards alive now (flying and lying; actors/fleet/glassShards.ts). */
    shards() { return shards?.alive ?? 0; },
    /** The parts the starter car's body was split into (actors/fleet/damageParts.ts): triangles and rest bounds. */
    split() {
      return (car.rig?.modelParts ?? []).map((p, i) => p ? { part: PARTS[i], triangles: p.triangles.length / 3,
        min: p.min.toArray().map(v => Math.round(v * 100) / 100), max: p.max.toArray().map(v => Math.round(v * 100) / 100) } : { part: PARTS[i], triangles: 0 });
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
      // Bench wrecks are not in any vehicle list: their rigs follow their (unchanged) state here, swinging loose parts.
      for (const mesh of benchWrecks) rigOf(mesh)!.show(benchWreck);
      if (!view) return;
      // The starter car's chassis frame faces +X; a remote actor's mesh faces +Z (its +X is the car's left).
      // The most damaged remote car (a room keeps other players' parked starter cars too).
      const remote = view.remote ? remoteCars().reduce<THREE.Mesh | null>((a, b) => !a || rigOf(b)!.state.hits > rigOf(a)!.state.hits ? b : a, null) : null;
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
    /** A scrape along the car as a wall or the ground would leave it: `side` 'left' | 'right' | 'roof' | 'front' |
     *  'rear', `metres` of sliding, along the middle band of that face from `from` to `to` (fractions of the length
     *  from the rear, or of the width for the ends); `ground` leaves dirt as a roll would. */
    scrape({ side = 'left', metres = 8, from = .25, to = .8, ground = false }: { side?: 'left' | 'right' | 'roof' | 'front' | 'rear'; metres?: number; from?: number; to?: number; ground?: boolean } = {}) {
      const l = car.rig?.layout;
      if (!l) return null;
      const face = ({ left: 'posX', right: 'negX', roof: 'top', front: 'front', rear: 'rear' } as const)[side];
      const along = (f: number) => l.min[2] + f * l.length, across = (f: number) => l.min[0] + f * l.width, y = l.min[1] + .45 * l.height;
      const ends = face === 'posX' || face === 'negX' ? [[face === 'posX' ? l.max[0] : l.min[0], y, along(from)], [face === 'posX' ? l.max[0] : l.min[0], y, along(to)]]
        : face === 'top' ? [[across(.5), l.max[1], along(from)], [across(.5), l.max[1], along(to)]]
          : [[across(from), l.min[1] + .3 * l.height, face === 'front' ? l.max[2] : l.min[2]], [across(to), l.min[1] + .3 * l.height, face === 'front' ? l.max[2] : l.min[2]]];
      const scrape = { face, from: ends[0] as [number, number, number], to: ends[1] as [number, number, number], metres, ground };
      physics.scrapeVehicle('player', scrape);
      return scrape;
    },
    /** The starter car's surface wear: worn and dirty cells, and the most worn and dirtiest. */
    surface() {
      const s = car.rig?.state;
      if (!s) return null;
      const round = (v: number) => Math.round(v * 100) / 100;
      return { worn: s.surfaceWear.filter(v => v > .05).length, dirty: s.surfaceDirt.filter(v => v > .05).length,
        maxWear: round(Math.max(...s.surfaceWear)), maxDirt: round(Math.max(...s.surfaceDirt)) };
    },
    /** Frame-cost bench: `count` extra starter cars in rows in front of the starter car's nose, drawn as
     *  damage-capable actors with a heavy crumple (`kind` 'damaged'), undamaged ('capable'), or as the same actors
     *  (body, glazing, hubs) with the plain fleet materials and no cage ('plain'); count 0 removes them. Returns how
     *  many are placed. */
    bench({ count = 20, kind = 'damaged' }: { count?: number; kind?: 'damaged' | 'wrecked' | 'capable' | 'plain' } = {}) {
      for (const o of benched.splice(0)) o.removeFromParent();
      benchWrecks.length = 0;
      if (!count || !fleet || !car.root.visible) return 0;
      const sample = fleet.remoteModel(`${PLAYER_MODEL}/${PLAYER_COLOR}`);
      if (!sample) return 0;
      const state = new DamageState(rigOf(sample)!.layout);
      applyHit(state, { dv: [0, 0, -80 / 3.6] }); applyHit(state, { dv: [-.3 * 50 / 3.6, 0, .95 * 50 / 3.6], at: [.8, .6, -2] });
      const wire = encodeDamage(state);
      // 'wrecked': the same crumple with both bumpers, the bonnet and the boot lid hanging loose and the left mirror off.
      const wreck = new DamageState(state.layout);
      applyHit(wreck, { dv: [0, 0, -80 / 3.6] }); applyHit(wreck, { dv: [-.3 * 50 / 3.6, 0, .95 * 50 / 3.6], at: [.8, .6, -2] });
      wreck.parts.set([1, 1, 1, 1, 2, 0]); wreck.partSide.set([1, -1, 1, 1, 1, 1]);
      // Cracked, crazed and shattered windows, and worn, scratched and dirty paint all round.
      wreck.glass.set([1, 2, 3, 1, 0, 2]);
      wreck.surfaceWear.forEach((_, i) => { wreck.surfaceWear[i] = (i * 37 % 16) / 16; wreck.surfaceDirt[i] = (i * 11 % 16) / 24; });
      const wrecked = encodeDamage(wreck);
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
        // First shown already wrecked: parts that are off are just gone (no debris), as for a car coming into view.
        if (kind === 'wrecked') { benchWreck = wrecked; rigOf(mesh)!.show(wrecked); benchWrecks.push(mesh); }
      }
      return benched.length;
    },
  };
}
