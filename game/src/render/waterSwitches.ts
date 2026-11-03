// River surface A/B switches, `?water=token,token` (play, bench lab and replays alike). The techniques come from the
// GTA V water study (loops/techstudy REPORT.md §8, W1-W9); each token selects the path a technique replaced, for the
// coordinator's winperf (reduced) and airperf (medium) A/B runs and Jonni's look checks. Owner: water loop. Each token
// is removed with its losing path once its A/B is decided, at most ten merges after it was added.
//   oldsun   W5 off: the earlier broad sun highlight (Phong powers 240/36) and flat body colour
export interface WaterSwitches {
  /** W5: a tight, energy-normalised sun lobe (GTA's falloff 1,600-2,000) and wrap-diffuse wave relief. */
  tightSun: boolean;
}

export const WATER_SWITCHES: WaterSwitches = { tightSun: true };

const TOKENS = ['oldsun'] as const;

/** Parse `?water=`; unknown tokens throw, as other URL parameters do. Absent keeps the defaults. */
export function parseWaterSwitches(value: string | null): WaterSwitches {
  const switches: WaterSwitches = { tightSun: true };
  if (!value) return switches;
  for (const token of value.split(',').map(t => t.trim()).filter(Boolean)) {
    if (!(TOKENS as readonly string[]).includes(token)) throw new Error(`bad ?water= token (${TOKENS.join('|')}): ${token}`);
    if (token === 'oldsun') switches.tightSun = false;
  }
  return switches;
}

/** Set the session's switches from the page URL before any river material is built. */
export function applyWaterSwitches(search: string) {
  Object.assign(WATER_SWITCHES, parseWaterSwitches(new URLSearchParams(search).get('water')));
}
