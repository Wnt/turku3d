import type * as THREE from 'three/webgpu';

type Retained = { usedTimes: number };
interface Pipelines {
  _releaseProgram(program: Retained): void;
  _releasePipeline(pipeline: Retained): void;
  dispose(): void;
}

/** r186 otherwise forgets a program as soon as its last draw leaves. Keep a bounded idle cache
 * of compiled code/pipelines, never render objects, bindings, tile geometry or textures. */
export function retainPrograms(pipelines: Pipelines, limit = 256) {
  const cleanups: (() => void)[] = [];
  for (const name of ['_releaseProgram', '_releasePipeline'] as const) {
    const release = pipelines[name].bind(pipelines), idle = new Set<Retained>();
    pipelines[name] = value => {
      idle.delete(value); idle.add(value);
      while (idle.size > limit) {
        const oldest = idle.values().next().value!;
        idle.delete(oldest);
        if (oldest.usedTimes === 0) release(oldest);
      }
    };
    cleanups.push(() => { for (const value of idle) if (value.usedTimes === 0) release(value); idle.clear(); });
  }
  const dispose = pipelines.dispose.bind(pipelines);
  pipelines.dispose = () => { for (const cleanup of cleanups) cleanup(); dispose(); };
}

interface NodeState { usedTimes: number }
interface RenderObjectLike { isRenderObject?: boolean }
interface Nodes {
  nodeBuilderCache: Map<number, NodeState>;
  get(object: object): { nodeBuilderState?: NodeState };
  getForRenderCacheKey(renderObject: RenderObjectLike): number;
  delete(object: object): unknown;
  dispose(): void;
}

/** r186 also drops a draw's node-builder state (the JS shader build, 20–80 ms) when its last render object goes
 * (NodeManager.delete), so a program family that is briefly unused rebuilds inside the frame that draws it next:
 * a vegetation pool replaced as it grows, a facade component whose only tile left, a dissolve twin. Keep the most
 * recent idle states (bounded) so a returning draw finds its build; r186 already shares a state between all live
 * draws with the same key, so a retained state is reused exactly as a concurrently live one would be. */
export function retainNodeStates(nodes: Nodes, limit = 512) {
  const idle = new Map<number, NodeState>();
  const remove = nodes.delete.bind(nodes);
  nodes.delete = (object: object) => {
    const renderObject = object as RenderObjectLike;
    const state = renderObject.isRenderObject ? nodes.get(object).nodeBuilderState : undefined;
    const key = state ? nodes.getForRenderCacheKey(renderObject) : 0;
    const result = remove(object);
    if (state && state.usedTimes === 0 && !nodes.nodeBuilderCache.has(key)) {
      nodes.nodeBuilderCache.set(key, state);
      idle.delete(key); idle.set(key, state);
      for (const [oldKey, oldState] of idle) {
        if (idle.size <= limit) break;
        idle.delete(oldKey);
        if (oldState.usedTimes === 0 && nodes.nodeBuilderCache.get(oldKey) === oldState) nodes.nodeBuilderCache.delete(oldKey);
      }
    }
    return result;
  };
  const dispose = nodes.dispose.bind(nodes);
  nodes.dispose = () => { idle.clear(); dispose(); };
  /** Forgets every idle state (still-used ones stay): their node graphs, and whatever those reference, can go. */
  const flush = () => {
    for (const [key, state] of idle) if (state.usedTimes === 0 && nodes.nodeBuilderCache.get(key) === state) nodes.nodeBuilderCache.delete(key);
    idle.clear();
  };
  return { idle, flush };
}

interface Bindings {
  deleteForRender(renderObject: { _bindings?: unknown }): void;
  delete(object: object): unknown;
}

/** r186 releases a disposed draw's bind groups through `renderObject.getBindings()`, which BUILDS the draw's node
 * program (20-80 ms of JS per pass) when it has none, only to destroy the bind groups it just made. A render object
 * whose program was never built (a draw deferred by render/asyncNodeBuilds.ts and dropped, a hidden pool batch in a
 * pass it never drew in) therefore cost a full synchronous build to dispose: the fleet's batch swap
 * (actors/fleet/visuals.ts loadNow) froze city.lab for 1.35 s that way on the M1 Max (framecheck mac-base-1, 1.77 s
 * frame). Without bindings there is nothing to destroy, so only its data entry goes. */
export function releaseUnbuiltBindings(bindings: Bindings) {
  const release = bindings.deleteForRender.bind(bindings);
  bindings.deleteForRender = renderObject => {
    if (renderObject._bindings == null) { bindings.delete(renderObject); return; }
    release(renderObject);
  };
}

export function retainRendererPrograms(renderer: THREE.WebGPURenderer) {
  const internal = renderer as unknown as { _pipelines: Pipelines; _nodes: Nodes; _bindings: Bindings };
  retainPrograms(internal._pipelines);
  releaseUnbuiltBindings(internal._bindings);
  return retainNodeStates(internal._nodes);
}
