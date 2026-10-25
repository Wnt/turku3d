import * as THREE from 'three/webgpu';
import type { VehicleState } from '../physics/protocol.ts';
import type { PhysicsClient } from '../physics/client.ts';
import { rotate } from './math.ts';

function chaseSettings(speed: number) {
  const s = Math.min(30, Math.max(0, speed));
  return { distance: 6.5 + s * 0.12, fov: 60 + s * 0.45 };
}

/** Right-stick orbit, delayed recentering and a sphere sweep protecting the camera near plane. */
export class ChaseCamera {
  yaw = 0;
  pitch = -0.2;
  private idle = 0;
  private epoch = 0;
  private busy = false;
  private active = false;
  private allowed = Infinity;
  constructor(private camera: THREE.PerspectiveCamera, private physics: Pick<PhysicsClient, 'cameraCast'>) {}

  leave() { this.active = false; this.epoch++; this.allowed = Infinity; this.camera.fov = 60; this.camera.updateProjectionMatrix(); }

  update(dt: number, state: VehicleState, lookX: number, lookY: number, reset: boolean) {
    const forward = rotate({ x: 1, y: 0, z: 0 }, state.rot);
    const heading = Math.atan2(-forward.x, -forward.z);
    if (!this.active || reset) { this.yaw = heading; this.pitch = -0.2; this.idle = 0; this.allowed = Infinity; }
    this.active = true;
    if (Math.abs(lookX) + Math.abs(lookY) > 0.001) {
      this.yaw -= lookX;
      this.pitch = THREE.MathUtils.clamp(this.pitch - lookY, -0.9, 0.35);
      this.idle = 0;
    } else this.idle += dt;
    if (this.idle > 1.2 && state.speed > 1) {
      const delta = Math.atan2(Math.sin(heading - this.yaw), Math.cos(heading - this.yaw));
      this.yaw += delta * (1 - Math.exp(-dt * 2.5));
      this.pitch += (-0.2 - this.pitch) * (1 - Math.exp(-dt));
    }
    const { distance, fov } = chaseSettings(state.speed);
    this.camera.fov += (fov - this.camera.fov) * (1 - Math.exp(-dt * 3));
    this.camera.updateProjectionMatrix();
    const target = new THREE.Vector3(state.pos.x, state.pos.y + 0.9, state.pos.z);
    const cp = Math.cos(this.pitch);
    const direction = new THREE.Vector3(Math.sin(this.yaw) * cp, -Math.sin(this.pitch), Math.cos(this.yaw) * cp);
    // Pose and target belong to the same render frame. A worker reply only changes the
    // collision limit used by a later frame; it must never move the camera asynchronously.
    this.camera.position.copy(target).addScaledVector(direction, Math.min(distance, this.allowed));
    this.camera.lookAt(target);
    if (this.busy) return;
    this.busy = true;
    const epoch = this.epoch;
    void this.physics.cameraCast(target, direction, distance).then(allowed => {
      if (!this.active || epoch !== this.epoch) return;
      this.allowed = allowed;
    }).finally(() => { this.busy = false; });
  }
}
