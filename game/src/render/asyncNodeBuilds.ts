import type * as THREE from 'three/webgpu';
import { addNodeBuilderHook } from './nodeBuilderHooks';
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
 * engines' rule: never compile inside a frame. Effect quads and every other render keep the synchronous path.
 *
 * The deferred builds run one at a time, each starting on its own `scheduler.yield()` turn (render/yieldPacer.ts
 * paces those). Started together after their frame, the boot warm-up's few hundred builds ran their synchronous
 * first stages in one task and the rest back to back: city.lab boot and join froze the page 22-25 s (Jonni's
 * 2026-11-05 drive; 9.6 s of it in those synchronous starts). */
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

/** A turn of the paced scheduler (render/yieldPacer.ts), or a task hop where scheduler.yield is missing. */
const turn = () => {
  const scheduler = (globalThis as { scheduler?: { yield?: () => Promise<void> } }).scheduler;
  return scheduler?.yield ? scheduler.yield() : new Promise<void>(resolve => setTimeout(resolve, 0));
};

/** A deferred build: when its program was first and last asked for by a frame's draw, and how far from the camera. */
interface Job { key: number; run: () => Promise<void>; drop: () => void; firstAsked: number; lastAsked: number; distance: number }
/** A job asked for within this many ms is visible now (a draw of the current frames is waiting for it). */
export const HOT_MS = 500;
const STATS_MS = 30_000;

const percentile = (values: number[], p: number) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return Math.round(sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * p) - 1))]);
};

export function asyncFirstUseBuilds(renderer: THREE.WebGPURenderer, active: () => boolean, now = () => performance.now()) {
  const internal = renderer as unknown as Internals;
  const pending = new Map<number, Promise<void>>();
  const waiting = new Map<number, Job>();
  let draining: Promise<void> | null = null;
  let built = 0, dropped = 0;
  /** Finished builds: how long each waited from its first ask, and whether a draw still wanted it (visible). */
  const waits: { at: number; ms: number; visible: boolean }[] = [];
  /** The next job: the oldest first ask (FIFO). */
  const next = (): Job | undefined => waiting.values().next().value;
  const drain = () => draining ??= (async () => {
    await new Promise<void>(resolve => afterRender(resolve));
    for (let job = next(); job; job = next()) {
      waiting.delete(job.key);
      await job.run();
      await turn();
    }
  })().finally(() => { draining = null; if (waiting.size) void drain(); });
  let deferred = 0;
  /** True when the draw may go ahead now; false when it is skipped while its program builds after the frame. */
  const ready: (...args: Parameters<DirectDraw>) => boolean = (object, material, scene, camera, lightsNode, _group, clippingContext, passId) => {
    if (!active() || (object as { isQuadMesh?: boolean }).isQuadMesh === true) return true;
    const nodes = internal._nodes;
    const renderObject = internal._objects.get(object, material, scene, camera, lightsNode, internal._currentRenderContext, clippingContext, passId);
    if (nodes.get(renderObject).nodeBuilderState !== undefined) return true;
    const key = nodes.getForRenderCacheKey(renderObject);
    if (nodes.nodeBuilderCache.has(key)) return true;
    const at = now(), o = object.matrixWorld?.elements, c = camera.matrixWorld?.elements;
    const distance = o && c ? Math.hypot(o[12] - c[12], o[13] - c[13], o[14] - c[14]) : Infinity;
    const job = waiting.get(key);
    if (job) { job.lastAsked = at; job.distance = Math.min(distance, job.distance); return false; }
    if (!pending.has(key)) {
      deferred++;
      // The build reads renderer pass state again after each yield: pin this draw's pass, as compileModel does.
      const pass = { target: internal._renderTarget, mrt: internal._mrt, context: internal.contextNode, renderContext: internal._currentRenderContext };
      const build = async () => {
        const removeHook = addNodeBuilderHook(renderer, (builder, draw) => {
          if ((draw as RenderObject) === renderObject) {
            (builder as { renderer: unknown }).renderer = new Proxy(renderer, { get(source, name, receiver) {
              if (name === '_renderTarget') return pass.target;
              if (name === '_mrt') return pass.mrt;
              if (name === 'contextNode') return pass.context;
              if (name === '_currentRenderContext') return pass.renderContext;
              return Reflect.get(source, name, receiver);
            } });
          }
        });
        const started = now();
        let promise: Promise<unknown>;
        try { promise = nodes.getForRenderAsync(renderObject); }
        finally { removeHook(); }
        await promise;
        const end = now();
        built++;
        waits.push({ at: end, ms: end - job.firstAsked, visible: end - job.lastAsked < HOT_MS });
        while (waits.length && end - waits[0].at > STATS_MS) waits.shift();
        streamTrace.event('gfx.deferred', undefined, { material: material.name || material.type, wait: Math.round(started - job.firstAsked), ms: Math.round(end - started) });
      };
      let settle: () => void = () => {};
      const job: Job = { key, firstAsked: at, lastAsked: at, distance, run: async () => { try { await build(); } catch (error) { console.warn(`[async node build] ${error}`); } finally { settle(); } },
        drop: () => { dropped++; settle(); } };
      pending.set(key, new Promise<void>(resolve => { settle = () => { pending.delete(key); resolve(); }; }));
      waiting.set(key, job);
      void drain();
    }
    return false;
  };
  return {
    ready,
    /** Draws deferred so far (each built after its frame instead of inside it). */
    get deferred() { return deferred; },
    /** Resolves once every deferred build so far is done. */
    settled: async () => { while (pending.size) await Promise.all([...pending.values()]); },
    /** window.__perf.builds: the queue now and the wait from a program's first ask to its build over the last 30 s,
     *  for programs a draw still wanted when they were built (visible) and for all. */
    stats() {
      const t = now();
      let hot = 0, oldestHotMs = 0;
      for (const job of waiting.values()) if (t - job.lastAsked < HOT_MS) { hot++; oldestHotMs = Math.max(oldestHotMs, t - job.firstAsked); }
      const recent = waits.filter(w => t - w.at <= STATS_MS), visible = recent.filter(w => w.visible).map(w => w.ms), all = recent.map(w => w.ms);
      return { queued: waiting.size, hot, oldestHotMs: Math.round(oldestHotMs), built, dropped,
        visibleWaitMs: { n: visible.length, p50: percentile(visible, 0.5), p90: percentile(visible, 0.9), max: visible.length ? Math.round(Math.max(...visible)) : null },
        allWaitMs: { n: all.length, p50: percentile(all, 0.5), p90: percentile(all, 0.9) } };
    },
  };
}
