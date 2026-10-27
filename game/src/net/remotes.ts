import * as THREE from 'three/webgpu';
import { clone as cloneSkeleton } from 'three/addons/utils/SkeletonUtils.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { collisionTileAt } from '../core/origin';
import type { V3 } from '../core/origin';
import type { PhysicsClient } from '../physics/client';
import type { PlayerVisual } from '../actors/player';
import type { CarVisual, ParkedCarVisuals } from '../actors/car';
import type { ScooterVisual } from '../actors/scooter';
import type { MultiplayerClient, NetPlayer, NetPose } from './client';
import { PoseBuffer } from './interpolation';
import { remoteRootHeight, VEHICLE_POSE_HEIGHT } from './height';

interface Remote {
  player: NetPlayer; buffer: PoseBuffer; foot: THREE.Object3D | null; distant: THREE.Mesh;
  car: THREE.Object3D; scooter: THREE.Object3D; vehicleKey: string;
  label: THREE.Sprite; lastT: number;
}

const FULL_MODEL_DISTANCE = 6;

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
  canvas.width = 128; canvas.height = 48;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = 'rgba(13, 24, 31, 0.86)'; ctx.fillRect(6, 2, 116, 42);
  ctx.fillStyle = '#fff'; ctx.font = 'bold 30px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText(nick, 64, 33);
  const texture = new THREE.CanvasTexture(canvas);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }));
  sprite.scale.set(1.2, 0.45, 1);
  return sprite;
}

/** Visuals and kinematic proxies for the current server roster. */
export class RemotePlayers {
  private readonly remotes = new Map<string, Remote>();
  private readonly distantSource = distantCharacter();
  frameMs = 0;
  constructor(private readonly root: THREE.Object3D, private readonly physics: PhysicsClient,
    private readonly sources: { player: PlayerVisual; car: CarVisual; scooter: ScooterVisual; parked: ParkedCarVisuals }) {}

  update(client: MultiplayerClient, now: number, local: V3) {
    const t0 = performance.now();
    const selfId = client.state.you?.id;
    const players = client.state.connected ? client.state.players.filter(p => p.id !== selfId) : [];
    const present = new Set(players.map(p => p.id));
    for (const [id, remote] of this.remotes) if (!present.has(id)) {
      remote.foot?.removeFromParent(); remote.distant.removeFromParent(); remote.car.removeFromParent(); remote.scooter.removeFromParent();
      remote.label.removeFromParent();
      (remote.label.material as THREE.SpriteMaterial).map?.dispose(); remote.label.material.dispose();
      this.remotes.delete(id);
    }
    const bodies: { id: string; pos: { x: number; y: number; z: number }; yaw: number; mode: NetPose['mode'] }[] = [];
    for (const p of players) {
      let remote = this.remotes.get(p.id);
      if (!remote) {
        const distant = this.distantSource.clone();
        const car = this.sources.car.root.clone(true);
        const scooter = this.sources.scooter.root.clone(true);
        scooter.getObjectByName('scooter-lean')!.position.y = -VEHICLE_POSE_HEIGHT;
        const label = nickname(p.nick);
        this.root.add(distant, car, scooter, label);
        remote = { player: p, buffer: new PoseBuffer(), foot: null, distant, car, scooter, label, lastT: -1, vehicleKey: 'player' };
        this.remotes.set(p.id, remote);
      }
      remote.player = p;
      if (p.pose && p.pose.t !== remote.lastT) { remote.buffer.push(p.pose, now); remote.lastT = p.pose.t; }
      const pose = remote.buffer.sample(now);
      if (pose?.mode === 'car' && pose.vehicle && pose.vehicle !== remote.vehicleKey) {
        const model = pose.vehicle === 'player' ? this.sources.car.root.clone(true) : this.sources.parked.remoteModel(pose.vehicle);
        if (model) {
          remote.car.removeFromParent();
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
      remote.label.visible = visible;
      if (!pose || !visible) continue;
      const pos = { x: pose.E, y: pose.H + remoteRootHeight(pose.mode), z: -pose.N };
      for (const model of [remote.distant, remote.car, remote.scooter]) model.position.set(pos.x, pos.y, pos.z);
      if (remote.foot) { remote.foot.position.set(pos.x, pos.y, pos.z); remote.foot.rotation.y = pose.yaw + Math.PI; }
      remote.distant.rotation.y = pose.yaw + Math.PI;
      remote.car.rotation.y = pose.yaw + Math.PI / 2;
      if (remote.vehicleKey !== 'player') remote.car.position.y -= 0.75;
      remote.scooter.rotation.y = pose.yaw + Math.PI / 2;
      remote.label.position.set(pos.x, pos.y + (pose.mode === 'foot' ? 2.2 : 2), pos.z);
      bodies.push({ id: p.id, pos, yaw: pose.yaw, mode: pose.mode });
    }
    this.physics.setRemotes(bodies);
    this.frameMs += (performance.now() - t0 - this.frameMs) * 0.1;
  }

  markers(): { id: string; nick: string; x: number; z: number }[] {
    return [...this.remotes.values()].flatMap(r => {
      const p = r.buffer.sample(performance.now());
      return p ? [{ id: r.player.id, nick: r.player.nick, x: p.E, z: -p.N }] : [];
    });
  }

  visualBounds(): { id: string; mode: NetPose['mode']; vehicle: string | null; paint: string | null;
    x: number; z: number; bottom: number; rootY: number }[] {
    const bounds = new THREE.Box3();
    return [...this.remotes.entries()].flatMap(([id, remote]) => {
      const model = remote.foot?.visible ? remote.foot : remote.distant.visible ? remote.distant
        : remote.car.visible ? remote.car : remote.scooter.visible ? remote.scooter : null;
      if (!model) return [];
      model.updateWorldMatrix(true, true);
      bounds.setFromObject(model);
      const mode = remote.car.visible ? 'car' : remote.scooter.visible ? 'scooter' : 'foot';
      const material = (model as THREE.Mesh).material;
      const paint = !Array.isArray(material) && (material as THREE.MeshStandardMaterial | undefined)?.color
        ? (material as THREE.MeshStandardMaterial).color.getHexString() : null;
      return [{ id, mode, vehicle: mode === 'car' ? remote.vehicleKey : mode === 'scooter' ? 'scooter' : null, paint,
        x: model.position.x, z: model.position.z, bottom: bounds.min.y, rootY: model.position.y }];
    });
  }

  dispose() {
    this.physics.setRemotes([]);
    for (const r of this.remotes.values()) {
      r.foot?.removeFromParent(); r.distant.removeFromParent(); r.car.removeFromParent(); r.scooter.removeFromParent(); r.label.removeFromParent();
      (r.label.material as THREE.SpriteMaterial).map?.dispose(); r.label.material.dispose();
    }
    this.remotes.clear();
  }
}
