export const PLAYER_LOCOMOTION_SHA256 = "5e0ef5e1a4b245f911e88df310d21cc819a24196f54b244b102ded5142b11545";

/** Ground travel per cycle, measured on planted sole vertices of the shipped player.
 * Re-measure after replacing clips/rig: scripts/measure-strides.mjs. The ankle alone
 * underestimates travel during heel/toe roll. Walkers carry their own per-body values. */
export const LOCOMOTION = [
  { clip: 'Idle_Loop', speed: 0, stride: 0, travel: 0, hipY: 0.89139, bob: 1 },
  { clip: 'Walk_Loop', speed: 1.5, stride: 1.4474, travel: 1.4474, hipY: 0.89139, bob: 1 },
  { clip: 'Jog_Fwd_Loop', speed: 3.6, stride: 5.5307, travel: 3, hipY: 0.84325, bob: 0.45 },
  { clip: 'Sprint_Loop', speed: 6, stride: 5.9558, travel: 3.8, hipY: 0.87193, bob: 0.65 },
] as const;
export const STAIR_STRIDE = 1.406;

export function locomotionWeights(speed: number): number[] {
  const weights = [0, 0, 0, 0];
  if (speed <= 0) weights[0] = 1;
  else if (speed >= LOCOMOTION[3].speed) weights[3] = 1;
  else for (let i = 0; i < LOCOMOTION.length - 1; i++) {
    const a = LOCOMOTION[i].speed, b = LOCOMOTION[i + 1].speed;
    if (speed >= a && speed <= b) { const t = (speed - a) / (b - a); weights[i] = 1 - t; weights[i + 1] = t; break; }
  }
  return weights;
}

/** Weighted travel is an approximation through quaternion blends; stance anchors
 * absorb the residual slip. Running strides are shortened by IK so cadence rises
 * with speed instead of slowing the exaggerated source clips to a bounding jog. */
export function locomotionFrequency(speed: number, weights: readonly number[], stairs: number): number {
  const travel = LOCOMOTION.reduce((sum, clip, i) => sum + clip.travel * weights[i], 0) * (1 - stairs) + STAIR_STRIDE * stairs;
  return speed > 0.01 && travel > 0.01 ? speed / travel : 0;
}

export function locomotionStrideScale(weights: readonly number[], stairs: number): number {
  const authored = LOCOMOTION.reduce((sum, clip, i) => sum + clip.stride * weights[i], 0) * (1 - stairs) + STAIR_STRIDE * stairs;
  const travel = LOCOMOTION.reduce((sum, clip, i) => sum + clip.travel * weights[i], 0) * (1 - stairs) + STAIR_STRIDE * stairs;
  return authored > 0.01 ? travel / authored : 1;
}

/** Shorter running strides need less of the source clips' exaggerated vertical bounce.
 * Preserve their mean hip height and leave walking/stair bob intact. */
export function locomotionBodyHeight(height: number, weights: readonly number[], stairs: number): number {
  const mean = LOCOMOTION.reduce((sum, clip, i) => sum + clip.hipY * weights[i], 0);
  const scale = LOCOMOTION.reduce((sum, clip, i) => sum + clip.bob * weights[i], 0);
  return height + (mean - height) * (1 - scale) * (1 - stairs);
}
