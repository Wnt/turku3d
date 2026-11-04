// W1 of the GTA V water study (loops/techstudy REPORT.md §8): at its lowest preset GTA draws no planar water pass. The
// reduced tier's river reflects instead what the last frame showed, by a screen-space march in its own fragments over
// TRAA's history (render/waterHistory.ts): no second city draw and no extra pass. The river writes no depth (W3), so the
// stored depth holds only what stands above and around the water, never the water itself.
import * as THREE from 'three/webgpu';
import { Break, Fn, If, Loop, bool, cameraFar, cameraNear, dot, float, getScreenPosition, max, min, mix, normalize,
  perspectiveDepthToViewZ, smoothstep, texture, uniform, vec2, vec3, vec4 } from 'three/tsl';
import type { WaterHistory } from './waterHistory';

/** March length (m) and samples. Twelve screen-space steps find a bracket, four binary steps and a secant step refine
 *  it (the secant keeps neighbouring hits continuous: without it lit windows mirrored as stair-stepped blocks). The
 *  reach covers the far bank's hillside trees: from near water the mirror ray rises about 12 degrees and meets them
 *  100-250 m away. A hit fades out over the image border (`border`, UV), towards the end of the march (`far`, share of
 *  the reach) and beyond the thickness limit. A ray that leaves last frame's image without a hit (or a hit fading at
 *  its border) takes the colour where it left (`edge`, at most that weight): what stands at the image border along the
 *  ray, not the bright sky. Four taps `blur` texels apart (more for longer rays) soften the read, stretched `stretch`
 *  times vertically as rippled water stretches reflections; the waves shift the read `ripple` times their offset
 *  (x, y): smaller than the planar pass's, as the march reads a sharp full-resolution image (larger offsets broke lit windows into stepped blocks). */
export const RIVER_SSR = { reach: 400, steps: 12, refine: 4, minFresnel: 0.035, border: [0.002, 0.03], far: [0.8, 1],
  blur: [1.5, 4], stretch: 2.5, ripple: [0.35, 0.6], edge: 0.7, edgeBlur: [8, 3] };

type Vec3 = THREE.Node<'vec3'>;

/** The reflected colour (rgb) and its confidence (a; 0 = sky only) for a ray from `origin` along `direction` (world). */
export function riverScreenReflection(history: WaterHistory & { depth: THREE.DepthTexture; color: THREE.Texture;
  camera: NonNullable<WaterHistory['camera']> }, origin: Vec3, direction: Vec3, fresnel: THREE.Node<'float'>,
  distortion: THREE.Node<'vec2'>): THREE.Node<'vec4'> {
  const camera = history.camera;
  const view = uniform(new THREE.Matrix4()).onRenderUpdate(() => camera.view);
  const projection = uniform(new THREE.Matrix4()).onRenderUpdate(() => camera.projection);
  const depth = texture(history.depth), color = texture(history.color);
  const size = new THREE.Vector2(1, 1);
  const texel = uniform(size).onRenderUpdate(() => {
    const image = history.color.image as { width?: number; height?: number } | undefined;
    return size.set(1 / Math.max(1, image?.width ?? 1), 1 / Math.max(1, image?.height ?? 1));
  });
  const near = cameraNear, far = cameraFar;
  // The projection's focal scales, to place a stored pixel in view space.
  const focalScale = new THREE.Vector2(1, 1);
  const focal = uniform(focalScale).onRenderUpdate(() => focalScale.set(camera.projection.elements[0], camera.projection.elements[5]));
  // Four taps in a vertical streak around `at`, `spread` texels apart.
  const soft = (at: THREE.Node<'vec2'>, spread: THREE.Node<'vec2'>) => {
    const tap = (x: number, y: number) => color.sample(at.add(spread.mul(vec2(x, y * RIVER_SSR.stretch)))).level(float(0)).rgb;
    return tap(-0.75, -1).add(tap(0.75, -0.35)).add(tap(-0.75, 0.35)).add(tap(0.75, 1)).mul(0.25);
  };
  return Fn(() => {
    const result = vec4(0).toVar();
    // Steep views carry almost no reflection (Schlick's 2 %): skip their march.
    If(fresnel.greaterThan(RIVER_SSR.minFresnel), () => {
      const start = view.mul(vec4(origin, 1)).xyz.toVar();
      const ray = normalize(view.mul(vec4(direction, 0)).xyz).toVar();
      const reach = float(RIVER_SSR.reach).toVar();
      // A ray turning towards the camera stops short of its near plane.
      If(ray.z.greaterThan(0), () => { reach.assign(reach.min(near.negate().sub(start.z).div(ray.z).mul(0.98))); });
      const end = start.add(ray.mul(reach));
      const startUV = getScreenPosition(start, projection).toVar();
      const projected = getScreenPosition(end, projection).sub(startUV).toVar();
      // Clip the screen segment to last frame's image.
      const clip = float(1).toVar();
      If(projected.x.greaterThan(0.00001), () => { clip.assign(clip.min(float(0.995).sub(startUV.x).div(projected.x))); });
      If(projected.x.lessThan(-0.00001), () => { clip.assign(clip.min(float(0.005).sub(startUV.x).div(projected.x))); });
      If(projected.y.greaterThan(0.00001), () => { clip.assign(clip.min(float(0.995).sub(startUV.y).div(projected.y))); });
      If(projected.y.lessThan(-0.00001), () => { clip.assign(clip.min(float(0.005).sub(startUV.y).div(projected.y))); });
      const span = clip.clamp(0, 1).toVar();
      const deltaUV = projected.mul(span).toVar();
      // View depth interpolates as 1/z along the screen segment.
      const reciprocalStart = float(1).div(start.z), reciprocalEnd = mix(reciprocalStart, float(1).div(end.z), span);
      const rayZ = (t: THREE.Node<'float'>) => float(1).div(mix(reciprocalStart, reciprocalEnd, t));
      const sceneZ = (coord: THREE.Node<'vec2'>) => perspectiveDepthToViewZ(depth.sample(coord).r, near, far);
      const found = bool(false).toVar(), low = float(0).toVar(), high = float(1).toVar(), previous = float(0).toVar();
      // The depth gap at the bracket's near end: the water surface itself starts in front of the stored bed and walls.
      const lowDelta = sceneZ(startUV).sub(start.z).min(-0.001).toVar(), previousDelta = lowDelta.toVar();
      const hitDelta = float(1000).toVar();
      Loop(RIVER_SSR.steps, ({ i }) => {
        const t = float(i.add(1)).div(RIVER_SSR.steps).toVar(), sampleUV = startUV.add(deltaUV.mul(t)).toVar();
        const delta = sceneZ(sampleUV).sub(rayZ(t)).toVar();
        // The ray passed behind a stored surface: a bracket.
        If(delta.greaterThan(0), () => {
          low.assign(previous); lowDelta.assign(previousDelta); high.assign(t); hitDelta.assign(delta);
          found.assign(true); Break();
        });
        previous.assign(t); previousDelta.assign(delta);
      });
      If(found, () => {
        Loop(RIVER_SSR.refine, () => {
          const t = low.add(high).mul(0.5).toVar(), sampleUV = startUV.add(deltaUV.mul(t)).toVar();
          const delta = sceneZ(sampleUV).sub(rayZ(t)).toVar();
          If(delta.greaterThan(0), () => { high.assign(t); hitDelta.assign(delta); })
            .Else(() => { low.assign(t); lowDelta.assign(delta); });
        });
        // Secant step: where the depth gap crosses zero between the bracket's ends.
        const crossing = low.add(high.sub(low).mul(lowDelta.negate().div(hitDelta.sub(lowDelta).max(0.0001)))).clamp(low, high);
        // A hit lies on the stored surface, not far behind a thin one (a lamp post in front of the far bank). Tree crowns
        // are metres deep and full of gaps: the refined bracket often lands a few metres behind their stored front, so
        // hits fade out between one and three thicknesses instead of falling back to the sky at one.
        const thickness = max(0.6, rayZ(high).negate().mul(0.1));
        If(hitDelta.lessThan(thickness.mul(3)), () => {
          const read = startUV.add(deltaUV.mul(crossing)).add(distortion.mul(vec2(...RIVER_SSR.ripple))).toVar();
          const border = min(min(read.x, float(1).sub(read.x)), min(read.y, float(1).sub(read.y)));
          const travel = crossing.mul(span);
          const confidence = smoothstep(RIVER_SSR.border[0], RIVER_SSR.border[1], border)
            .mul(float(1).sub(smoothstep(RIVER_SSR.far[0], RIVER_SSR.far[1], travel)))
            .mul(float(1).sub(smoothstep(1, 3, hitDelta.div(thickness))));
          const spread = texel.mul(mix(float(RIVER_SSR.blur[0]), float(RIVER_SSR.blur[1]), travel));
          result.assign(vec4(soft(read, spread), confidence));
        });
      });
      // Off the image at a side: the colour where the ray left it fills what the hit leaves (all of a miss). A ray that
      // leaves through the top meets the sky there, which the fallback already shows: it skips the extra reads.
      const leaving = startUV.add(deltaUV).toVar();
      If(span.lessThan(0.999).and(result.a.lessThan(0.999)).and(leaving.x.sub(0.5).abs().greaterThan(0.49)), () => {
        const exit = leaving.clamp(0.01, 0.99).toVar();
        // Only what stands above the water: at an exit on the water itself (or its bed) last frame shows this same
        // reflection, which would copy the sky fallback back in. Sky pixels lie far above and pass.
        const exitZ = sceneZ(exit);
        const exitView = vec3(exit.x.mul(2).sub(1).mul(exitZ.negate()).div(focal.x), float(1).sub(exit.y.mul(2)).mul(exitZ.negate()).div(focal.y), exitZ);
        const height = dot(exitView.sub(start), normalize(view.mul(vec4(0, 1, 0, 0)).xyz));
        const weight = float(RIVER_SSR.edge).mul(float(1).sub(smoothstep(RIVER_SSR.far[0], RIVER_SSR.far[1], span)))
          .mul(smoothstep(0.3, 1.5, height));
        const fill = weight.mul(float(1).sub(result.a));
        const edge = soft(exit, texel.mul(vec2(...RIVER_SSR.edgeBlur)));
        const alpha = result.a.add(fill);
        result.assign(vec4(result.rgb.mul(result.a).add(edge.mul(fill)).div(alpha.max(0.0001)), alpha));
      });
    });
    return result;
  })();
}
