// Two wind-driven cloud decks and a restrained city night sky in the existing sky draw.
// The noise tile is generated here, with no downloaded imagery or per-frame noise generation.
import * as THREE from 'three/webgpu';
import { cameraPosition, Fn, dot, float, floor, fract, length, max, mix, normalize, positionWorld,
  pow, smoothstep, texture, uniform, vec3, vec4 } from 'three/tsl';
import type { SkyMesh } from 'three/addons/objects/SkyMesh.js';
import type { TimecycleSample } from './timecycle';
import { cloudPixels } from './cloudField';

function cloudTexture() {
  const size = 512, pixels = cloudPixels(size);
  const map = new THREE.DataTexture(pixels, size, size);
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.magFilter = THREE.LinearFilter; map.minFilter = THREE.LinearMipmapLinearFilter;
  map.generateMipmaps = true; map.needsUpdate = true;
  map.name = 'authored seamless cloud density';
  return map;
}

export class Atmosphere {
  seconds = 0;
  private readonly wind = uniform(new THREE.Vector2());
  private readonly detailWind = uniform(new THREE.Vector2(0.31, 0));
  private readonly highWind = uniform(new THREE.Vector2(0.61, 0));
  readonly coverage = uniform(0.38);
  readonly cirrusStrength = uniform(0.15);
  readonly density = uniform(1);
  readonly overcast = uniform(0);
  readonly weatherHorizon = uniform(new THREE.Color());
  private readonly clearFog = new THREE.Color();
  readonly zenith = uniform(new THREE.Color());
  readonly horizon = uniform(new THREE.Color());
  readonly cloudLit = uniform(new THREE.Color());
  readonly cloudShade = uniform(new THREE.Color());
  readonly daylight = uniform(1);
  /** Visible daytime scattering, independently calibrated from the ambient lighting bake. */
  readonly skyBrightness = uniform(0.45);
  readonly stars = uniform(0);
  readonly moonDirection = uniform(new THREE.Vector3(0.5, 0.4, -0.7).normalize());
  private readonly map = cloudTexture();

  constructor(sky: SkyMesh) {
    // Disable the addon's expensive procedural cloud branch; both decks use our shared mipmapped tile.
    sky.cloudCoverage.value = 0;
    const scattered = sky.material.colorNode as THREE.Node<'vec4'>;
    sky.material.colorNode = Fn(() => {
      const view = normalize(positionWorld.sub(cameraPosition)).toVar();
      const horizonWeight = pow(float(1).sub(max(view.y, 0)), 4);
      const night = mix(this.zenith, this.horizon, horizonWeight);
      const displayScale = mix(1, this.skyBrightness, smoothstep(0.07, 0.26, sky.sunPosition.y));
      const base = scattered.rgb.mul(this.daylight).mul(displayScale).add(night).toVar();
      // Curved projection avoids infinite texture frequency at the horizon. Decks drift in different directions.
      const plane = view.xz.div(max(view.y, 0).add(0.14));
      const uv = plane.mul(0.18).add(this.wind);
      const field = texture(this.map, uv).toVar();
      const detail = texture(this.map, plane.mul(0.18 * 2.7).add(this.detailWind));
      const shape = field.r.mul(0.72).add(detail.r.mul(0.28)).toVar();
      const threshold = float(0.834).sub(this.coverage.mul(0.55));
      const body = smoothstep(threshold, threshold.add(0.12), shape).toVar();
      const horizonFade = smoothstep(0.005, 0.13, view.y);
      const alpha = body.mul(this.density).mul(horizonFade).clamp(0, 0.96).toVar();
      const sunward = pow(max(dot(view, normalize(sky.sunPosition)), 0), 8);
      const shade = smoothstep(0.48, 0.72, shape).mul(0.65);
      const cloud = mix(this.cloudLit, this.cloudShade, shade)
        .add(this.cloudLit.mul(sunward).mul(body.mul(float(1).sub(body))).mul(1.3));
      const aerial = mix(base, cloud, smoothstep(0, 0.3, view.y).mul(0.8).add(0.2));
      // Thin high cloud streaks cross the gaps, independently of the cumulus deck.
      const high = texture(this.map, plane.mul(0.12).add(this.highWind));
      const cirrus = smoothstep(0.46, 0.75, high.g).mul(high.r).mul(this.cirrusStrength).mul(smoothstep(0.015, 0.12, view.y));
      const fairSky = mix(mix(base, this.cloudLit, cirrus), aerial, alpha);
      // Broad low cloud banks and slower upper density move independently, using the existing samples.
      // The horizon below is joined to effective weather fog, keeping strong variation in the ceiling.
      const ceilingBody = smoothstep(0.25, 0.72, shape.mul(0.8).add(high.r.mul(0.2)));
      const banks = mix(this.cloudShade.mul(0.7), this.cloudLit.mul(0.55), ceilingBody);
      const sheet = banks.add(night.mul(0.6));
      const grey = dot(sheet, vec3(0.2126, 0.7152, 0.0722));
      const sheetSky = mix(sheet, vec3(grey), this.daylight.mul(0.75))
        .mul(mix(1, 0.72, smoothstep(0.85, 1, this.overcast)));
      const overcastSky = mix(this.weatherHorizon, sheetSky, smoothstep(0.01, 0.2, view.y));
      const deck = smoothstep(0, 0.85, this.overcast);
      const skyColor = mix(fairSky, overcastSky, deck);

      // Sparse, stable directions: stars only emerge after twilight and disappear behind both cloud decks.
      const cell = view.mul(560);
      const id = floor(cell);
      const h = fract(id.mul(vec3(0.1031, 0.103, 0.0973)));
      const hash = fract(dot(h, h.yzx.add(33.33)).mul(17.17));
      const point = float(1).sub(smoothstep(0.09, 0.3, length(fract(cell).sub(0.5))));
      const star = point.mul(smoothstep(0.9985, 1, hash)).mul(this.stars).mul(horizonFade)
        .mul(float(1).sub(alpha)).mul(float(1).sub(cirrus)).mul(float(1).sub(deck));
      // A modest moon disc and soft halo; kept out of the direct-light system owned by lighting.
      const moonDot = dot(view, this.moonDirection);
      const moonDisc = smoothstep(0.999987, 0.999991, moonDot);
      const moonHalo = pow(max(moonDot, 0), 650).mul(0.018);
      const moon = moonDisc.mul(1.5).add(moonHalo).mul(this.stars).mul(horizonFade)
        .mul(float(1).sub(alpha)).mul(float(1).sub(cirrus)).mul(float(1).sub(deck));
      return vec4(skyColor.add(vec3(0.7, 0.8, 1).mul(star))
        .add(vec3(0.75, 0.84, 1).mul(moon)), 1);
    })();
    sky.material.needsUpdate = true;
  }

  /** Reduce each UV offset in double precision before sending floats to the GPU. */
  setTime(seconds: number) {
    this.seconds = seconds;
    const wrap = (n: number) => n - Math.floor(n);
    this.wind.value.set(wrap(seconds * 0.0007), wrap(seconds * 0.00023));
    this.detailWind.value.set(wrap(seconds * 0.0007 * 2.7 + 0.31), wrap(seconds * (0.00023 * 2.7 - 0.00017)));
    this.highWind.value.set(0.61, wrap(seconds * -0.00009));
  }

  /** Structural subset of WeatherSample: the weather clock owns transitions, this hook never accumulates state. */
  setWeather(sample: { cloudCover: number; overcast: number }, fogColor?: THREE.Color) {
    this.coverage.value = THREE.MathUtils.clamp(sample.cloudCover, 0, 1);
    this.overcast.value = THREE.MathUtils.clamp(sample.overcast, 0, 1);
    this.weatherHorizon.value.copy(fogColor ?? this.clearFog);
  }

  setSun(elevation: number, azimuth: number, sample: TimecycleSample) {
    this.zenith.value.setRGB(...sample.skyZenith);
    this.horizon.value.setRGB(...sample.skyHorizon);
    this.clearFog.setRGB(...sample.fogColor);
    this.weatherHorizon.value.copy(this.clearFog);
    this.daylight.value = THREE.MathUtils.smoothstep(elevation, -10, -1);
    this.stars.value = 1 - THREE.MathUtils.smoothstep(elevation, -12, -5);
    const day = THREE.MathUtils.smoothstep(elevation, -4, 15);
    const warm = THREE.MathUtils.smoothstep(elevation, -5, 1) * (1 - THREE.MathUtils.smoothstep(elevation, 3, 15));
    this.cloudLit.value.setRGB(0.018 + 2.6 * day + warm * 0.5, 0.025 + 2.65 * day + warm * 0.19, 0.045 + 2.75 * day);
    this.cloudShade.value.setRGB(0.009 + 0.64 * day + warm * 0.15, 0.014 + 0.76 * day + warm * 0.07, 0.029 + 0.95 * day);
    // Stylised full moon travels opposite the sun with an offset, above the evening skyline.
    const a = (azimuth + 135) * Math.PI / 180;
    const e = THREE.MathUtils.clamp(25 - elevation * 0.3, 12, 60) * Math.PI / 180;
    this.moonDirection.value.set(Math.sin(a) * Math.cos(e), Math.sin(e), -Math.cos(a) * Math.cos(e));
  }

  dispose() { this.map.dispose(); }
}
