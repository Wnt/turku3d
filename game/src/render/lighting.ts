// The session's fixed light set. Adding, removing or hiding a light changes r186's lights cache key and
// recompiles every lit material, so lights are created once at startup and only their uniforms change.
import type * as THREE from 'three/webgpu';
import { DynamicLighting } from 'three/addons/lighting/DynamicLighting.js';
import DynamicLightsNode from 'three/addons/tsl/lighting/DynamicLightsNode.js';
import PointLightDataNode from 'three/addons/tsl/lighting/data/PointLightDataNode.js';

export interface LightSlots { point: number }

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

type Builder = THREE.NodeBuilder & { context: { materialLightings?: THREE.LightingNode[] } };
type LightNodeClass = new (light: THREE.Light) => THREE.LightingNode;
const perLightNodes = new WeakMap<THREE.Light, THREE.LightingNode>();
const isHemisphere = (light: THREE.Light) => (light as THREE.HemisphereLight).isHemisphereLight === true;

/**
 * Two corrections to r186's DynamicLightsNode, both found in browser renders:
 * - It builds its list from scene lights only and drops the material lightings the base LightsNode merges in (scene
 *   IBL, light maps, GTAO and each material's aoNode). They are appended again, after the lights, as the base orders them.
 * - Its batched hemisphere loop cost about 2 ms of GPU per frame on an M1 Max at medium, by day, for one light.
 *   The always-present hemisphere light therefore uses r186's ordinary per-light node (render-group uniforms).
 */
class GameLightsNode extends DynamicLightsNode {
  override setupLightsNode(builder: Builder) {
    const self = this as unknown as { _lights: THREE.Light[] };
    const all = self._lights;
    self._lights = all.filter(light => !isHemisphere(light));
    let batched: THREE.LightingNode[];
    try { batched = super.setupLightsNode(builder); } finally { self._lights = all; }
    const direct = all.filter(isHemisphere).map(light => {
      let node = perLightNodes.get(light);
      if (!node) {
        const LightNode = builder.renderer.library.getLightNodeClass(light.constructor as never) as unknown as LightNodeClass;
        perLightNodes.set(light, node = new LightNode(light));
      }
      return node;
    });
    return [...batched, ...direct, ...(builder.context.materialLightings ?? [])];
  }
}

/**
 * r186 DynamicLighting (batched uniform arrays; light counts never recompile) with point slots that exist from the
 * first compile, so lamps can switch on later without new pipelines. The shadowed sun and the hemisphere ("artificial
 * ambient") light keep per-light paths. Idle point slots still cost about 1 ms of GPU on an M1 Max at medium (the
 * loop's code is compiled in), so mobile and low have none.
 * Budget: every batched light type adds uniform buffers to each lit fragment stage (point 3, spot 4) and WebGPU
 * guarantees only 12 per stage, so spot slots need a packed data node before they can join.
 */
export class GameLighting extends DynamicLighting {
  constructor(readonly slots: LightSlots) {
    super({ maxDirectionalLights: 1, maxHemisphereLights: 1, maxPointLights: Math.max(1, slots.point), maxSpotLights: 1 });
  }

  override createNode(lights: THREE.Light[] = []) {
    const node = new GameLightsNode(this.options);
    const data = (node as unknown as { _dataNodes: Map<string, THREE.Node> })._dataNodes;
    if (this.slots.point > 0) data.set('PointLight', new ActivePointLights(this.slots.point));
    return node.setLights(lights);
  }
}
