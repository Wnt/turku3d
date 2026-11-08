// Aerial perspective and height fog: replaces the scene's linear THREE.Fog with an exponential, height-dependent haze
// whose extinction is stronger in blue than in red, plus a low mist layer by time of day and weather.
import * as THREE from 'three/webgpu';
import { abs, cameraPosition, dot, exp, float, Fn, max, mix, normalize, output, positionWorld, pow, select, smoothstep,
  uniform, vec3, vec4 } from 'three/tsl';
import type { TimecycleSample } from './timecycle';
import type { WeatherSample } from './weather/schedule';

/** Haze optical depth at the timecycle's fogFar (green channel). The clear-day fogFar (12 km) leaves about half the
 *  light of land at the 6 km far plane; weather fog shortens fogFar to its visibility (weather/lighting.ts). */
export const HAZE_DEPTH = 1.3;
/** Relative extinction of red, green and blue: aerosol haze between Mie (about λ^-1.3) and Rayleigh (λ^-4), so
 *  distant land turns blue-grey before it fades into the horizon colour. */
export const HAZE_COLOUR = [0.7, 1, 1.5] as const;
/** Scale heights (m above sea level, engine y): the haze thins slowly; mist lies in the valleys and over water. */
export const HAZE_HEIGHT = 1500, MIST_HEIGHT = 18;
/** Mist extinction per metre at sea level at full strength: a 300 m path over the river halves the light. */
export const MIST_DENSITY = 1 / 450;
/** Forward scattering towards the sun (Henyey-Greenstein g) and how much it brightens the in-scatter there. */
const SUN_G = 0.6, SUN_GLOW = 0.12;

/** How strong the low mist is (0-1): weather fog and rain, and calm clear mornings around sunrise (the sun between
 *  -3 and 10 degrees before noon). Wind disperses the morning mist. */
export function mistAmount(weather: Readonly<WeatherSample>, elevation: number, hour: number) {
  const unit = (v: number) => (Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0);
  const sunrise = hour < 12 ? THREE.MathUtils.smoothstep(elevation, -6, -2) * (1 - THREE.MathUtils.smoothstep(elevation, 4, 10)) : 0;
  const morning = 0.35 * sunrise * (1 - unit(weather.wind) / 0.5) * (1 - unit(weather.overcast));
  return Math.max(unit(weather.fog), unit(weather.rain) * 0.5, Math.max(0, morning));
}

/** r186 materials take the scene's `fogNode` over the node built from `scene.fog` (NodeManager.getFogNode). */
export type SceneFog = THREE.Scene & { fogNode?: THREE.Node | null };

export class AerialFog {
  /** In-scatter colour: the timecycle/weather fog colour, which the sky's horizon also joins (atmosphere.ts). */
  readonly color = uniform(new THREE.Vector3(...new THREE.Color(0xb8cde0).toArray()));
  readonly sunColor = uniform(new THREE.Vector3(1, 1, 1));
  readonly sunDirection = uniform(new THREE.Vector3(0, 1, 0));
  /** Green-channel haze extinction per metre at sea level. */
  readonly haze = uniform(HAZE_DEPTH / 12000);
  /** Mist extinction per metre at sea level. */
  readonly mist = uniform(0);
  /** The view is fully fogged from fogFar on (weather fog walls, the world edge). */
  readonly far = uniform(12000);
  readonly node: THREE.Node<'vec4'>;

  constructor() {
    this.node = Fn(() => {
      const ray = positionWorld.sub(cameraPosition).toVar();
      const distance = ray.length().toVar();
      const h0 = max(cameraPosition.y, 0);
      // Optical depth of a density exp(-y / H) along the ray, per metre of path at sea-level density:
      // exp(-h0 / H) * (1 - exp(-k)) / k with k = dy / H (its limit 1 - k/2 for a level ray).
      const layer = (height: number) => {
        const k = ray.y.div(height).toVar();
        const along = select(abs(k).lessThan(1e-3), float(1).sub(k.mul(0.5)), float(1).sub(exp(k.negate())).div(k));
        return exp(h0.div(-height)).mul(along).mul(distance);
      };
      const haze = vec3(...HAZE_COLOUR).mul(this.haze.mul(layer(HAZE_HEIGHT)));
      const mist = this.mist.mul(layer(MIST_HEIGHT));
      const edge = float(1).sub(smoothstep(this.far.mul(0.6), this.far, distance));
      const transmittance = exp(haze.add(mist).negate()).mul(edge).toVar();
      const cosine = dot(normalize(ray), this.sunDirection);
      const lobe = float(1 - SUN_G * SUN_G).div(pow(float(1 + SUN_G * SUN_G).sub(cosine.mul(2 * SUN_G)), 1.5));
      const sunTint = this.sunColor.div(max(dot(this.sunColor, vec3(0.2126, 0.7152, 0.0722)), 1e-4));
      const inscatter = this.color.mul(mix(vec3(1), sunTint, 0.5).mul(lobe.mul(SUN_GLOW)).add(1));
      return vec4(output.rgb.mul(transmittance).add(inscatter.mul(float(1).sub(transmittance))), output.a);
    })();
  }

  /** Follow the applied timecycle/weather sample and the sun. */
  update(sample: Readonly<TimecycleSample>, weather: Readonly<WeatherSample>, sunDirection: THREE.Vector3, elevation: number, hour: number) {
    this.color.value.set(...sample.fogColor);
    this.sunColor.value.set(...sample.sunColor);
    this.sunDirection.value.copy(sunDirection);
    this.haze.value = HAZE_DEPTH / Math.max(sample.fogFar, 1);
    this.far.value = sample.fogFar;
    this.mist.value = MIST_DENSITY * mistAmount(weather, elevation, hour);
  }
}
