import { applyFleetWheels } from '../actors/fleet/wheels';
import { showFleetDamage } from '../actors/fleet/visuals';
import { vehicleLamps } from '../vehicle/lamps';
import type { HornEmitter } from '../audio/mix';
import type { TyreEffects } from '../vehicle/tyreEffects';
import { mechanicsOf } from '../vehicle/mechanics';
import * as THREE from 'three/webgpu';
import { clone as cloneSkeleton } from 'three/addons/utils/SkeletonUtils.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { collisionTileAt } from '../core/origin';
import type { V3 } from '../core/origin';
import type { RemoteBody, VehicleState } from '../physics/protocol';
import { VEHICLES } from '../physics/constants';
import { chassisForward } from '../traffic/pedestrians';
import type { WalkerThreat } from '../world/walkers';
import type { PhysicsClient } from '../physics/client';
import type { PlayerVisual } from '../actors/player';
import type { ParkedCarVisuals } from '../actors/car';
import { PLAYER_COLOR, PLAYER_MODEL } from '../actors/fleet/catalog';
import type { ScooterVisual } from '../actors/scooter';
import type { TruckVisual } from '../actors/truck';
import { applyTrophyRig } from '../actors/trophy4400/rig';
import { TruckSoundFeed } from '../audio/engine/feed';
import type { EngineVoiceState } from '../audio/engine/voices';
import type { MultiplayerClient, NetPlayer, NetPose } from './client';
import { vehicleRotation } from './orientation';
import { vehiclePose } from './sharedVehicles';
import type { SharedVehicles } from './sharedVehicles';
import { PoseBuffer } from './interpolation';
import { remoteRootHeight, VEHICLE_POSE_HEIGHT } from './height';
import { fleetDriverLayout, type Drivers } from '../actors/driver/drivers';

interface Remote {
  player: NetPlayer & { occupant?: string | null }; buffer: PoseBuffer; foot: THREE.Object3D | null; distant: THREE.Mesh;
  car: THREE.Object3D; scooter: THREE.Object3D; truck: THREE.Object3D; vehicleKey: string;
  pizza: THREE.Mesh; label: THREE.Sprite;  lastT: number; effectSlot: number; receivedAt: number;
  /** The remote truck's engine voice input, stepped once per frame from its synced wheels and `drv`. */
  engine: { feed: TruckSoundFeed; at: number } | null;
}

const FULL_MODEL_DISTANCE = 6;

/** The local player's own name is never drawn in the world: not on foot, nor over a vehicle they occupy or own. */
export function showsNameTag(p: { id: string; nick: string; occupant?: string | null }, selfId: string | undefined): boolean {
  return !!p.nick && (!selfId || (p.id !== selfId && p.occupant !== selfId));
}

const TAG_HIDDEN_WITHIN = 4, TAG_FULL_FROM = 12, TAG_CENTRE_RANGE = 40;
const TAG_CENTRE_CLEAR = Math.cos(THREE.MathUtils.degToRad(3)), TAG_CENTRE_FULL = Math.cos(THREE.MathUtils.degToRad(8));
const smooth = (edge0: number, edge1: number, x: number) => { const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0))); return t * t * (3 - 2 * t); };

/** Opacity and scale factor for a remote name tag: faded out close to the camera and off the screen centre,
 *  grown with distance so it stays readable far away. `cosToCentre` is the cosine between the view direction
 *  and the direction to the tag. */
export function nameTagFade(distance: number, cosToCentre: number): { opacity: number; scale: number } {
  let opacity = smooth(TAG_HIDDEN_WITHIN, TAG_FULL_FROM, distance);
  if (distance < TAG_CENTRE_RANGE) opacity *= 1 - smooth(TAG_CENTRE_FULL, TAG_CENTRE_CLEAR, cosToCentre);
  return { opacity, scale: Math.max(1, distance / 30) };
}

/** A single-draw silhouette for distant players; its colours are baked into vertex attributes. */
function distantCharacter(): THREE.Mesh {
  const part = (geometry: THREE.BufferGeometry, color: number, x: number, y: number, z: number) => {
    geometry.translate(x, y, z);
    const c = new THREE.Color(color), count = geometry.getAttribute('position').count;
    const values = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) { values[i * 3] = c.r; values[i * 3 + 1] = c.g; values[i * 3 + 2] = c.b; }
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(values, 3));
    return geometry;
  };
  const parts = [
    part(new THREE.BoxGeometry(0.52, 0.68, 0.28), 0x386c62, 0, 1.17, 0),
    part(new THREE.SphereGeometry(0.19, 8, 6), 0xc79570, 0, 1.68, 0),
    part(new THREE.BoxGeometry(0.2, 0.72, 0.22), 0x2c4356, -0.15, 0.36, 0),
    part(new THREE.BoxGeometry(0.2, 0.72, 0.22), 0x2c4356, 0.15, 0.36, 0),
    part(new THREE.BoxGeometry(0.15, 0.67, 0.2), 0x386c62, -0.36, 1.14, 0),
    part(new THREE.BoxGeometry(0.15, 0.67, 0.2), 0x386c62, 0.36, 1.14, 0),
  ];
  const geometry = mergeGeometries(parts)!;
  for (const p of parts) p.dispose();
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.9 }));
  mesh.name = 'remote-character-distant';
  mesh.frustumCulled = true;
  return mesh;
}

function nickname(nick: string): THREE.Sprite {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d')!;
  ctx.font = 'bold 30px sans-serif';
  canvas.width = Math.ceil(ctx.measureText(nick).width) + 24; canvas.height = 48;
  ctx.fillStyle = 'rgba(13, 24, 31, 0.86)'; ctx.fillRect(0, 2, canvas.width, 42);
  ctx.fillStyle = '#fff'; ctx.font = 'bold 30px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText(nick, canvas.width / 2, 33);
  const texture = new THREE.CanvasTexture(canvas);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }));
  sprite.userData.devLayer = 'People/Name Tags';
  sprite.scale.set(canvas.width / 48 * 0.45, 0.45, 1);
  sprite.userData.baseScale = sprite.scale.x;
  return sprite;
}

/** Visuals and kinematic proxies for the current server roster. */
export class RemotePlayers {
  private readonly remotes = new Map<string, Remote>();
  private readonly pizzaGeometry = new THREE.BoxGeometry(.42, .12, .42);
  private readonly pizzaMaterial = new THREE.MeshStandardMaterial({ color: 0xd8b479, roughness: .9 });
  private readonly distantSource = distantCharacter();
  private selfId: string | undefined;
  private readonly toTag = new THREE.Vector3();
  private readonly view = new THREE.Vector3();
  frameMs = 0;
  readonly horns: HornEmitter[] = [];
  /** Engine voices of remote trucks with a driver, refreshed by update() (GameAudio.engineVoice, SPEC §4.6). */
  readonly engines: { id: string; state: EngineVoiceState }[] = [];
  /** Remote players as ambient walkers see them (world/walkers/reactions.ts), refreshed by update(). */
  readonly threats: WalkerThreat[] = [];
  /** `drivers`: seats an animated driver in every remote car and truck that has one (actors/driver/drivers.ts). */
  constructor(private readonly root: THREE.Object3D, private readonly physics: PhysicsClient,
    private readonly sources: { player: PlayerVisual; scooter: ScooterVisual; truck: TruckVisual;
      parked: ParkedCarVisuals }, private readonly tyreEffects: TyreEffects, private readonly drivers?: Drivers) {}

  /** `drawn`: the render pose of a locally simulated vehicle (the driven one), else its latest physics state is used. */
  update(client: MultiplayerClient, now: number, local: V3, effectsDt = 0, shared?: SharedVehicles,
    drawn: (id: string) => VehicleState | null = () => null) {
    const t0 = performance.now();
    this.horns.length = 0; this.threats.length = 0; this.engines.length = 0;
    const selfId = client.state.you?.id;
    this.selfId = selfId;
    const players: (NetPlayer & { locallySimulated?: boolean; occupant?: string | null })[] = client.state.connected
      ? client.state.players.filter(p => p.id !== selfId && !p.pose?.vehicleId) : [];
    if (client.state.connected && shared) for (const v of client.state.vehicles.values()) {
      const localId = shared.localId(v);
      const local = v.owner === selfId;
      // Existing local visuals own native IDs. Acquired foreign vehicles use the
      // same shared renderer as replicas, sampled directly from their local body.
      if (local && localId !== v.id) continue;
      const state = local ? drawn(localId) ?? this.physics.latest?.vehicles.find(s => s.id === localId) ?? null : null;
      players.push({ id: v.id, nick: client.state.players.find(p => p.id === v.occupant)?.nick ?? '',
        spawn: { E: v.pose.E, N: v.pose.N, yaw: v.pose.yaw },
        // A replica is drawn on its owner's own pose clock (net/interpolation.ts times poses by sender `t`, ms);
        // the record revision is a change counter, which made every remote shared car stutter.
        pose: state ? vehiclePose(state, v.pose.vehicle!, now) : { ...v.pose, t: Number.isFinite(v.pose.t) && v.pose.t > 0 ? v.pose.t : v.revision }, locallySimulated: local,
        occupant: v.occupant });
    }
    const present = new Set(players.map(p => p.id));
    for (const [id, remote] of this.remotes) if (!present.has(id)) {
      remote.pizza.removeFromParent(); remote.foot?.removeFromParent(); remote.distant.removeFromParent(); remote.car.removeFromParent(); remote.scooter.removeFromParent();
      remote.truck.removeFromParent(); remote.label.removeFromParent();
      (remote.label.material as THREE.SpriteMaterial).map?.dispose(); remote.label.material.dispose();
      this.tyreEffects.release(remote.effectSlot);
      this.remotes.delete(id);
    }
    const bodies: RemoteBody[] = [];
    for (const p of players) {
      let remote = this.remotes.get(p.id);
      if (!remote) {
        const distant = this.distantSource.clone();
        const car = new THREE.Group();
        const scooter = this.sources.scooter.root.clone(true);
        scooter.getObjectByName('scooter-lean')!.position.y = -VEHICLE_POSE_HEIGHT;
        // The truck root follows the chassis centre; its model's bones are cloned per remote (actors/truck.ts). A local
        // driver seated in the source truck at this moment is not part of the clone.
        const truck = cloneSkeleton(this.sources.truck.root);
        for (let seat = truck.getObjectByName('driver-seat'); seat; seat = truck.getObjectByName('driver-seat')) seat.removeFromParent();
        const label = nickname(p.nick);
        const pizza = new THREE.Mesh(this.pizzaGeometry, this.pizzaMaterial);
        for (const visual of [car, scooter, truck]) visual.userData.devLayer = 'Vehicles/Other Players Cars';
        distant.userData.devLayer = 'People/Players';
        pizza.userData.devLayer = 'People/Players';
        this.root.add(distant, car, scooter, truck, label, pizza);
        remote = { pizza, player: p, buffer: new PoseBuffer(), foot: null, distant, car, scooter, truck, label, lastT: -1, vehicleKey: '', effectSlot: this.tyreEffects.reserve(), receivedAt: now, engine: null };
        this.remotes.set(p.id, remote);
      }
      remote.player = p;
      if (p.pose && p.pose.t !== remote.lastT) { remote.buffer.push(p.pose, now); remote.lastT = p.pose.t; remote.receivedAt = now; }
      const pose = p.locallySimulated ? p.pose : remote.buffer.sample(now);
      if (pose?.mode === 'car' && pose.vehicle && pose.vehicle !== remote.vehicleKey) {
        // The starter car identity ('player') is the real PLAYER_MODEL, drawn like every other fleet model.
        const key = pose.vehicle === 'player' ? `${PLAYER_MODEL}/${PLAYER_COLOR}` : pose.vehicle;
        const model = this.sources.parked.remoteModel(key);
        if (!model) this.sources.parked.requestRemoteModel(key);
        if (model) {
          remote.car.removeFromParent();
          model.userData.devLayer = 'Vehicles/Other Players Cars';
          remote.car = model;
          remote.vehicleKey = pose.vehicle;
          this.root.add(model);
        }
      }
      const visible = !!pose && this.physics.loaded.has((() => {
        const t = collisionTileAt(pose.E, -pose.N); return `c_${t.i}_${t.j}`;
      })());
      const near = !!pose && Math.hypot(pose.E - local.x, -pose.N - local.z) < FULL_MODEL_DISTANCE;
      if (near && pose?.mode === 'foot' && !remote.foot) {
        remote.foot = cloneSkeleton(this.sources.player.root);
        this.root.add(remote.foot);
      }
      if (remote.foot) remote.foot.visible = visible && pose?.mode === 'foot' && near;
      remote.distant.visible = visible && pose?.mode === 'foot' && !near;
      remote.car.visible = visible && pose?.mode === 'car';
      remote.scooter.visible = visible && pose?.mode === 'scooter';
      remote.truck.visible = visible && pose?.mode === 'truck';
      remote.pizza.visible = visible && pose?.mode === 'foot' && pose.carrying;
      const tagged = showsNameTag(p, selfId);
      remote.label.visible = visible && tagged;
      if (p.nick !== remote.label.userData.nick) {
        const label = nickname(p.nick); label.userData.nick = p.nick;
        remote.label.removeFromParent(); (remote.label.material as THREE.SpriteMaterial).map?.dispose(); remote.label.material.dispose();
        remote.label = label; this.root.add(label); label.visible = visible && tagged;
      }
      if (!pose || !visible) continue;
      const pos = { x: pose.E, y: pose.H + remoteRootHeight(pose.mode), z: -pose.N };
      if (pose.mode !== 'foot') {
        const model = pose.mode === 'car' ? remote.car : pose.mode === 'truck' ? remote.truck : remote.scooter;
        vehicleLamps(model, pose.mode, pose.lights ?? 0);
        if (pose.horn && !p.locallySimulated && now - remote.receivedAt < 1500) this.horns.push({ id: p.id, kind: pose.mode, position: pos });
      }
      remote.pizza.position.set(pos.x - Math.sin(pose.yaw) * .45, pos.y + 1, pos.z - Math.cos(pose.yaw) * .45);
      remote.pizza.rotation.y = pose.yaw;
      for (const model of [remote.distant, remote.car, remote.scooter, remote.truck]) model.position.set(pos.x, pos.y, pos.z);
      if (remote.foot) { remote.foot.position.set(pos.x, pos.y, pos.z); remote.foot.rotation.y = pose.yaw + Math.PI; }
      remote.distant.rotation.y = pose.yaw + Math.PI;
      remote.car.quaternion.copy(vehicleRotation(pose, true));
      remote.car.position.y -= 0.75;
      remote.scooter.quaternion.copy(vehicleRotation(pose, false));
      remote.truck.quaternion.copy(vehicleRotation(pose, false));
      if (pose.mode === 'car' && pose.wheels) applyFleetWheels(remote.car, pose.wheels);
      // The owner's body damage (vehicle/damage.ts), replicated in the pose.
      if (pose.mode === 'car') showFleetDamage(remote.car, pose.damage);
      if (pose.mode === 'truck' && pose.wheels) {
        applyTrophyRig(remote.truck, pose.wheels);
        // Driven remote trucks only (a player's own pose, or a shared record with an occupant): the engine voice.
        const driven = !p.locallySimulated && (!('occupant' in p) || !!p.occupant);
        if (driven && now - remote.receivedAt < 1500) {
          let hash = 0;
          for (let i = 0; i < p.id.length; i++) hash = Math.imul(hash ^ p.id.charCodeAt(i), 16777619);
          remote.engine ??= { feed: new TruckSoundFeed(hash >>> 0 || 1), at: now };
          const dt = Math.min(0.25, Math.max(1e-3, (now - remote.engine.at) / 1000)); remote.engine.at = now;
          const forward = chassisForward(remote.truck.quaternion), speed = Math.hypot(pose.vE, pose.vN);
          const forwardSpeed = pose.vE * forward.hx - pose.vN * forward.hz;
          const out = remote.engine.feed.step(dt, { forwardSpeed, speed, wheels: pose.wheels }, { drv: pose.drv ?? null });
          this.engines.push({ id: `remote:${p.id}`, state: { drive: out.drive, speed, position: pos, velocity: { x: pose.vE, y: pose.vH, z: -pose.vN },
            local: false, wheels: pose.wheels, rearSlew: out.rearSlew } });
        }
      } else remote.engine = null;
      if (effectsDt > 0 && now - remote.receivedAt < 300 && pose.mode !== 'foot' && pose.wheels) {
        // Network ground points are relative to the physical chassis, including parked-model cars.
        this.tyreEffects.vehicle(remote.effectSlot, pos, remote.truck.quaternion, pose.wheels, pose.mode, effectsDt, Math.hypot(pose.vE, pose.vN));
      }
      // A driven damaged car vents steam or smoke from its engine bay (vehicle/mechanics.ts, from the same wire), also
      // standing still, when its owner sends a pose only every second.
      if (effectsDt > 0 && now - remote.receivedAt < 1500 && pose.mode === 'car' && (!('occupant' in p) || !!p.occupant)) {
        const bay = mechanicsOf(pose.damage);
        this.tyreEffects.engineBay(p.id, pos, remote.truck.quaternion, bay.steam, bay.smoke, effectsDt);
      }
      // A driven car or truck shows its driver, animated from the drawn pose (no extra synced field): a player's own
      // pose, or a shared record with an occupant. The local player's own shared car seats the local driver.
      const occupied = !('occupant' in p) || !!p.occupant;
      if (this.drivers && occupied && (pose.mode === 'car' || pose.mode === 'truck')) {
        const key = p.occupant && p.occupant === selfId ? 'local' : `remote:${p.id}`;
        const input = { velocity: { x: pose.vE, y: pose.vH, z: -pose.vN }, wheels: pose.wheels, horn: !!pose.horn };
        if (pose.mode === 'car' && remote.vehicleKey && remote.car.parent) {
          const model = remote.vehicleKey === 'player' ? PLAYER_MODEL : remote.vehicleKey.split('/')[0];
          this.drivers.seat(key, remote.car, fleetDriverLayout(model, remote.vehicleKey), input);
        } else if (pose.mode === 'truck' && this.sources.truck.driverLayout) {
          const frame = remote.truck.getObjectByName('driver-frame');
          if (frame) this.drivers.seat(key, frame, this.sources.truck.driverLayout, input);
        }
      }
      remote.label.position.set(pos.x, pos.y + (pose.mode === 'foot' ? 2.2 : 2), pos.z);
      if (!p.locallySimulated) bodies.push({ id: p.id, pos, yaw: pose.yaw, rot: pose.rot, mode: pose.mode });
      if (!p.locallySimulated) {
        const half = pose.mode === 'foot' ? null : VEHICLES[pose.mode].halfExt;
        const heading = pose.rot ? chassisForward(pose.rot) : { hx: Math.cos(pose.yaw + Math.PI / 2), hz: -Math.sin(pose.yaw + Math.PI / 2) };
        this.threats.push({ x: pose.E, y: pose.H, z: -pose.N, vx: pose.vE, vz: -pose.vN,
          car: half ? { ...heading, length: 2 * half.x, width: 2 * half.z } : null });
      }
    }
    this.physics.setRemotes(bodies);
    this.frameMs += (performance.now() - t0 - this.frameMs) * 0.1;
  }

  /** Fades and scales the visible name tags for this camera; call once per frame before rendering. */
  fadeLabels(camera: THREE.Camera) {
    camera.getWorldDirection(this.view);
    for (const r of this.remotes.values()) {
      const label = r.label;
      if (!label.visible) continue;
      const distance = this.toTag.subVectors(label.position, camera.position).length();
      const fade = nameTagFade(distance, distance > 1e-3 ? this.toTag.dot(this.view) / distance : 1);
      (label.material as THREE.SpriteMaterial).opacity = fade.opacity;
      if (fade.opacity <= 0.01) { label.visible = false; continue; }
      const base = label.userData.baseScale as number;
      label.scale.set(base * fade.scale, 0.45 * fade.scale, 1);
    }
  }

  markers(): { id: string; nick: string; x: number; z: number }[] {
    return [...this.remotes.values()].filter(r => showsNameTag(r.player, this.selfId) || !r.player.nick).flatMap(r => {
      const p = r.buffer.sample(performance.now());
      return p ? [{ id: r.player.id, nick: r.player.nick, x: p.E, z: -p.N }] : [];
    });
  }

  visualBounds(): { id: string; mode: NetPose['mode']; vehicle: string | null; paint: string | null;
    x: number; z: number; bottom: number; rootY: number; rotation: number[]; wheels: { pos: number[]; rotation: number[] }[] }[] {
    const bounds = new THREE.Box3();
    return [...this.remotes.entries()].flatMap(([id, remote]) => {
      const model = remote.foot?.visible ? remote.foot : remote.distant.visible ? remote.distant
        : remote.car.visible ? remote.car : remote.scooter.visible ? remote.scooter : remote.truck.visible ? remote.truck : null;
      if (!model) return [];
      model.updateWorldMatrix(true, true);
      bounds.setFromObject(model);
      const mode = remote.car.visible ? 'car' : remote.scooter.visible ? 'scooter' : remote.truck.visible ? 'truck' : 'foot';
      const material = (model as THREE.Mesh).material;
      const paint = !Array.isArray(material) && (material as THREE.MeshStandardMaterial | undefined)?.color
        ? (material as THREE.MeshStandardMaterial).color.getHexString() : null;
      return [{ id, mode, vehicle: mode === 'car' ? remote.vehicleKey : mode === 'foot' ? null : mode, paint,
        x: model.position.x, z: model.position.z, bottom: bounds.min.y, rootY: model.position.y, rotation: model.quaternion.toArray(),
        wheels: [0, 1, 2, 3].flatMap(i => { const w = model.getObjectByName(`vehicle-wheel-${i}`);
          return w ? [{ pos: w.position.toArray(), rotation: w.quaternion.toArray() }] : []; }) }];
    });
  }

  dispose() {
    this.physics.setRemotes([]); this.threats.length = 0;
    for (const r of this.remotes.values()) {
      this.tyreEffects.release(r.effectSlot);
      r.pizza.removeFromParent(); r.foot?.removeFromParent(); r.distant.removeFromParent(); r.car.removeFromParent(); r.scooter.removeFromParent();
      r.truck.removeFromParent(); r.label.removeFromParent();
      (r.label.material as THREE.SpriteMaterial).map?.dispose(); r.label.material.dispose();
    }
    this.remotes.clear();
  }
}
