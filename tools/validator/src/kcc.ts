// Rapier KinematicCharacterController walker with the game settings (ARCHITECTURE Task 1.13):
// capsule r 0.3 / half-height 0.6 (1.8 m), step clearance 0.35 m / minWidth 0.15, snap 0.3,
// maxClimb 45°, minSlide 50°, offset 0.02.
//
// Gravity handling (Rapier 0.21 autostep depends on walking speed). Two modes:
//  * 'probe' (default): the tools/engine-probe scheme. One move per frame; while grounded the
//    vertical velocity is a small constant -0.5 m/s (-8 mm/frame) and full gravity applies only when
//    airborne. A bounded clearance move climbs steps through 0.35 m at slow speeds.
//  * 'split': horizontal (autostep) move with y = 0, then a separate vertical gravity move.
//    Measured: at 1.5 m/s only steps <= 0.22 m are climbed (0.25 m blocks); at 4 m/s <= 0.25 m.
// The split mode is retained only for comparing Rapier behaviour in synthetic tests.

import { CHARACTER_FILTER, GROUP, groups } from '../../../game/src/shared/col/index.ts';

const DT = 1 / 60;
const GRAVITY = 9.81;

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
  autostepMaxHeight: 0.35,
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
  private lastGrounded = false;
  x = 0;
  y = 0;
  z = 0;

  constructor(RAPIER: any, world: any, cfg: KccConfig = DEFAULT_KCC) {
    this.RAPIER = RAPIER;
    this.world = world;
    this.cfg = cfg;
    const cc = world.createCharacterController(cfg.offset);
    cc.setUp({ x: 0, y: 1, z: 0 });
    cc.enableAutostep(Math.min(cfg.autostepMaxHeight, 0.2), cfg.autostepMinWidth, false);
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
    let m = this.cc.computedMovement();
    let grounded = this.cc.computedGrounded();
    if (this.cfg.mode === 'probe' && this.grounded && Math.hypot(dx, dz) > 0.001) {
      const requested = Math.hypot(dx, dz);
      const progressed = (m.x * dx + m.z * dz) / requested;
      if (progressed < requested * 0.5) {
        const feet = this.feetY;
        const ahead = (this.cfg.radius + requested) / requested;
        const ray = new this.RAPIER.Ray(
          { x: this.x + dx * ahead, y: feet + this.cfg.autostepMaxHeight + 0.1, z: this.z + dz * ahead },
          { x: 0, y: -1, z: 0 },
        );
        const support = this.world.castRayAndGetNormal(ray, this.cfg.autostepMaxHeight + 0.4, true,
          flags, groups(0xff00, GROUP.STATIC_SOLID), this.collider);
        const supportY = support ? ray.origin.y - support.timeOfImpact : -Infinity;
        const rearRay = new this.RAPIER.Ray(
          { x: this.x - dx * this.cfg.radius / requested, y: feet + 0.2, z: this.z - dz * this.cfg.radius / requested },
          { x: 0, y: -1, z: 0 },
        );
        const rear = this.world.castRayAndGetNormal(rearRay, 0.7, true, flags,
          groups(0xff00, GROUP.STATIC_SOLID), this.collider);
        const baseY = Math.min(feet, rear ? rearRay.origin.y - rear.timeOfImpact : feet);
        if (supportY - baseY <= this.cfg.autostepMaxHeight + 0.002) {
          const p0 = { x: this.x, y: this.y, z: this.z };
          const raised = this.cfg.autostepMaxHeight + this.cfg.offset;
          this.cc.computeColliderMovement(this.collider, { x: 0, y: raised, z: 0 }, flags, CHARACTER_FILTER);
          const up = this.cc.computedMovement();
          if (up.y >= raised - this.cfg.offset) {
            this.collider.setTranslation({ x: this.x, y: this.y + up.y, z: this.z });
            this.cc.computeColliderMovement(this.collider, { x: dx, y: 0, z: dz }, flags, CHARACTER_FILTER);
            const across = this.cc.computedMovement();
            if ((across.x * dx + across.z * dz) / requested > progressed + 0.001) {
              this.collider.setTranslation({ x: this.x + across.x, y: this.y + up.y + across.y, z: this.z + across.z });
              this.cc.computeColliderMovement(this.collider, { x: 0, y: -(raised + this.cfg.snapToGround), z: 0 }, flags, CHARACTER_FILTER);
              const down = this.cc.computedMovement();
              const rise = up.y + across.y + down.y;
              if (this.cc.computedGrounded() && rise > -this.cfg.offset && rise <= this.cfg.autostepMaxHeight + 0.002) {
                m = { x: across.x, y: rise, z: across.z };
                grounded = true;
              }
            }
            this.collider.setTranslation(p0);
          }
        }
      }
    }
    this.x += m.x;
    this.y += m.y;
    this.z += m.z;
    this.collider.setTranslation({ x: this.x, y: this.y, z: this.z });
    this.lastGrounded = grounded;
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
    this.grounded = this.lastGrounded;
  }

  free(): void {
    this.world.removeCharacterController(this.cc);
    this.world.removeCollider(this.collider, false);
  }
}
