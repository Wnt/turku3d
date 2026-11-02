/** Weather is a pure function of shared UTC and the world's seed, independent of day-clock developer edits. */
export const WEATHER_NAMES = ['clear', 'partly cloudy', 'overcast', 'light rain', 'heavy rain', 'fog'] as const;
export type WeatherName = typeof WEATHER_NAMES[number];
export interface WeatherSample {
  cloudCover: number; overcast: number; rain: number; wetness: number; fog: number; wind: number;
}
export const WEATHER_PRESETS: Readonly<Record<WeatherName, Readonly<WeatherSample>>> = {
  clear: { cloudCover: .06, overcast: 0, rain: 0, wetness: 0, fog: 0, wind: .15 },
  'partly cloudy': { cloudCover: .38, overcast: 0, rain: 0, wetness: 0, fog: 0, wind: .28 },
  overcast: { cloudCover: .96, overcast: .85, rain: 0, wetness: .12, fog: .12, wind: .4 },
  'light rain': { cloudCover: 1, overcast: .9, rain: .32, wetness: .7, fog: .22, wind: .38 },
  'heavy rain': { cloudCover: 1, overcast: 1, rain: 1, wetness: 1, fog: .48, wind: .85 },
  fog: { cloudCover: .92, overcast: .65, rain: 0, wetness: .28, fog: 1, wind: .08 },
};
export const WEATHER_SEED = 0x7475726b;
/** A front lasts 12 real minutes, with a two-minute smooth transition. */
export const FRONT_MS = 12 * 60_000, TRANSITION_MS = 2 * 60_000;
const FIELDS = ['cloudCover', 'overcast', 'rain', 'wetness', 'fog', 'wind'] as const;
export function blendWeather(a: Readonly<WeatherSample>, b: Readonly<WeatherSample>, fraction: number): WeatherSample {
  const t = Math.max(0, Math.min(1, fraction));
  if (t === 0) return { ...a };
  if (t === 1) return { ...b };
  return Object.fromEntries(FIELDS.map(k => [k, a[k] + (b[k] - a[k]) * t])) as unknown as WeatherSample;
}
export function smoothWeather(t: number) { t = Math.max(0, Math.min(1, t)); return t * t * (3 - 2 * t); }
function hash(n: number) { n = Math.imul(n ^ (n >>> 16), 0x45d9f3b); n = Math.imul(n ^ (n >>> 16), 0x45d9f3b); return (n ^ (n >>> 16)) >>> 0; }
export function frontName(index: number, seed = WEATHER_SEED): WeatherName {
  // Temperate summer distribution; rain comes in distinct fronts rather than flickering random frames.
  const r = hash(index ^ seed) % 100;
  return r < 24 ? 'clear' : r < 51 ? 'partly cloudy' : r < 72 ? 'overcast' : r < 88 ? 'light rain' : r < 96 ? 'heavy rain' : 'fog';
}
export function weatherAt(sharedUtcMs: number, seed = WEATHER_SEED): WeatherSample {
  const index = Math.floor(sharedUtcMs / FRONT_MS), elapsed = sharedUtcMs - index * FRONT_MS;
  const previous = WEATHER_PRESETS[frontName(index - 1, seed)], target = WEATHER_PRESETS[frontName(index, seed)];
  const result = blendWeather(previous, target, smoothWeather(elapsed / TRANSITION_MS));
  // Water remains after a shower and dries over the following front. No frame integration or client history.
  const remaining = 1 - smoothWeather(elapsed / FRONT_MS);
  result.wetness = Math.max(result.wetness, previous.wetness * remaining);
  return result;
}

export class WeatherClock {
  private override: WeatherName | null = null;
  private transition: { from: WeatherSample; since: number; duration: number } | null = null;
  sample: WeatherSample;
  constructor(private readonly sharedNow: () => number, readonly seed = WEATHER_SEED, initial?: WeatherName) {
    this.override = initial ?? null;
    this.sample = initial ? { ...WEATHER_PRESETS[initial] } : weatherAt(sharedNow(), seed);
  }
  get mode() { return this.override ?? 'live'; }
  setWeather(name: WeatherName | 'live', seconds = 8) {
    if (name !== 'live' && !WEATHER_NAMES.includes(name)) throw new Error(`Unknown weather: ${name}`);
    if (!Number.isFinite(seconds) || seconds < 0) throw new Error('Weather transition must be finite and non-negative');
    this.update();
    this.override = name === 'live' ? null : name;
    this.transition = { from: { ...this.sample }, since: this.sharedNow(), duration: seconds * 1000 };
    this.update();
  }
  update() {
    const now = this.sharedNow(), target = this.override ? WEATHER_PRESETS[this.override] : weatherAt(now, this.seed);
    const t = this.transition;
    const progress = t ? (t.duration === 0 ? 1 : Math.max(0, now - t.since) / t.duration) : 1;
    this.sample = t ? blendWeather(t.from, target, smoothWeather(progress)) : { ...target };
    if (progress >= 1) this.transition = null;
    return this.sample;
  }
}
