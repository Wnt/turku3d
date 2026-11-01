// Population ring: which of the network's cars this client draws and simulates (plan §3.6). Positions never
// depend on it; it only decides visibility, so it may differ between clients without moving any car.
import { RING } from './constants';

export interface View {
  x: number; z: number;
  /** Unit horizontal forward of the camera. */
  fx: number; fz: number;
  /** Horizontal half field of view (rad). */
  halfFov: number;
}

/** True when (x, z) is inside the camera's horizontal view cone (plus margin). */
export function inView(view: View, x: number, z: number): boolean {
  const dx = x - view.x, dz = z - view.z, d = Math.hypot(dx, dz);
  if (d < 1) return true;
  return (dx * view.fx + dz * view.fz) / d >= Math.cos(Math.min(Math.PI, view.halfFov + RING.viewMargin));
}

/** May a car that is not drawn yet appear at distance d? `fill` lifts the view rule (start, teleport). */
export function mayCreate(d: number, visible: boolean, fill: boolean): boolean {
  if (fill) return d <= RING.createInViewMax;
  return visible ? d >= RING.createInViewMin && d <= RING.createInViewMax
    : d >= RING.createOutOfViewMin && d <= RING.removeOutOfView;
}

/** Must a drawn car go? */
export function mustRemove(d: number, visible: boolean): boolean {
  return d > (visible ? RING.removeInView : RING.removeOutOfView);
}
