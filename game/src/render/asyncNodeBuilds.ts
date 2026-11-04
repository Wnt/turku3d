import type * as THREE from 'three/webgpu';
import { afterRender } from './afterRender';
import { streamTrace } from '../telemetry/streamTrace';

/** r186 builds a draw's node program (TSL to WGSL, 20–80 ms of JS) synchronously the first time the draw needs it
 * (`_renderObjectDirect` -> `NodeManager.getForRender`). Preparation builds every program it can foresee ahead, but
 * some first draws still reach a frame unprepared: a building's own facade-component materials when its block swaps
 * to HD, a pool mesh of a newly met fence type. Each such build stalled its frame by 50–110 ms (drive replay
 * 20261104-182633: 13 of 23 in-play builds; the other 10 were freed builds, render/programCache.ts).
 *
 * While `active()` holds (the per-frame post pipeline), a world draw whose program is neither built nor cached is
 * instead skipped and its program built after the frame with `getForRenderAsync`, which yields between build stages;
 * the draw appears a few frames later, like a first-use pipeline (render/asyncPipelines.ts). This is the commercial
 * engines' rule: never compile inside a frame. Effect quads and every other render keep the synchronous path. */
type RenderObject = { isRenderObject: true; object: THREE.Object3D; material: THREE.Material; context: unknown };
interface Nodes {
  nodeBuilderCache: Map<number, unknown>;
  get(object: object): { nodeBuilderState?: unknown };
  getForRenderCacheKey(renderObject: RenderObject): number;
  getForRenderAsync(renderObject: RenderObject): Promise<unknown>;
}
interface Objects {
  get(object: THREE.Object3D, material: THREE.Material, scene: THREE.Scene, camera: THREE.Camera, lightsNode: unknown,
    renderContext: unknown, clippingContext: unknown, passId?: string): RenderObject;
}
interface Internals {
  _nodes: Nodes; _objects: Objects; _currentRenderContext: unknown; _renderTarget: unknown; _mrt: unknown;
  contextNode: unknown; debug: { onNodeBuilderCreated: ((builder: unknown, draw: unknown) => void) | null };
}
export type DirectDraw = (object: THREE.Object3D, material: THREE.Material, scene: THREE.Scene, camera: THREE.Camera,
  lightsNode: unknown, group: unknown, clippingContext: unknown, passId?: string) => void;

export function asyncFirstUseBuilds(renderer: THREE.WebGPURenderer, active: () => boolean) {
  const internal = renderer as unknown as Internals;
  const pending = new Map<number, Promise<void>>();
  let deferred = 0;
  /** True when the draw may go ahead now; false when it is skipped while its program builds after the frame. */
  const ready: (...args: Parameters<DirectDraw>) => boolean = (object, material, scene, camera, lightsNode, _group, clippingContext, passId) => {
    if (!active() || (object as { isQuadMesh?: boolean }).isQuadMesh === true) return true;
    const nodes = internal._nodes;
    const renderObject = internal._objects.get(object, material, scene, camera, lightsNode, internal._currentRenderContext, clippingContext, passId);
    if (nodes.get(renderObject).nodeBuilderState !== undefined) return true;
    const key = nodes.getForRenderCacheKey(renderObject);
    if (nodes.nodeBuilderCache.has(key)) return true;
    if (!pending.has(key)) {
      deferred++;
      // The build reads renderer pass state again after each yield: pin this draw's pass, as compileModel does.
      const pass = { target: internal._renderTarget, mrt: internal._mrt, context: internal.contextNode, renderContext: internal._currentRenderContext };
      const queued = performance.now();
      const built = new Promise<void>(resolve => afterRender(resolve)).then(async () => {
        const previous = internal.debug.onNodeBuilderCreated;
        internal.debug.onNodeBuilderCreated = (builder, draw) => {
          if ((draw as RenderObject) === renderObject) {
            (builder as { renderer: unknown }).renderer = new Proxy(renderer, { get(source, name, receiver) {
              if (name === '_renderTarget') return pass.target;
              if (name === '_mrt') return pass.mrt;
              if (name === 'contextNode') return pass.context;
              if (name === '_currentRenderContext') return pass.renderContext;
              return Reflect.get(source, name, receiver);
            } });
          }
          previous?.(builder, draw);
        };
        const started = performance.now();
        let promise: Promise<unknown>;
        try { promise = nodes.getForRenderAsync(renderObject); }
        finally { internal.debug.onNodeBuilderCreated = previous; }
        await promise;
        streamTrace.event('gfx.deferred', undefined, { material: material.name || material.type, wait: Math.round(started - queued), ms: Math.round(performance.now() - started) });
      }).catch(error => console.warn(`[async node build] ${error}`)).finally(() => pending.delete(key));
      pending.set(key, built);
    }
    return false;
  };
  return {
    ready,
    /** Draws deferred so far (each built after its frame instead of inside it). */
    get deferred() { return deferred; },
    /** Resolves once every deferred build so far is done. */
    settled: async () => { while (pending.size) await Promise.all([...pending.values()]); },
  };
}
