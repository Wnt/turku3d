// Third-person orbit camera. yaw: rotation about +Y (0 = looking north, -Z); pitch: positive looks up.
import * as THREE from 'three/webgpu';

export class OrbitCamera {
  yaw = 0;
  pitch = -0.12;
  distance = 4.5;
  minDist = 1.5;
  maxDist = 14;
  /** Max distance allowed by a camera-collision ray (set asynchronously from physics). */
  clampDist = Infinity;
  private curDist = 4.5;
  readonly target = new THREE.Vector3();

  constructor(readonly camera: THREE.PerspectiveCamera) {}

  input(dx: number, dy: number, wheel: number) {
    this.yaw -= dx;
    this.pitch = THREE.MathUtils.clamp(this.pitch - dy, -1.2, 0.6);
    this.distance = THREE.MathUtils.clamp(this.distance * (1 + wheel * 0.1), this.minDist, this.maxDist);
  }

  /** Unit direction from target to camera. */
  backDir(out = new THREE.Vector3()) {
    const cp = Math.cos(this.pitch);
    return out.set(Math.sin(this.yaw) * cp, -Math.sin(this.pitch), Math.cos(this.yaw) * cp);
  }

  update(dt: number, target: THREE.Vector3) {
    // The visual character already follows interpolated physics. A second positional
    // filter creates speed-dependent lag and catch-up jumps as frame delivery varies.
    this.target.copy(target);
    const want = Math.min(this.distance, this.clampDist);
    this.curDist += (want - this.curDist) * Math.min(1, dt * (want < this.curDist ? 20 : 4));
    const p = this.backDir().multiplyScalar(this.curDist).add(this.target);
    this.camera.position.copy(p);
    this.camera.lookAt(this.target);
  }

  /** Horizontal forward/right unit vectors for movement input. */
  basis() {
    return { fx: -Math.sin(this.yaw), fz: -Math.cos(this.yaw), rx: Math.cos(this.yaw), rz: -Math.sin(this.yaw) };
  }
}
