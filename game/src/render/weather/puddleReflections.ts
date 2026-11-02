// Bounded screen-space puddles from the existing scene buffers: no second city draw.
import * as THREE from 'three/webgpu';
import { Break, Fn, If, Loop, bool, dot, exp, float, getScreenPosition, getViewPosition, max, min, mix, normalize,
  perspectiveDepthToViewZ, pow, reflect, smoothstep, uniform, uv, vec2, vec4, convertToTexture } from 'three/tsl';
import { isPrecompiling } from '../compileModel';
import { puddleMask, weatherSurface, weatherUniforms } from './surfaces';

type Buffer = THREE.TextureNode<'vec4'>;
type Coord = THREE.Node<'vec2'>;
export class PuddleReflections {
  readonly gain = uniform(1);
  readonly texture: ReturnType<typeof convertToTexture>;
  private readonly near = uniform(.1);
  private readonly far = uniform(1000);
  private readonly dimensions = uniform(new THREE.Vector2(1, 1));
  private readonly projection = uniform(new THREE.Matrix4());
  private readonly inverseProjection = uniform(new THREE.Matrix4());
  private readonly world = uniform(new THREE.Matrix4());
  constructor(renderer: THREE.WebGPURenderer, beauty: Buffer, depth: Buffer,
    normals: (coord: Coord) => THREE.Node<'vec3'>, receivers: (coord: Coord) => THREE.Node<'float'>,
    camera: THREE.PerspectiveCamera, baseContext: THREE.WebGPURenderer['contextNode']) {
    this.projection.value = camera.projectionMatrix;
    this.inverseProjection.value = camera.projectionMatrixInverse;
    this.world.value = camera.matrixWorld;
    const near = this.near, far = this.far;
    near.value = camera.near; far.value = camera.far;
    const effect = Fn(() => {
      const coord = uv(), sceneDepth = depth.sample(coord).level(float(0)).r;
      const output = vec4(0).toVar();
      If(weatherUniforms.wetness.greaterThan(.35).and(this.gain.greaterThan(0))
        .and(sceneDepth.lessThan(.99999)).and(receivers(coord).greaterThan(.5)), () => {
        const origin = getViewPosition(coord, sceneDepth, this.inverseProjection).toVar();
        const normal = normalize(normals(coord)).toVar();
        const worldPosition = this.world.mul(vec4(origin, 1)).xyz;
        const worldNormal = this.world.mul(vec4(normal, 0)).xyz;
        const field = weatherSurface(worldPosition);
        const wetness = weatherUniforms.wetness.mul(field.x);
        const receiver = puddleMask(worldPosition, wetness, smoothstep(.8, .98, worldNormal.y), 1, field)
          .mul(float(1).sub(smoothstep(55, 90, origin.length())));
        If(receiver.greaterThan(.02), () => {
          const direction = reflect(normalize(origin), normal).toVar();
          const start = origin.add(normal.mul(.06));
          const reach = float(45).toVar();
          If(direction.z.greaterThan(0), () => { reach.assign(reach.min(near.negate().sub(start.z).div(direction.z).mul(.98))); });
          const end = start.add(direction.mul(reach));
          const startUV = getScreenPosition(start, this.projection).toVar();
          const projected = getScreenPosition(end, this.projection).sub(startUV).toVar();
          const clip = float(1).toVar();
          If(projected.x.greaterThan(.00001), () => { clip.assign(clip.min(float(.995).sub(startUV.x).div(projected.x))); });
          If(projected.x.lessThan(-.00001), () => { clip.assign(clip.min(float(.005).sub(startUV.x).div(projected.x))); });
          If(projected.y.greaterThan(.00001), () => { clip.assign(clip.min(float(.995).sub(startUV.y).div(projected.y))); });
          If(projected.y.lessThan(-.00001), () => { clip.assign(clip.min(float(.005).sub(startUV.y).div(projected.y))); });
          const deltaUV = projected.mul(clip.clamp(0, 1));
          const reciprocalStart = float(1).div(start.z), reciprocalEnd = mix(reciprocalStart, float(1).div(end.z), clip.clamp(0, 1));
          const rayZ = (t: THREE.Node<'float'>) => float(1).div(mix(reciprocalStart, reciprocalEnd, t));
          const found = bool(false).toVar(), low = float(0).toVar(), high = float(1).toVar();
          const previous = float(0).toVar(), previousDelta = float(-1).toVar();
          const hitUV = vec2(0).toVar(), hitDelta = float(1000).toVar();
          // Twenty projected-screen samples find a bracket. Four binary refinements share the same 24-fetch budget.
          Loop(20, ({ i }) => {
            const t = float(i.add(1)).div(20).toVar(), sampleUV = startUV.add(deltaUV.mul(t)).toVar();
            const sampled = depth.sample(sampleUV).level(float(0)).r.toVar();
            const delta = perspectiveDepthToViewZ(sampled, near, far).sub(rayZ(t)).toVar();
            const facing = dot(direction, normals(sampleUV)).lessThan(0);
            If(sampled.lessThan(.99999).and(previousDelta.lessThanEqual(0)).and(delta.greaterThan(0)).and(facing), () => {
              low.assign(previous); high.assign(t); hitUV.assign(sampleUV); hitDelta.assign(delta); found.assign(true); Break();
            });
            previous.assign(t); previousDelta.assign(facing.select(delta, float(-1)));
          });
          If(found, () => {
            Loop(4, () => {
              const t = low.add(high).mul(.5).toVar(), sampleUV = startUV.add(deltaUV.mul(t)).toVar();
              const sampled = depth.sample(sampleUV).level(float(0)).r.toVar();
              const delta = perspectiveDepthToViewZ(sampled, near, far).sub(rayZ(t)).toVar();
              If(sampled.lessThan(.99999).and(delta.greaterThan(0)).and(dot(direction, normals(sampleUV)).lessThan(0)), () => {
                high.assign(t); hitUV.assign(sampleUV); hitDelta.assign(delta);
              }).Else(() => { low.assign(t); });
            });
            If(hitDelta.lessThan(.2).and(dot(direction, normals(hitUV)).lessThan(0)), () => {
              const border = min(min(hitUV.x, float(1).sub(hitUV.x)), min(hitUV.y, float(1).sub(hitUV.y)));
              const fresnel = pow(float(1).sub(dot(normalize(origin.negate()), normal).max(0)), 5).mul(.97).add(.03);
              const weight = receiver.mul(fresnel).mul(smoothstep(.01, .09, border)).mul(this.gain).mul(.85);
              output.assign(vec4(beauty.sample(hitUV).level(float(0)).rgb.mul(weight), weight));
            });
          });
        });
      });
      return output;
    })();
    this.texture = convertToTexture(effect, null, null, { depthBuffer: false, resolutionScale: .5 });
    this.texture.name = 'weather puddles 20+4 rgb-v3';
    const update = this.texture.updateBefore.bind(this.texture);
    let dry = false;
    this.texture.updateBefore = frame => {
      if (isPrecompiling(renderer)) return;
      const inactive = weatherUniforms.wetness.value <= .35 || this.gain.value === 0;
      if (dry && inactive) return;
      near.value = camera.near; far.value = camera.far;
      const previous = renderer.contextNode; renderer.contextNode = baseContext;
      try {
        update(frame); dry = inactive;
        const target = (this.texture as unknown as { renderTarget: THREE.RenderTarget }).renderTarget;
        this.dimensions.value.set(target.width, target.height);
      } finally { renderer.contextNode = previous; }
    };
  }
  /** Bilateral reconstruction keeps half-resolution reflections on the same material/depth/normal receiver. */
  reconstruct(depth: Buffer, normals: (coord: Coord) => THREE.Node<'vec3'>,
    receivers: (coord: Coord) => THREE.Node<'float'>) {
    const near = this.near, far = this.far;
    return Fn(() => {
      const coord = uv(), ownDepth = depth.sample(coord).level(float(0)).r;
      const result = vec4(0).toVar();
      If(ownDepth.lessThan(.99999).and(receivers(coord).greaterThan(.5)), () => {
        const ownNormal = normalize(normals(coord)), ownZ = perspectiveDepthToViewZ(ownDepth, near, far);
        const pixel = coord.mul(this.dimensions).sub(.5), base = pixel.floor(), fraction = pixel.fract();
        const sum = vec4(0).toVar(), total = float(0).toVar();
        for (const [x, y] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
          const sampleUV = base.add(vec2(x + .5, y + .5)).div(this.dimensions);
          const sampleZ = perspectiveDepthToViewZ(depth.sample(sampleUV).level(float(0)).r, near, far);
          const bilinear = (x ? fraction.x : float(1).sub(fraction.x)).mul(y ? fraction.y : float(1).sub(fraction.y));
          const weight = bilinear.mul(exp(sampleZ.sub(ownZ).abs().div(max(.1, ownZ.abs().mul(.025))).negate()))
            .mul(dot(ownNormal, normalize(normals(sampleUV))).max(0).pow(8)).mul(receivers(sampleUV));
          sum.addAssign(this.texture.sample(sampleUV).level(float(0)).mul(weight)); total.addAssign(weight);
        }
        const worldPosition = this.world.mul(vec4(getViewPosition(coord, ownDepth, this.inverseProjection), 1)).xyz;
        const worldNormal = this.world.mul(vec4(ownNormal, 0)).xyz, field = weatherSurface(worldPosition);
        const wetness = weatherUniforms.wetness.mul(field.x);
        const pool = puddleMask(worldPosition, wetness, smoothstep(.8, .98, worldNormal.y), 1, field);
        result.assign(sum.div(max(total, .00001)).mul(smoothstep(.005, .04, pool)));
      });
      return result;
    })();
  }
  dispose() { this.texture.dispose(); }
}
