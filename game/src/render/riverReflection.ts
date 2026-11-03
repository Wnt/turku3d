// W1 of the GTA V water study (loops/techstudy REPORT.md §8): at its lowest preset GTA draws no planar water pass. The
// reduced tier's river reflects instead what the last frame showed, by a screen-space march in its own fragments over
// TRAA's history (render/waterHistory.ts): no second city draw and no extra pass. The river writes no depth (W3), so the
// stored depth holds only what stands above and around the water, never the water itself.
import * as THREE from 'three/webgpu';
import { Break, Fn, If, Loop, bool, cameraFar, cameraNear, float, getScreenPosition, max, min, mix, normalize,
  perspectiveDepthToViewZ, smoothstep, texture, uniform, vec2, vec4 } from 'three/tsl';
import type { WaterHistory } from './waterHistory';

/** March length (m) and samples. Twelve screen-space steps find a bracket, four binary steps refine it: across the
 *  Aura (40-80 m) a step spans about the height of a storey at the far bank. */
export const RIVER_SSR = { reach: 160, steps: 12, refine: 4, minFresnel: 0.035 };

type Vec3 = THREE.Node<'vec3'>;

/** The reflected colour (rgb) and its confidence (a; 0 = sky only) for a ray from `origin` along `direction` (world). */
export function riverScreenReflection(history: WaterHistory & { depth: THREE.DepthTexture; color: THREE.Texture;
  camera: NonNullable<WaterHistory['camera']> }, origin: Vec3, direction: Vec3, fresnel: THREE.Node<'float'>): THREE.Node<'vec4'> {
  const camera = history.camera;
  const view = uniform(new THREE.Matrix4()).onRenderUpdate(() => camera.view);
  const projection = uniform(new THREE.Matrix4()).onRenderUpdate(() => camera.projection);
  const depth = texture(history.depth), color = texture(history.color);
  const near = cameraNear, far = cameraFar;
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
      const hitUV = vec2(0).toVar(), hitDelta = float(1000).toVar();
      Loop(RIVER_SSR.steps, ({ i }) => {
        const t = float(i.add(1)).div(RIVER_SSR.steps).toVar(), sampleUV = startUV.add(deltaUV.mul(t)).toVar();
        const delta = sceneZ(sampleUV).sub(rayZ(t)).toVar();
        // The ray passed behind a stored surface: a bracket.
        If(delta.greaterThan(0), () => {
          low.assign(previous); high.assign(t); hitUV.assign(sampleUV); hitDelta.assign(delta); found.assign(true); Break();
        });
        previous.assign(t);
      });
      If(found, () => {
        Loop(RIVER_SSR.refine, () => {
          const t = low.add(high).mul(0.5).toVar(), sampleUV = startUV.add(deltaUV.mul(t)).toVar();
          const delta = sceneZ(sampleUV).sub(rayZ(t)).toVar();
          If(delta.greaterThan(0), () => { high.assign(t); hitUV.assign(sampleUV); hitDelta.assign(delta); })
            .Else(() => { low.assign(t); });
        });
        // A hit lies on the stored surface, not far behind a thin one (a lamp post in front of the far bank).
        const thickness = max(0.6, rayZ(high).negate().mul(0.04));
        If(hitDelta.lessThan(thickness), () => {
          const border = min(min(hitUV.x, float(1).sub(hitUV.x)), min(hitUV.y, float(1).sub(hitUV.y)));
          const confidence = smoothstep(0.01, 0.08, border).mul(float(1).sub(smoothstep(0.75, 1, high.mul(span))));
          result.assign(vec4(color.sample(hitUV).level(float(0)).rgb, confidence));
        });
      });
    });
    return result;
  })();
}
