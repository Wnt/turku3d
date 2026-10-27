import * as THREE from 'three/webgpu';
import { clone as cloneSkeleton } from 'three/addons/utils/SkeletonUtils.js';
import { collisionTileAt } from '../core/origin';
import type { PhysicsClient } from '../physics/client';
import type { PlayerVisual } from '../actors/player';
import type { CarVisual } from '../actors/car';
import type { ScooterVisual } from '../actors/scooter';
import type { MultiplayerClient, NetPlayer, NetPose } from './client';
import { PoseBuffer } from './interpolation';

interface Remote {
  player: NetPlayer; buffer: PoseBuffer; foot: THREE.Object3D; car: THREE.Object3D; scooter: THREE.Object3D;
  label: THREE.Sprite; lastT: number;
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
  frameMs = 0;
  constructor(private readonly root: THREE.Object3D, private readonly physics: PhysicsClient,
    private readonly sources: { player: PlayerVisual; car: CarVisual; scooter: ScooterVisual }) {}

  update(client: MultiplayerClient, now: number) {
    const t0 = performance.now();
    const selfId = client.state.you?.id;
    const players = client.state.connected ? client.state.players.filter(p => p.id !== selfId) : [];
    const present = new Set(players.map(p => p.id));
    for (const [id, remote] of this.remotes) if (!present.has(id)) {
      remote.foot.removeFromParent(); remote.car.removeFromParent(); remote.scooter.removeFromParent();
      remote.label.removeFromParent();
      (remote.label.material as THREE.SpriteMaterial).map?.dispose(); remote.label.material.dispose();
      this.remotes.delete(id);
    }
    const bodies: { id: string; pos: { x: number; y: number; z: number }; yaw: number; mode: NetPose['mode'] }[] = [];
    for (const p of players) {
      let remote = this.remotes.get(p.id);
      if (!remote) {
        const foot = cloneSkeleton(this.sources.player.root);
        const car = this.sources.car.root.clone(true);
        const scooter = this.sources.scooter.root.clone(true);
        const label = nickname(p.nick);
        this.root.add(foot, car, scooter, label);
        remote = { player: p, buffer: new PoseBuffer(), foot, car, scooter, label, lastT: -1 };
        this.remotes.set(p.id, remote);
      }
      remote.player = p;
      if (p.pose && p.pose.t !== remote.lastT) { remote.buffer.push(p.pose, now); remote.lastT = p.pose.t; }
      const pose = remote.buffer.sample(now);
      const visible = !!pose && this.physics.loaded.has((() => {
        const t = collisionTileAt(pose.E, -pose.N); return `c_${t.i}_${t.j}`;
      })());
      remote.foot.visible = visible && pose?.mode === 'foot';
      remote.car.visible = visible && pose?.mode === 'car';
      remote.scooter.visible = visible && pose?.mode === 'scooter';
      remote.label.visible = visible;
      if (!pose || !visible) continue;
      const pos = { x: pose.E, y: pose.H, z: -pose.N };
      for (const model of [remote.foot, remote.car, remote.scooter]) model.position.set(pos.x, pos.y, pos.z);
      remote.foot.rotation.y = pose.yaw + Math.PI;
      remote.car.rotation.y = pose.yaw + Math.PI / 2;
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

  dispose() {
    this.physics.setRemotes([]);
    for (const r of this.remotes.values()) {
      r.foot.removeFromParent(); r.car.removeFromParent(); r.scooter.removeFromParent(); r.label.removeFromParent();
      (r.label.material as THREE.SpriteMaterial).map?.dispose(); r.label.material.dispose();
    }
    this.remotes.clear();
  }
}
