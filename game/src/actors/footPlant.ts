import type { V3 } from '../core/origin';

/** A stance contact is held in world X/Z until the animated foot begins its swing.
 * Height remains a terrain query, so a loaded/corrected tread can still settle under it.
 * A short release blend avoids snapping to the authored swing trajectory. */
export class FootPlant {
  private anchor: { x: number; z: number } | null = null;
  private weight = 0;
  private planted = false;

  reset() { this.anchor = null; this.weight = 0; this.planted = false; }

  update(dt: number, animated: V3, lift: number, contact: boolean): { x: number; z: number; weight: number } {
    if (!contact || (this.anchor && Math.hypot(animated.x - this.anchor.x, animated.z - this.anchor.z) > 0.12)) this.reset();
    if (contact && !this.planted && lift < 0.025 && this.weight < 0.05) {
      this.anchor = { x: animated.x, z: animated.z }; this.planted = true; this.weight = 1;
    }
    if (lift > 0.035) this.planted = false;
    if (!this.planted) this.weight *= Math.exp(-Math.max(0, dt) * 28);
    return this.project(animated);
  }

  /** Sample the held support without advancing its state (for terrain probes). */
  project(animated: V3): { x: number; z: number; weight: number } {
    return this.anchor ? { x: animated.x + (this.anchor.x - animated.x) * this.weight,
      z: animated.z + (this.anchor.z - animated.z) * this.weight, weight: this.weight }
      : { x: animated.x, z: animated.z, weight: 0 };
  }
}
