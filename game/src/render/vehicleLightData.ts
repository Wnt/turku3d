import * as T from 'three/webgpu';
import { Loop, getDistanceAttenuation, positionView, renderGroup, smoothstep, uniform, uniformArray, vec3, select, min, max, step } from 'three/tsl';
import type Node from 'three/src/nodes/core/Node.js';

type Frame = Parameters<T.Node['update']>[0];
const vector = new T.Vector3(), direction = new T.Vector3();
interface LightingContext {
  positionView?: Node<'vec3'>;
  lightingModel: { direct(data: Record<string, unknown>, builder: T.NodeBuilder): void };
  reflectedLight: { directDiffuse: ReturnType<typeof vec3>; directSpecular: ReturnType<typeof vec3> };
}
/** Two vehicle beams in one packed UBO. Zero active lights means a zero-iteration fragment loop.
 * Five conservative angular depth bins stop light behind collision walls without shadow maps. */
export class VehicleLightData extends T.Node {
  private lights: T.SpotLight[] = [];
  private values = Array.from({ length: 12 }, () => new T.Vector4());
  private packed = uniformArray(this.values, 'vec4').setGroup(renderGroup);
  private count = uniform(0, 'int').setGroup(renderGroup);
  constructor() { super(); this.updateType = T.NodeUpdateType.RENDER; }
  setLights(lights: T.Light[]) { this.lights = lights as T.SpotLight[]; return this; }
  override update({ camera }: Frame): boolean | undefined {
    if (!camera) return;
    let count = 0;
    for (const light of this.lights) {
      if (light.intensity <= 0 || count === 2) continue;
      const at = count++ * 6, q = light.parent!.getWorldQuaternion(new T.Quaternion());
      vector.setFromMatrixPosition(light.matrixWorld).applyMatrix4(camera.matrixWorldInverse);
      this.values[at].set(vector.x, vector.y, vector.z, light.distance);
      for (const [slot, axis] of [[1, [1,0,0]], [2,[0,0,1]], [3,[0,1,0]]] as const) {
        direction.set(axis[0], axis[1], axis[2]).applyQuaternion(q).transformDirection(camera.matrixWorldInverse);
        this.values[at + slot].set(direction.x, direction.y, direction.z, 0);
      }
      this.values[at + 1].w = light.userData.mode ?? 1;
      const depths: number[] = light.userData.depths ?? [145,145,145,145,145];
      this.values[at + 3].w = depths[4];
      this.values[at + 4].set(light.color.r * light.intensity, light.color.g * light.intensity, light.color.b * light.intensity, light.decay);
      this.values[at + 5].fromArray(depths);
    }
    this.count.value = count;
    return undefined;
  }
  override setup(builder: T.NodeBuilder) {
    const context = builder.context as unknown as LightingContext;
    const surface = context.positionView ?? positionView;
    const diffuse = vec3(0).toVar(), specular = vec3(0).toVar();
    Loop(this.count, ({ i }) => {
      const at = i.mul(6);
      const row = (offset: number) => this.packed.element(at.add(offset)) as unknown as Node<'vec4'>;
      const pos = row(0), forward = row(1), right = row(2), up = row(3), color = row(4), depths = row(5);
      const delta = surface.sub(pos.xyz), along = max(delta.dot(forward.xyz), .01);
      const h = delta.dot(right.xyz).div(along), v = delta.dot(up.xyz).div(along);
      const high = forward.w.greaterThan(1.5);
      const vertical = select(high, v.sub(.01).div(.22), v.add(.04).div(.24));
      const ellipse = h.div(select(high, .48, .42)).pow(2).add(vertical.pow(2)).sqrt();
      const cone = smoothstep(.65, 1, ellipse).oneMinus();
      const cutoff = select(high, 1, smoothstep(h.max(0).mul(.14).sub(.025), h.max(0).mul(.14).sub(.005), v).oneMinus());
      // Min adjacent fan samples rather than interpolating a hole around the edge of an occluder.
      const depth = select(h.lessThan(-.21), min(depths.x, depths.y),
        select(h.lessThan(0), min(depths.y, depths.z), select(h.lessThan(.21), min(depths.z, depths.w), min(depths.w, up.w))));
      const distance = delta.length();
      const occlusion = smoothstep(depth.add(.2), depth.add(.7), distance).oneMinus();
      const attenuation = getDistanceAttenuation({ lightDistance: distance, cutoffDistance: pos.w, decayExponent: color.w });
      const lightColor = color.xyz.mul(cone).mul(cutoff).mul(occlusion).mul(attenuation).mul(step(.05, delta.dot(forward.xyz)));
      context.lightingModel.direct({ lightDirection: delta.negate().normalize(), lightColor,
        lightNode: { light: {}, shadowNode: null }, reflectedLight: { directDiffuse: diffuse, directSpecular: specular } }, builder);
    });
    context.reflectedLight.directDiffuse.addAssign(diffuse);
    context.reflectedLight.directSpecular.addAssign(specular);
    return null;
  }
}
