// River surface A/B switches, `?water=token,token` (play, bench lab and replays alike). The techniques come from the
// GTA V water study (loops/techstudy REPORT.md §8, W1-W9); each token selects the path a technique replaced, for the
// coordinator's winperf (reduced) and airperf (medium) A/B runs and Jonni's look checks. Owner: water loop. Each token
// is removed with its losing path once its A/B is decided, at most ten merges after it was added.
//   oldsun    W5 off: the earlier broad sun highlight (Phong powers 240/36) and flat body colour
//   nodepth   W3 off: no bank fade; the river is opaque and writes depth again
//   nomotion  W7 off: river pixels keep the motion vector of whatever lies below them
//   planar | ssr | sky   W1: the river's reflection on every tier that has one: the planar reflector pass, the
//            screen-space march over TRAA's last frame (reduced's default), or the sky alone
import type { RiverReflection } from './quality';

export interface WaterSwitches {
  /** W5: a tight, energy-normalised sun lobe (GTA's falloff 1,600-2,000) and wrap-diffuse wave relief. */
  tightSun: boolean;
  /** W3: the body colour thickens with the depth of water over the previous frame's opaque bed and walls. */
  depth: boolean;
  /** W7: the river writes its own TRAA motion vectors. */
  motion: boolean;
  /** W1: the reflection every reflecting tier uses instead of its own (render/water.ts riverReflection). */
  reflect: RiverReflection | null;
}

const DEFAULTS: WaterSwitches = { tightSun: true, depth: true, motion: true, reflect: null };
export const WATER_SWITCHES: WaterSwitches = { ...DEFAULTS };

const TOKENS = ['oldsun', 'nodepth', 'nomotion', 'planar', 'ssr', 'sky'] as const;

/** Parse `?water=`; unknown tokens throw, as other URL parameters do. Absent keeps the defaults. */
export function parseWaterSwitches(value: string | null): WaterSwitches {
  const switches: WaterSwitches = { ...DEFAULTS };
  if (!value) return switches;
  for (const token of value.split(',').map(t => t.trim()).filter(Boolean)) {
    if (!(TOKENS as readonly string[]).includes(token)) throw new Error(`bad ?water= token (${TOKENS.join('|')}): ${token}`);
    if (token === 'oldsun') switches.tightSun = false;
    if (token === 'nodepth') switches.depth = false;
    if (token === 'nomotion') switches.motion = false;
    if (token === 'planar' || token === 'ssr' || token === 'sky') switches.reflect = token;
  }
  return switches;
}

/** Set the session's switches from the page URL before any river material is built. */
export function applyWaterSwitches(search: string) {
  Object.assign(WATER_SWITCHES, parseWaterSwitches(new URLSearchParams(search).get('water')));
}
