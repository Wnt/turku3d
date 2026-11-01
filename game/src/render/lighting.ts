// The session's fixed light set. Adding, removing or hiding a light changes r186's lights cache key and
// recompiles every lit material, so lights are created once at startup and only their uniforms change.
import type * as THREE from 'three/webgpu';
import { DynamicLighting } from 'three/addons/lighting/DynamicLighting.js';
import DynamicLightsNode from 'three/addons/tsl/lighting/DynamicLightsNode.js';
import PointLightDataNode from 'three/addons/tsl/lighting/data/PointLightDataNode.js';
import SpotLightDataNode from 'three/addons/tsl/lighting/data/SpotLightDataNode.js';

export interface LightSlots { point: number; spot: number }

type DataNodeClass = new (maxCount: number) => THREE.Node;
type Frame = Parameters<THREE.Node['update']>[0];
interface LightData { _lights: THREE.Light[]; update(frame: Frame): boolean | undefined }

/** Pack only lights with intensity > 0 into the uniform arrays. The shader loop is bounded by the active count,
 *  so idle slots cost nothing by day while the light set, and therefore every cache key, stays fixed. */
function activeOnly(Base: DataNodeClass): DataNodeClass {
  const update = (Base.prototype as unknown as LightData).update;
  return class extends Base {
    private all: THREE.Light[] = [];
    private active: THREE.Light[] = [];
    setLights(lights: THREE.Light[]) { this.all = lights; return this; }
    override update(frame: Frame) {
      this.active.length = 0;
      for (const light of this.all) if (light.intensity > 0) this.active.push(light);
      (this as unknown as LightData)._lights = this.active;
      return update.call(this, frame);
    }
  };
}
const ActivePointLights = activeOnly(PointLightDataNode);
const ActiveSpotLights = activeOnly(SpotLightDataNode);

/**
 * r186 DynamicLighting (batched uniform arrays; light counts never recompile) with point and spot slots that exist
 * from the first compile, so lamps and headlights can switch on later without new pipelines. The shadowed sun
 * keeps its per-light path; the hemisphere ("artificial ambient") light is batched.
 */
export class GameLighting extends DynamicLighting {
  constructor(readonly slots: LightSlots) {
    super({ maxDirectionalLights: 1, maxHemisphereLights: 1,
      maxPointLights: Math.max(1, slots.point), maxSpotLights: Math.max(1, slots.spot) });
  }

  override createNode(lights: THREE.Light[] = []) {
    const node = new DynamicLightsNode(this.options);
    const data = (node as unknown as { _dataNodes: Map<string, THREE.Node> })._dataNodes;
    if (this.slots.point > 0) data.set('PointLight', new ActivePointLights(this.slots.point));
    if (this.slots.spot > 0) data.set('SpotLight', new ActiveSpotLights(this.slots.spot));
    return node.setLights(lights);
  }
}
