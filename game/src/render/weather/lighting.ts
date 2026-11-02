import { linearRgb, type TimecycleSample } from '../timecycle';
import type { WeatherSample } from './schedule';

const overcastFog = linearRgb('#bbc0c8');
const unit = (value: number) => Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;

/** Derive from the clear timecycle every time; never multiply the previously applied weather. */
export function weatherLighting(base: TimecycleSample, weather: Readonly<WeatherSample>): TimecycleSample {
  const overcast = unit(weather.overcast), mist = Math.max(unit(weather.fog), unit(weather.rain) * .22);
  if (overcast === 0 && mist === 0) return base;
  const diffuse = overcast * (1 - base.night);
  return {
    ...base,
    sunIntensity: base.sunIntensity * (1 - .88 * overcast),
    hemisphereIntensity: base.hemisphereIntensity * (1 + 1.4 * diffuse),
    environmentIntensity: base.environmentIntensity * (1 + .75 * diffuse),
    exposure: base.exposure * (1 + .1 * diffuse),
    fogNear: base.fogNear / (1 + mist * 12),
    // Add extinction rather than scaling the clear view distance: dense fog stays dense
    // when the clear-weather skyline grows from 4 to 8 or 15 kilometres.
    fogFar: base.fogFar / (1 + mist * base.fogFar / 220),
    fogColor: base.fogColor.map((channel, i) => channel + (overcastFog[i] - channel) * diffuse * .65) as unknown as TimecycleSample['fogColor'],
  };
}
