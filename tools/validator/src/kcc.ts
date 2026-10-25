// Rapier KinematicCharacterController walker with the game settings (ARCHITECTURE Task 1.13):
// capsule r 0.3 / half-height 0.6 (1.8 m), autostep 0.22 m / minWidth 0.15, snap 0.3,
// maxClimb 45°, minSlide 50°, offset 0.02.
//
// Gravity handling (Rapier 0.21 autostep fails when the desired move carries a large downward
// component, e.g. -0.05 m/frame; research/reconstruction.md). Two modes:
//  * 'probe' (default): the tools/engine-probe scheme. One move per frame; while grounded the
//    vertical velocity is a small constant -0.5 m/s (-8 mm/frame) and full gravity applies only when
//    airborne. Measured: a walker at 1.5-5 m/s climbs steps <= 0.27 m and is blocked at >= 0.30 m.
//  * 'split': horizontal (autostep) move with y = 0, then a separate vertical gravity move.
//    Measured: at 1.5 m/s only steps <= 0.22 m are climbed (0.25 m blocks); at 4 m/s <= 0.25 m.
// Both: at 1.0 m/s nothing >= 0.22 m is climbed (autostep is speed dependent).

import { CHARACTER_FILTER, GROUP, groups } from '../../../game/src/shared/col/index.ts';

export const DT = 1 / 60;
export const GRAVITY = 9.81;

export interface KccConfig {
  radius: number;
  halfHeight: number;
  offset: number;
  autostepMaxHeight: number;
  autostepMinWidth: number;
  snapToGround: number;
  maxClimbDeg: number;
  minSlideDeg: number;
  mode: 'probe' | 'split';
}

export const DEFAULT_KCC: KccConfig = {
  radius: 0.3,
  halfHeight: 0.6,
  offset: 0.02,
  autostepMaxHeight: 0.22,
  autostepMinWidth: 0.15,
  snapToGround: 0.3,
  maxClimbDeg: 45,
  minSlideDeg: 50,
  mode: 'probe',
};

export class Walker {
  RAPIER: any;
  world: any;
  cfg: KccConfig;
  cc: any;
  collider: any;
  vy = 0;
  grounded = false;
  x = 0;
  y = 0;
  z = 0;

  constructor(RAPIER: any, world: any, cfg: KccConfig = DEFAULT_KCC) {
    this.RAPIER = RAPIER;
    this.world = world;
    this.cfg = cfg;
    const cc = world.createCharacterController(cfg.offset);
    cc.setUp({ x: 0, y: 1, z: 0 });
    cc.enableAutostep(cfg.autostepMaxHeight, cfg.autostepMinWidth, false);
    cc.enableSnapToGround(cfg.snapToGround);
    cc.setMaxSlopeClimbAngle((cfg.maxClimbDeg * Math.PI) / 180);
    cc.setMinSlopeSlideAngle((cfg.minSlideDeg * Math.PI) / 180);
    cc.setSlideEnabled(true);
    cc.setApplyImpulsesToDynamicBodies(false);
    this.cc = cc;
    const desc = RAPIER.ColliderDesc.capsule(cfg.halfHeight, cfg.radius).setCollisionGroups(groups(GROUP.CHARACTER, GROUP.STATIC_SOLID));
    this.collider = world.createCollider(desc);
  }

  /** Feet (capsule bottom) height. */
  get feetY(): number {
    return this.y - this.cfg.halfHeight - this.cfg.radius;
  }

  /** Place the capsule so its bottom is `clearance` above `groundY`. */
  place(x: number, groundY: number, z: number, clearance = 0.03): void {
    this.x = x;
    this.y = groundY + this.cfg.halfHeight + this.cfg.radius + clearance;
    this.z = z;
    this.vy = 0;
    this.grounded = false;
    this.collider.setTranslation({ x: this.x, y: this.y, z: this.z });
  }

  private move(dx: number, dy: number, dz: number): void {
    const flags = this.RAPIER.QueryFilterFlags.EXCLUDE_SENSORS;
    this.cc.computeColliderMovement(this.collider, { x: dx, y: dy, z: dz }, flags, CHARACTER_FILTER);
    const m = this.cc.computedMovement();
    this.x += m.x;
    this.y += m.y;
    this.z += m.z;
    this.collider.setTranslation({ x: this.x, y: this.y, z: this.z });
  }

  /** One 60 Hz frame: horizontal velocity (vx, vz) in m/s, then gravity. */
  step(vx: number, vz: number): void {
    this.vy = this.grounded ? -0.5 : Math.max(-50, this.vy - GRAVITY * DT);
    if (this.cfg.mode === 'probe') {
      this.move(vx * DT, this.vy * DT, vz * DT);
    } else {
      if (vx !== 0 || vz !== 0) this.move(vx * DT, 0, vz * DT);
      this.move(0, this.vy * DT, 0);
    }
    this.grounded = this.cc.computedGrounded();
  }

  free(): void {
    this.world.removeCharacterController(this.cc);
    this.world.removeCollider(this.collider, false);
  }
}
