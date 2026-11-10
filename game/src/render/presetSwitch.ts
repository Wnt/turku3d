// What a preset switch in a running session cannot change (ui/graphicsMenu.ts, main.ts switchQuality): the parts of
// a tier that are built once at startup. Draw distances, vegetation and grass, the resolution cap, the planar
// reflection's size and rate, the streaming share and the walker cap switch at once; these wait for a reload.
import { QUALITY, type QualityTier } from './quality';
import { TRAFFIC_CAPS } from '../traffic/constants';

/** The startup-bound parts in which `now` differs from the tier the page booted with, as menu wording. */
export function reloadBound(boot: QualityTier, now: QualityTier): string[] {
  const a = QUALITY[boot], b = QUALITY[now], out: string[] = [];
  if (a.shadowSize !== b.shadowSize || a.splits.join() !== b.splits.join()) out.push('sun shadows');
  if (a.lights.point !== b.lights.point) out.push('lamp lights');
  if (a.aoSamples !== b.aoSamples || a.aoScale !== b.aoScale || a.aoStrength !== b.aoStrength) out.push('ambient occlusion');
  if (a.bloomScale !== b.bloomScale || a.bloomStrength !== b.bloomStrength || a.temporalSubpixel !== b.temporalSubpixel
    || a.detail !== b.detail || a.saturation !== b.saturation) out.push('bloom and surface detail');
  if (a.riverReflection !== b.riverReflection || (a.reflectionScale > 0) !== (b.reflectionScale > 0)) out.push('river reflection technique');
  if (TRAFFIC_CAPS[boot] !== TRAFFIC_CAPS[now] || a.parkedBodies !== b.parkedBodies) out.push('traffic and parked cars');
  return out;
}
