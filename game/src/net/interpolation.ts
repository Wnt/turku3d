import { interpolateRotation } from './orientation';
import type { NetPose } from './client';

interface Sample { pose: NetPose; arrival: number }
const BUFFER_MS = 100;
const MAX_EXTRAPOLATE_MS = 200;

/** Samples network poses against local arrival times; server timestamps only detect a new epoch. */
export class PoseBuffer {
  private samples: Sample[] = [];
  push(pose: NetPose, arrival: number) {
    const previous = this.samples.at(-1);
    if (previous && pose.t === previous.pose.t) return;
    if (previous && pose.t < previous.pose.t) this.samples = [];
    this.samples.push({ pose, arrival });
    if (this.samples.length > 5) this.samples.shift();
  }

  sample(now: number): NetPose | null {
    if (!this.samples.length) return null;
    const target = now - BUFFER_MS;
    const first = this.samples[0];
    if (target <= first.arrival) return first.pose;
    for (let i = 1; i < this.samples.length; i++) {
      const a = this.samples[i - 1], b = this.samples[i];
      if (target > b.arrival) continue;
      if (a.pose.mode !== b.pose.mode || a.pose.vehicle !== b.pose.vehicle) return b.pose;
      const f = Math.min(1, Math.max(0, (target - a.arrival) / Math.max(1, b.arrival - a.arrival)));
      const angle = Math.atan2(Math.sin(b.pose.yaw - a.pose.yaw), Math.cos(b.pose.yaw - a.pose.yaw));
      const lerp = (x: keyof Pick<NetPose, 'E' | 'N' | 'H' | 'vE' | 'vN' | 'vH'>) => a.pose[x] + (b.pose[x] - a.pose[x]) * f;
      return { ...b.pose, E: lerp('E'), N: lerp('N'), H: lerp('H'), yaw: a.pose.yaw + angle * f,
        rot: b.pose.mode === 'foot' ? undefined : interpolateRotation(a.pose, b.pose, f),
        wheels: b.pose.wheels?.map((w, i) => {
          const prev = a.pose.wheels?.[i];
          if (!prev) return w;
          const mix = (a: number, b: number) => a + (b - a) * f;
          return { ...w, pos: { x: mix(prev.pos.x, w.pos.x), y: mix(prev.pos.y, w.pos.y), z: mix(prev.pos.z, w.pos.z) },
            steer: mix(prev.steer, w.steer), rotation: mix(prev.rotation, w.rotation), suspension: mix(prev.suspension, w.suspension) };
        }),
        vE: lerp('vE'), vN: lerp('vN'), vH: lerp('vH') };
    }
    const last = this.samples.at(-1)!;
    const dt = Math.min(MAX_EXTRAPOLATE_MS, Math.max(0, target - last.arrival)) / 1000;
    return { ...last.pose, E: last.pose.E + last.pose.vE * dt, N: last.pose.N + last.pose.vN * dt,
      H: last.pose.H + last.pose.vH * dt };
  }
}
