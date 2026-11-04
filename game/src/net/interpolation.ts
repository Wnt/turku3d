import { interpolateRotation } from './orientation';
import type { NetPose } from './client';
import { PoseTrack, type PoseOps } from '../actors/interpolation';
import { quatConjugate, quatExp, quatLog, quatMultiply } from '../actors/renderClock';

/** Remote poses are sent every RATE_MS (55 ms) at most: the render delay is one median spacing plus the delivery
 *  jitter (30-250 ms), and a late pose is extrapolated along its velocity and turn rate for up to 250 ms. */
const MIN_DELAY = 0.03, MAX_DELAY = 0.25;

const ops: PoseOps<NetPose> = {
  lerp: (a, b, f) => {
    if (a.mode !== b.mode || a.vehicle !== b.vehicle) return b;
    const angle = Math.atan2(Math.sin(b.yaw - a.yaw), Math.cos(b.yaw - a.yaw));
    const lerp = (x: keyof Pick<NetPose, 'E' | 'N' | 'H' | 'vE' | 'vN' | 'vH'>) => a[x] + (b[x] - a[x]) * f;
    return { ...b, E: lerp('E'), N: lerp('N'), H: lerp('H'), yaw: a.yaw + angle * f,
      rot: b.mode === 'foot' ? undefined : interpolateRotation(a, b, f),
      wheels: b.wheels?.map((w, i) => {
        const prev = a.wheels?.[i];
        if (!prev) return w;
        const mix = (a: number, b: number) => a + (b - a) * f;
        return { ...w, pos: { x: mix(prev.pos.x, w.pos.x), y: mix(prev.pos.y, w.pos.y), z: mix(prev.pos.z, w.pos.z) },
          steer: mix(prev.steer, w.steer), rotation: mix(prev.rotation, w.rotation), suspension: mix(prev.suspension, w.suspension) };
      }),
      vE: lerp('vE'), vN: lerp('vN'), vH: lerp('vH') };
  },
  extrapolate: (a, b, dt, s) => {
    const k = dt > 0 ? s / dt : 0, same = a.mode === b.mode && a.vehicle === b.vehicle;
    const yaw = same ? b.yaw + Math.atan2(Math.sin(b.yaw - a.yaw), Math.cos(b.yaw - a.yaw)) * k : b.yaw;
    let rot = b.rot;
    if (same && a.rot && b.rot) {
      const turn = quatLog(quatMultiply(b.rot, quatConjugate(a.rot)));
      rot = quatMultiply(quatExp({ x: turn.x * k, y: turn.y * k, z: turn.z * k }), b.rot);
    }
    return { ...b, E: b.E + b.vE * s, N: b.N + b.vN * s, H: b.H + b.vH * s, yaw, rot,
      wheels: same ? b.wheels?.map((w, i) => {
        const prev = a.wheels?.[i];
        return prev ? { ...w, rotation: w.rotation + (w.rotation - prev.rotation) * k } : w;
      }) : b.wheels };
  },
  pos: p => ({ x: p.E, y: p.H, z: -p.N }),
  rot: p => p.rot,
  speed: p => Math.hypot(p.vE, p.vN),
  with: (p, pos, rot) => ({ ...p, E: pos.x, H: pos.y, N: -pos.z, ...(rot ? { rot } : {}) }),
};

/** A remote player's poses on the shared render clock (actors/interpolation.ts PoseTrack): drawn at the sender's own
 *  timestamps (`t`, ms) mapped through the fastest recent delivery, interpolated, extrapolated when late and blended
 *  back without snapping. A mode or vehicle change, a teleport or a rejoin epoch is never blended. */
export class PoseBuffer {
  private track = new PoseTrack(ops, { minDelay: MIN_DELAY, maxDelay: MAX_DELAY, intervals: 1 });
  private lastT = -Infinity;
  /** `arrival`: local ms. */
  push(pose: NetPose, arrival: number) {
    if (pose.t === this.lastT) return;
    if (pose.t < this.lastT) { this.track.clear(); this.track.clock.reset(); }
    this.lastT = pose.t;
    this.track.push(pose.t / 1000, pose, arrival / 1000, true,
      (a, b) => a.mode === b.mode && a.vehicle === b.vehicle && Math.hypot(a.E - b.E, a.N - b.N, a.H - b.H) < 2 + 0.25 * Math.hypot(b.vE, b.vN));
  }
  /** `now`: local ms. */
  sample(now: number): NetPose | null { return this.track.sample(now / 1000); }
}
