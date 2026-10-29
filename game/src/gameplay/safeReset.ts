// Safe-reset driver for main.ts: builds the per-frame probe from the physics state, samples the river surface,
// runs the RecoveryMonitor rules and performs the reset through the physics worker. See README.md "Safe reset".
import { FlippedCarMonitor, recoveryVehicle } from './flippedCar';
import * as THREE from 'three/webgpu';
import { collisionTileAt } from '../core/origin';
import type { Manifest } from '../world/manifest';
import type { PhysicsClient } from '../physics/client';
import type { CollisionStreamer } from '../world/collisionStreaming';
import type { RecoveryHud } from '../ui/recovery';
import { RECOVERY, RecoveryMonitor, lowestGround, type PlayableArea, type Probe } from './recovery';

/** What the frame loop knows about the player this frame. */
interface PlayerFrame {
  mode: 'foot' | 'car';
  drivenId: string | null;
  feet: THREE.Vector3;
  vel: THREE.Vector3;
  grounded: boolean;
  /** The character's facing (`PlayerVisual.root.rotation.y`, forward = (sin, cos) in x/z). */
  facing: number;
  /** Developer fly mode is on: rules hold during flight; outside the area they resume when flight ends. */
  flying: boolean;
}

const tileId = (x: number, z: number) => { const t = collisionTileAt(x, z); return `c_${t.i}_${t.j}`; };

export class SafeReset {
  private readonly flipped = new FlippedCarMonitor();
  flippedCarId: string | null = null;
  readonly monitor: RecoveryMonitor;
  /** Game-clock seconds: unpaused frames only, so the countdown waits while paused or in a menu. */
  time = 0;
  waterDepth: number | null = null;
  recovering = false;
  private waterBusy = false;
  private lastWaterProbe = -Infinity;
  /** The physics placement (`PhysicsClient.placement.seq`) already accounted for, and the last probed position. */
  private placementSeen = 0;
  private readonly at = new THREE.Vector3(NaN, NaN, NaN);
  private readonly lowestManifestGround: number;
  private readonly quat = new THREE.Quaternion();
  private readonly axis = new THREE.Vector3();

  /** `onReset` turns the camera behind the player: `car` snaps the chase camera, `foot` gets the facing. */
  constructor(private readonly area: PlayableArea, private readonly manifest: Manifest,
    private readonly physics: PhysicsClient, private readonly streamer: CollisionStreamer,
    private readonly hud: RecoveryHud, private readonly onReset: (mode: 'foot' | 'car', heading: number) => void) {
    this.monitor = new RecoveryMonitor(area);
    this.lowestManifestGround = lowestGround(Object.keys(manifest.collision), manifest.collision);
  }

  get lost() { return this.monitor.lost; }

  /** Call every unpaused frame outside menus; `resetNow` is a respawn / vehicleReset press while lost. */
  update(dt: number, player: PlayerFrame, resetNow: boolean) {
    if (this.recovering) return;
    this.time += dt;
    this.followPlacement(player);
    if (player.flying) {
      this.monitor.hold();
      this.waterDepth = null;
      return;
    }
    const probe = this.probe(player);
    if (!probe) return;
    this.at.set(probe.x, probe.y, probe.z);
    if (!this.waterBusy && this.time - this.lastWaterProbe >= 0.25) {
      this.waterBusy = true; this.lastWaterProbe = this.time;
      const y = probe.y;
      void this.physics.raycast({ x: probe.x, y: y + 50, z: probe.z }, { x: 0, y: -1, z: 0 }, 100, 'water').then(hit => {
        if (!this.recovering) this.waterDepth = hit ? hit.point.y - y : null;
      }).finally(() => { this.waterBusy = false; });
    }
    const ground = lowestGround(this.physics.loaded.keys(), this.manifest.collision);
    if (this.monitor.update(probe, Number.isFinite(ground) ? ground : this.lowestManifestGround) || resetNow) void this.recover(player);
  }

  /** A loaded or new game placed the player: start sampling afresh at the new place. */
  restart() {
    this.monitor.restart(this.time);
    this.waterDepth = null;
  }

  /**
   * Any placement of the player through the physics client (a test or debug teleport, an injected mission step,
   * a loaded game) farther than `RECOVERY.teleport` from where they stood restarts sampling there, so a later fall
   * never returns them to the old place. Nearer placements (leaving a car, R in a car) keep the samples.
   */
  private followPlacement(player: PlayerFrame) {
    const placed = this.physics.placement;
    if (placed.seq === this.placementSeen) return;
    this.placementSeen = placed.seq;
    if (placed.actor !== (player.mode === 'car' ? player.drivenId : 'character')) return;
    const moved = Math.hypot(placed.pos.x - this.at.x, placed.pos.y - this.at.y, placed.pos.z - this.at.z);
    if (!(moved <= RECOVERY.teleport)) this.restart();
  }

  /** The lost prompt or the edge notice for this frame; true when the edge notice shows. */
  render(feet: THREE.Vector3, button: string, flipButton: string) {
    const lost = this.monitor.lost;
    const nearEdge = !lost && this.area.edgeDistance(feet.x, feet.z) <= RECOVERY.edgeWarning;
    this.hud.setEdge(nearEdge);
    // A fall that resolved without a reset (landed safely, or a restart) removes its countdown prompt.
    if (lost) this.hud.lost(lost.reason, this.monitor.countdown(this.time) ?? 0, button);
    else this.hud.clearLost();
    this.hud.setFlipped(!lost && this.flippedCarId !== null, flipButton);
    return nearEdge;
  }

  updateFlipped(dt: number, drivenId: string | null, feet: THREE.Vector3, mayDrive: (id: string) => boolean) {
    const vehicle = recoveryVehicle(this.physics.latest?.vehicles ?? [], drivenId, feet, mayDrive);
    this.flippedCarId = this.flipped.update(dt, vehicle) ? vehicle?.id ?? null : null;
  }

  snapshot(feet: THREE.Vector3) {
    const m = this.monitor;
    return { armed: m.armed, lost: m.lost, countdown: m.countdown(this.time), samples: m.samples.length, resets: m.resets,
      waterDepth: this.waterDepth, edgeDistance: this.area.edgeDistance(feet.x, feet.z), hud: this.hud.snapshot() };
  }

  /** The lost rules see the feet on foot and the car body in a car. */
  private probe(p: PlayerFrame): Probe | null {
    const base = { time: this.time, inVehicle: p.mode === 'car', waterDepth: this.waterDepth };
    if (p.mode === 'foot') {
      return { ...base, x: p.feet.x, y: p.feet.y, z: p.feet.z, heading: p.facing, grounded: p.grounded, steady: true,
        vy: p.vel.y, loaded: this.physics.loaded.has(tileId(p.feet.x, p.feet.z)) };
    }
    const v = this.physics.latest?.vehicles.find(c => c.id === p.drivenId);
    if (!v) return null;
    this.quat.set(v.rot.x, v.rot.y, v.rot.z, v.rot.w);
    const forward = this.axis.set(1, 0, 0).applyQuaternion(this.quat);
    const heading = Math.atan2(forward.x, forward.z);
    const upright = this.axis.set(0, 1, 0).applyQuaternion(this.quat).y > 0.7;
    const contacts = v.wheels.filter(w => w.contact).length;
    return { ...base, x: v.pos.x, y: v.pos.y, z: v.pos.z, heading, grounded: contacts > 0, steady: contacts >= 3 && upright,
      vy: v.linvel.y, loaded: this.physics.loaded.has(tileId(v.pos.x, v.pos.z)) };
  }

  /** Puts the player (or the driven car, upright and still) back at the chosen safe sample. */
  private async recover(player: PlayerFrame) {
    this.recovering = true;
    const target = this.monitor.target();
    console.info(`[recovery] reset (${this.monitor.lost?.reason ?? 'button'}) from ${player.feet.x.toFixed(1)},${player.feet.y.toFixed(1)},${player.feet.z.toFixed(1)} `
      + `to ${target.x.toFixed(1)},${target.y.toFixed(1)},${target.z.toFixed(1)}`);
    const id = tileId(target.x, target.z);
    this.streamer.update(target.x, target.z, true);
    for (let i = 0; i < 600 && this.manifest.collision[id] && !this.physics.loaded.has(id); i++) {
      await new Promise(resolve => setTimeout(resolve, 50));
      this.streamer.update(target.x, target.z, true);
    }
    const hit = await this.physics.raycast({ x: target.x, y: target.y + 2, z: target.z }, { x: 0, y: -1, z: 0 }, 60);
    const y = hit?.point.y ?? target.y;
    if (player.mode === 'car' && player.drivenId) {
      // heading is the forward direction atan2(x, z); resetVehicle's yaw turns the chassis +X axis.
      this.physics.resetVehicle({ x: target.x, y: y + 0.75, z: target.z },
        Math.atan2(-Math.cos(target.heading), Math.sin(target.heading)), player.drivenId);
    } else {
      this.physics.setCharacterEnabled(true, { x: target.x, y: y + 0.05, z: target.z });
    }
    this.placementSeen = this.physics.placement.seq;
    this.at.set(target.x, y, target.z);
    this.onReset(player.mode, target.heading);
    this.waterDepth = null;
    this.monitor.recovered(target, this.time);
    this.hud.recovered();
    this.recovering = false;
  }
}
