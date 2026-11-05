import type * as THREE from 'three/webgpu';

type Hook = (builder: unknown, value: unknown) => void;
interface Debug { onNodeBuilderCreated?: Hook | null }

const hooks = new WeakMap<object, Set<Hook>>();

/**
 * Adds a renderer.debug.onNodeBuilderCreated hook and returns its removal. One dispatcher per renderer calls every
 * active hook: compiles that chained their hook onto the previous one and restored it only when still on top left
 * every interleaved hook installed for good (two preparation queues and the first-use builds overlap), each keeping
 * its mesh and geometry alive (a heap snapshot after four city switches: 1.3 GB more array buffers, held by a chain
 * of such hooks).
 */
export function addNodeBuilderHook(renderer: THREE.WebGPURenderer, hook: Hook): () => void {
  let set = hooks.get(renderer);
  if (!set) {
    const active = new Set<Hook>();
    set = active;
    hooks.set(renderer, active);
    const debug = (renderer as unknown as { debug: Debug }).debug;
    const original = debug.onNodeBuilderCreated ?? null;
    debug.onNodeBuilderCreated = (builder, value) => {
      for (const each of [...active]) each(builder, value);
      original?.(builder, value);
    };
  }
  set.add(hook);
  return () => { set!.delete(hook); };
}
