// Two cloud layers composited into the analytic sky: low cumulus from a tileable noise texture (assets/nature/clouds.png)
// lit towards the sun with a two-tap density gradient, and high cirrus streaks. Colours come from the timecycle rows
// (cloudLight/cloudShade/cloudBrightness), so dusk tints them and the city glow lights their bases at night.
import * as THREE from 'three/webgpu';
import { add, dot, float, max, mix, normalize, pow, smoothstep, texture, time, uniform, vec2 } from 'three/tsl';
import type { TimecycleSample } from './timecycle';

export const CLOUD_NOISE_URL = `${import.meta.env.BASE_URL}assets/nature/clouds.png`;
/** clouds.png is 256 x 256 (scripts/build-nature-assets.mjs). The GPU texture is created at this size once. */
const NOISE_SIZE = 256;

export class CloudLayer {
  /** 0 = clear, 1 = overcast. Every client shares it. */
  readonly coverage = uniform(0.42);
  readonly cirrus = uniform(0.55);
  private light = uniform(new THREE.Color(1, 1, 1));
  private shade = uniform(new THREE.Color(0.5, 0.55, 0.6));
  private sunDir = uniform(new THREE.Vector3(0, 1, 0));
  /** One data texture for the sky's lifetime: empty (no clouds) until the noise pixels arrive, so nothing recompiles
   *  and the GPU texture keeps its size. */
  private noise: THREE.DataTexture;

  constructor(private deterministic: boolean) {
    this.noise = new THREE.DataTexture(new Uint8Array(NOISE_SIZE * NOISE_SIZE * 4), NOISE_SIZE, NOISE_SIZE);
    this.noise.wrapS = this.noise.wrapT = THREE.RepeatWrapping; this.noise.colorSpace = THREE.NoColorSpace;
    this.noise.magFilter = THREE.LinearFilter; this.noise.minFilter = THREE.LinearMipmapLinearFilter;
    this.noise.generateMipmaps = true; this.noise.anisotropy = 4; this.noise.needsUpdate = true;
  }

  /** Browser only: decode the tileable noise PNG into the texture's pixels. */
  async load(url = CLOUD_NOISE_URL) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    const bitmap = await createImageBitmap(await response.blob(), { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
    if (bitmap.width !== NOISE_SIZE || bitmap.height !== NOISE_SIZE) throw new Error(`${url}: expected ${NOISE_SIZE}² noise`);
    const canvas = new OffscreenCanvas(NOISE_SIZE, NOISE_SIZE), context = canvas.getContext('2d')!;
    context.drawImage(bitmap, 0, 0);
    (this.noise.image.data as Uint8Array).set(context.getImageData(0, 0, NOISE_SIZE, NOISE_SIZE).data);
    this.noise.needsUpdate = true;
  }

  /** Sky colour with the clouds over it, for a view direction (unit, world space). */
  composite(view: THREE.Node<'vec3'>, sky: THREE.Node<'vec3'>): THREE.Node<'vec3'> {
    const clock = this.deterministic ? float(0) : time;
    const up = max(view.y, 0);
    // A plane bent down with distance (a cheap earth curvature) so the layer meets the horizon at a finite range.
    const plane = (height: number) => view.xz.mul(float(height).div(up.add(0.08)));
    const low = plane(1.6).mul(0.075).add(vec2(clock.mul(0.0011), clock.mul(0.0004)));
    const sample = (p: THREE.Node<'vec2'>) => {
      const t = texture(this.noise, p);
      const billow = texture(this.noise, p.mul(2.3).add(0.37)).g;
      return t.r.mul(0.62).add(billow.mul(0.38));
    };
    const shape = sample(low);
    const detail = texture(this.noise, low.mul(6.1).add(0.11)).b;
    // The shape value's quantiles (clouds.png): 0.41 at 20 %, 0.51 at 50 %, 0.61 at 80 %, so this threshold covers
    // about `coverage` of the sky; zero-mean detail erodes the edges.
    const threshold = float(0.7).sub(this.coverage.mul(0.4));
    const density = shape.sub(threshold).sub(float(0.5).sub(detail).mul(0.12));
    const horizon = smoothstep(0.015, 0.16, up);
    const alpha = smoothstep(0, 0.09, density).mul(horizon);
    // Denser towards the sun means this texel is in the cloud's own shadow.
    const towardsSun = sample(low.add(normalize(this.sunDir.xz.add(vec2(1e-4, 0))).mul(0.02))).sub(threshold);
    const lit = smoothstep(-0.04, 0.05, towardsSun.sub(density)).oneMinus().mul(0.7).add(0.3);
    const thick = smoothstep(0.02, 0.16, density).mul(0.3).oneMinus();
    const forward = pow(max(dot(view, this.sunDir), 0), 6);
    const edge = alpha.mul(alpha.oneMinus()).mul(4);
    const cloud = mix(this.shade, this.light, lit.mul(thick)).add(this.light.mul(forward).mul(edge.mul(1.2).add(0.15)));
    // Aerial perspective: distant clouds take on the sky's colour near the horizon.
    const aerial = mix(sky, cloud, smoothstep(0.0, 0.35, up).mul(0.55).add(0.45));
    // Cirrus: thin, high and slow, lit almost only by the sun's colour.
    const high = plane(8).mul(0.012).add(vec2(clock.mul(0.0002), clock.mul(-0.0001)));
    const streak = texture(this.noise, high).a;
    const cirrus = smoothstep(0.62, 0.92, streak).mul(this.cirrus).mul(horizon).mul(0.55);
    const withCirrus = mix(sky, add(sky.mul(0.4), this.light.mul(0.75)), cirrus);
    return mix(withCirrus, aerial, alpha);
  }

  apply(sample: TimecycleSample, sunDir: THREE.Vector3) {
    const k = sample.cloudBrightness;
    this.light.value.setRGB(sample.cloudLight[0] * k, sample.cloudLight[1] * k, sample.cloudLight[2] * k);
    this.shade.value.setRGB(sample.cloudShade[0] * k, sample.cloudShade[1] * k, sample.cloudShade[2] * k);
    this.sunDir.value.copy(sunDir);
  }

  dispose() { this.noise.dispose(); }
}

