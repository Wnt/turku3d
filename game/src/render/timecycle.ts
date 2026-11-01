// Timecycle: one keyed table drives sun, sky ambient, fog, exposure, bloom and the night factor.
// Values are our own (timecycle.json); GTA informs only the structure (keyed rows, an artificial ambient term).
import table from './timecycle.json';

export type Rgb = readonly [number, number, number];

export interface TimecycleSample {
  sunIntensity: number;
  sunColor: Rgb;
  environmentIntensity: number;
  hemisphereIntensity: number;
  hemisphereSky: Rgb;
  hemisphereGround: Rgb;
  fogColor: Rgb;
  fogNear: number;
  fogFar: number;
  exposure: number;
  /** Medium-tier units; PostProcessing scales it by the tier's own day strength. */
  bloomStrength: number;
  bloomThreshold: number;
  /** 0 by day, 1 at full night: artificial light, lit windows and lamps scale with it. */
  night: number;
  /** Twilight and night sky added to the analytic sky (black by day). */
  skyZenith: Rgb;
  skyHorizon: Rgb;
  /** Cloud layer colours (render/clouds.ts), scaled by cloudBrightness in sky units. */
  cloudLight: Rgb;
  cloudShade: Rgb;
  cloudBrightness: number;
}

type ScalarRow = 'sunIntensity' | 'environmentIntensity' | 'hemisphereIntensity' | 'fogNear' | 'fogFar' | 'exposure'
  | 'bloomStrength' | 'bloomThreshold' | 'night' | 'cloudBrightness';
type ColorRow = 'sunColor' | 'hemisphereSky' | 'hemisphereGround' | 'fogColor' | 'skyZenith' | 'skyHorizon' | 'cloudLight' | 'cloudShade';
const SCALARS: readonly ScalarRow[] = ['sunIntensity', 'environmentIntensity', 'hemisphereIntensity', 'fogNear', 'fogFar',
  'exposure', 'bloomStrength', 'bloomThreshold', 'night', 'cloudBrightness'];
const COLORS: readonly ColorRow[] = ['sunColor', 'hemisphereSky', 'hemisphereGround', 'fogColor', 'skyZenith', 'skyHorizon', 'cloudLight', 'cloudShade'];

/** sRGB hex to linear-light RGB, the renderer's working space. */
export function linearRgb(hex: string): Rgb {
  const n = Number.parseInt(hex.slice(1), 16);
  const channel = (v: number) => { const c = v / 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  return [channel((n >> 16) & 255), channel((n >> 8) & 255), channel(n & 255)];
}

const KEYS: readonly number[] = table.elevation;
const COLOR_KEYS = Object.fromEntries(COLORS.map(row => [row, table.rows[row].map(linearRgb)])) as Record<ColorRow, Rgb[]>;
for (const row of [...SCALARS, ...COLORS]) {
  if (table.rows[row].length !== KEYS.length) throw new Error(`timecycle row ${row} needs ${KEYS.length} keys`);
}

/** Index of the key at or below `x` and the fraction towards the next key; clamped at both ends. */
function bracket(keys: readonly number[], x: number): [number, number] {
  if (!(x > keys[0])) return [0, 0];
  for (let i = 1; i < keys.length; i++) {
    if (x <= keys[i]) return [i - 1, (x - keys[i - 1]) / (keys[i] - keys[i - 1])];
  }
  return [keys.length - 2, 1];
}

/** Linear interpolation of every row at a sun elevation (degrees). */
export function timecycle(elevation: number): TimecycleSample {
  const [i, t] = bracket(KEYS, elevation);
  const scalar = (row: ScalarRow) => table.rows[row][i] + (table.rows[row][i + 1] - table.rows[row][i]) * t;
  const colour = (row: ColorRow): Rgb => {
    const a = COLOR_KEYS[row][i], b = COLOR_KEYS[row][i + 1];
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  };
  const sample = Object.fromEntries(SCALARS.map(row => [row, scalar(row)])) as Record<ScalarRow, number>;
  return { ...sample, sunColor: colour('sunColor'), hemisphereSky: colour('hemisphereSky'),
    hemisphereGround: colour('hemisphereGround'), fogColor: colour('fogColor'), skyZenith: colour('skyZenith'), skyHorizon: colour('skyHorizon'),
    cloudLight: colour('cloudLight'), cloudShade: colour('cloudShade') };
}

/** Night factor for a sun elevation in degrees: 0 by day, 1 at full night. Pure; identical on every client. */
export function nightFactor(elevation: number): number {
  const [i, t] = bracket(KEYS, elevation);
  return table.rows.night[i] + (table.rows.night[i + 1] - table.rows.night[i]) * t;
}

/** Share of windows lit at a local clock hour (0..24, wrapping): peaks in the early evening, thins after midnight. */
export function litFraction(hour: number): number {
  const h = ((hour % 24) + 24) % 24;
  const [i, t] = bracket(table.litFraction.hour, h);
  return table.litFraction.value[i] + (table.litFraction.value[i + 1] - table.litFraction.value[i]) * t;
}

/** Night grading: dim light (scene-linear luminance below `luminance[0]`) keeps `saturation` at full night; light
 *  above `luminance[1]` keeps the day grade. */
export const MESOPIC = { saturation: table.mesopic.saturation, luminance: table.mesopic.luminance as [number, number] };
export const WINDOW_LIGHT = { warm: linearRgb(table.windows.warm), cool: linearRgb(table.windows.cool),
  coolShare: table.windows.coolShare, coolLevel: table.windows.coolLevel, paneLevel: table.windows.paneLevel,
  intensity: table.windows.intensity };
