// Light coronas: ONE instanced additive draw for every light sprite in the resident window (street lamps today;
// car and window lights join as further groups). It renders in its own pass and is composited after the TRAA
// resolve (render/post.ts), so jitter and history rejection never flicker or smear it. The pass has no depth
// buffer: each fragment compares itself with the scene's resolved depth, so walls hide lamps behind them.
import * as THREE from 'three/webgpu';
import { attribute, cameraFar, cameraNear, cameraViewMatrix, clamp, dot, exp, float, length, max, pass,
  perspectiveDepthToViewZ, positionGeometry, screenUV, select, smoothstep, texture, uniform, varying, vec4 } from 'three/tsl';
import { isPrecompiling } from '../compileModel';
import type { SpriteLight } from './lampField';
import type { QualityTier } from '../quality';

export const SPRITE = {
  /** Output-pixel diameter clamp: distant lights never vanish, near ones bound additive overdraw. */
  minPx: 2, maxPx: 48,
  /** Near fade (metres): close lamps are their emissive heads, not coronas. */
  nearStart: 3, nearEnd: 10,
  /** Corona is pulled this far toward the camera before the depth test, clearing its own lamp head. */
  pull: 0.8,
  /** Fade-in of newly streamed lights, seconds. */
  fadeIn: 1.5,
  /** Capacity of the instance buffers per tier (all resident sprites; the shader skips unlit ones). */
  capacity: { mobile: 1024, low: 1536, medium: 4096, high: 10240 } satisfies Record<QualityTier, number>,
} as const;

/** Mirror of the vertex stage: corona diameter in output pixels. `focal` is P[1][1] * drawing-buffer height / 2. */
export function spritePixels(radius: number, depth: number, focal: number) {
  const physical = 2 * radius * focal / Math.max(depth, 1e-3);
  return Math.min(SPRITE.maxPx, Math.max(SPRITE.minPx, physical));
}

const smooth = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

/** Mirror of the vertex stage: brightness factor of one sprite (night level, day weight, near and load fades). */
export function spriteLevel(night: number, day: number, distance: number, age: number) {
  return Math.max(night, day) * smooth(SPRITE.nearStart, SPRITE.nearEnd, distance) * Math.min(1, Math.max(0, age / SPRITE.fadeIn));
}

export class LightSprites {
  readonly scene = new THREE.Scene();
  readonly mesh: THREE.Mesh<THREE.InstancedBufferGeometry, THREE.NodeMaterial>;
  readonly night = uniform(0);
  readonly capacity: number;
  /** Seconds on this object's clock; birth times use the same clock. */
  private readonly now = uniform(0);
  private readonly projection = uniform(new THREE.Matrix4());
  private readonly focal = uniform(1);
  private readonly viewport = uniform(new THREE.Vector2(1, 1));
  /** The scene's resolved depth; bound by `overlay` (a placeholder keeps the material complete for prebuilds). */
  private readonly sceneDepth = texture(new THREE.DepthTexture(1, 1));
  private readonly centres: THREE.InstancedBufferAttribute;
  private readonly colors: THREE.InstancedBufferAttribute;
  private readonly births: THREE.InstancedBufferAttribute;
  private groups = new Map<string, readonly SpriteLight[]>();
  private born = new Map<string, Map<string, number>>();
  private dirty = false;
  private clock = 0;
  count = 0;
  dropped = 0;

  constructor(tier: QualityTier, private readonly seconds = () => performance.now() / 1000) {
    this.capacity = SPRITE.capacity[tier];
    const geometry = new THREE.InstancedBufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0], 3));
    geometry.setIndex([0, 1, 2, 0, 2, 3]);
    this.centres = new THREE.InstancedBufferAttribute(new Float32Array(this.capacity * 4), 4);
    this.colors = new THREE.InstancedBufferAttribute(new Float32Array(this.capacity * 4), 4);
    this.births = new THREE.InstancedBufferAttribute(new Float32Array(this.capacity), 1);
    for (const a of [this.centres, this.colors, this.births]) a.setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute('lightCentre', this.centres);
    geometry.setAttribute('lightColor', this.colors);
    geometry.setAttribute('lightBorn', this.births);
    // Always at least one (zero-radius) instance: the draw and its pipeline exist from the first frame.
    geometry.instanceCount = 1;
    this.mesh = new THREE.Mesh(geometry, this.material());
    this.mesh.name = 'light-sprites';
    this.mesh.frustumCulled = false;
    this.scene.add(this.mesh);
  }

  private material() {
    const material = new THREE.NodeMaterial();
    material.name = 'light-sprites';
    material.transparent = true; material.depthTest = false; material.depthWrite = false;
    material.blending = THREE.AdditiveBlending; material.side = THREE.DoubleSide;
    material.fog = false; material.lights = false;
    const centre = attribute('lightCentre', 'vec4'), tint = attribute('lightColor', 'vec4'), born = attribute('lightBorn', 'float');
    const corner = positionGeometry.xy;
    const view = cameraViewMatrix.mul(vec4(centre.xyz, 1)).xyz;
    const distance = length(view);
    const pulled = view.mul(max(distance.sub(SPRITE.pull), distance.mul(0.5)).div(max(distance, 1e-3)));
    const depth = pulled.z.negate();
    // Unjittered projection (TRAA's view offset applies only inside the pipeline render).
    const clip = this.projection.mul(vec4(pulled, 1));
    const pixels = clamp(centre.w.mul(2).mul(this.focal).div(max(depth, 1e-3)), SPRITE.minPx, SPRITE.maxPx);
    const level = max(this.night, tint.w)
      .mul(smoothstep(SPRITE.nearStart, SPRITE.nearEnd, distance))
      .mul(clamp(this.now.sub(born).div(SPRITE.fadeIn), 0, 1));
    const visible = level.greaterThan(0.002).and(depth.greaterThan(cameraNear)).and(centre.w.greaterThan(0));
    const size = select(visible, pixels, float(0));
    material.vertexNode = vec4(clip.xy.add(corner.mul(size).div(this.viewport).mul(clip.w)), clip.zw);
    const uv = varying(corner, 'vLightUv');
    const r2 = dot(uv, uv);
    // A small hot core in a wide soft halo, smoothly zero at the quad edge: tone mapping keeps the core white
    // without flattening the whole corona into a disc.
    const edge = clamp(float(1).sub(r2), 0, 1);
    const shape = exp(r2.mul(-30)).add(exp(r2.mul(-4.5)).mul(0.18)).mul(edge).mul(edge);
    const own = varying(depth, 'vLightDepth');
    const sceneZ = perspectiveDepthToViewZ(this.sceneDepth.sample(screenUV).x, cameraNear, cameraFar).negate();
    const unoccluded = smoothstep(own.sub(0.3), own.add(0.3), sceneZ);
    material.fragmentNode = vec4(varying(tint.rgb.mul(level), 'vLightTint').mul(shape).mul(unoccluded), 1);
    return material;
  }

  /** The overlay layer for render/post.ts: an additive HDR texture whose fragments test `sceneDepth`. */
  overlay(renderer: THREE.WebGPURenderer, camera: THREE.Camera, sceneDepth: THREE.Texture) {
    this.sceneDepth.value = sceneDepth;
    const layer = pass(this.scene, camera, { depthBuffer: false });
    const render = layer.updateBefore.bind(layer);
    layer.updateBefore = frame => { if (!isPrecompiling(renderer)) render(frame); };
    return { color: layer.getTextureNode().rgb, resource: layer };
  }

  /** Replace one source's lights (a tile's lamps, later cars or windows). Unchanged positions keep their fade state. */
  setGroup(key: string, lights: readonly SpriteLight[] | null) {
    if (!lights?.length) {
      if (this.groups.delete(key)) this.dirty = true;
      this.born.delete(key);
      return;
    }
    const before = this.born.get(key), births = new Map<string, number>();
    for (const l of lights) {
      const id = `${Math.round(l.x * 10)},${Math.round(l.y * 10)},${Math.round(l.z * 10)}`;
      births.set(id, before?.get(id) ?? this.clock);
    }
    this.born.set(key, births);
    this.groups.set(key, lights); this.dirty = true;
  }

  /** Per frame, before the pipeline renders: camera uniforms, night level and any changed instance data. */
  update(camera: THREE.PerspectiveCamera, size: THREE.Vector2, night: number) {
    this.clock = this.seconds();
    this.now.value = this.clock;
    this.night.value = night;
    this.projection.value.copy(camera.projectionMatrix);
    this.viewport.value.copy(size);
    this.focal.value = camera.projectionMatrix.elements[5] * size.y / 2;
    if (this.dirty) this.upload(camera.position);
  }

  private upload(at: THREE.Vector3) {
    this.dirty = false;
    let all: { light: SpriteLight; born: number }[] = [];
    for (const [key, lights] of this.groups) {
      const births = this.born.get(key)!;
      for (const l of lights) all.push({ light: l, born: births.get(`${Math.round(l.x * 10)},${Math.round(l.y * 10)},${Math.round(l.z * 10)}`) ?? this.clock });
    }
    this.dropped = Math.max(0, all.length - this.capacity);
    if (this.dropped) all = all.sort((a, b) => Math.hypot(a.light.x - at.x, a.light.z - at.z) - Math.hypot(b.light.x - at.x, b.light.z - at.z)).slice(0, this.capacity);
    const c = this.centres.array as Float32Array, k = this.colors.array as Float32Array, b = this.births.array as Float32Array;
    all.forEach(({ light, born }, i) => {
      c.set([light.x, light.y, light.z, light.radius], i * 4);
      k.set([light.r, light.g, light.b, light.day], i * 4);
      b[i] = born;
    });
    if (!all.length) { c.fill(0, 0, 4); k.fill(0, 0, 4); }
    this.count = all.length;
    this.mesh.geometry.instanceCount = Math.max(1, all.length);
    for (const [attribute, size] of [[this.centres, 4], [this.colors, 4], [this.births, 1]] as const) {
      attribute.clearUpdateRanges();
      attribute.addUpdateRange(0, Math.max(1, all.length) * size);
      attribute.needsUpdate = true;
    }
  }

  dispose() { this.mesh.geometry.dispose(); this.mesh.material.dispose(); }
}
