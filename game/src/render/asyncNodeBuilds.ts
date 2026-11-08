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
 * 2026-11-05 drive; 9.6 s of it in those synchronous starts).
 *
 * The queue serves what the frames draw first: a program a draw asked for within HOT_MS is visible now and builds
 * before the others, nearest to the camera first; the rest follow, most recently wanted first, and a job whose object
 * left the scene leaves unbuilt. While a visible build waits, `share` (the pacer's ms per frame gap, main.ts) grows
 * with its wait, so on a slow machine the backlog cannot hold what is on screen for minutes: first come, first served,
 * the M1 Air queued ~1,000 builds after boot at 2.6 builds/s and drew no trophy truck (1,456 of 1,456 draws skipped). */
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
interface Job { key: number; object: THREE.Object3D; run: () => Promise<void>; drop: () => void; firstAsked: number; lastAsked: number;
  /** When a draw began asking again after HOT_MS without (became visible this time). */
  visibleSince: number; distance: number }
/** A job asked for within this many ms is visible now (a draw of the current frames is waiting for it). */
export const HOT_MS = 500;
const STATS_MS = 30_000;
/** The pacer's share while a visible build waits: SHARE_BASE_MS, plus SHARE_PER_S for each second the oldest visible
 *  build has waited since it became visible, at most SHARE_MAX_MS (a stage that runs over still ends the gap's
 *  share). The M1 Air's build stages run 50-80 ms, so below ~100 ms a gap holds one stage whatever the share: with a
 *  40 ms cap a visible build there waited up to 11.5 s after boot. */
const SHARE_BASE_MS = 6, SHARE_PER_S = 30, SHARE_MAX_MS = 120;

const inScene = (object: THREE.Object3D) => {
  let o: THREE.Object3D = object;
  while (o.parent) o = o.parent;
  return (o as THREE.Scene).isScene === true;
};

const percentile = (values: number[], p: number) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return Math.round(sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * p) - 1))]);
};

/** A copy of an override material as a draw dressed it (its nodes are shared, not rebuilt), flags included. */
export function dressedCopy(material: THREE.Material): THREE.Material {
  const copy = material.clone();
  copy.alphaTest = material.alphaTest; // an accessor NodeMaterial.copy skips (render/lodFade.ts cloneNodeMaterial)
  const flags = material as unknown as Record<string, unknown>, own = copy as unknown as Record<string, unknown>;
  for (const flag of ['isShadowPassMaterial']) if (flag in flags) own[flag] = flags[flag];
  return copy;
}

export function asyncFirstUseBuilds(renderer: THREE.WebGPURenderer, active: () => boolean, now = () => performance.now()) {
  const internal = renderer as unknown as Internals;
  // The render object `ready` finds for its check is the one r186's _renderObjectDirect looks up again at once with the
  // same arguments (RenderObjects.get: four WeakMap levels, then clipping, geometry and material-version checks with a
  // dynamic cache key; about 1 % of the M1 Air's main thread during a drive). The draw that goes ahead hands it over
  // to that next lookup instead. Plain variables, not an argument array: this runs for every draw of every pass.
  const objects = internal._objects, lookup = objects.get.bind(objects);
  let handed: RenderObject | null = null, hObject: unknown, hMaterial: unknown, hScene: unknown, hCamera: unknown, hLights: unknown,
    hContext: unknown, hClipping: unknown, hPass: unknown;
  objects.get = function (object, material, scene, camera, lightsNode, renderContext, clippingContext, passId) {
    const renderObject = handed; handed = null;
    if (renderObject && object === hObject && material === hMaterial && scene === hScene && camera === hCamera && lightsNode === hLights
      && renderContext === hContext && clippingContext === hClipping && passId === hPass) return renderObject;
    return lookup(object, material, scene, camera, lightsNode, renderContext, clippingContext, passId);
  };
  const handOver = (renderObject: RenderObject, object: unknown, material: unknown, scene: unknown, camera: unknown, lightsNode: unknown,
    renderContext: unknown, clippingContext: unknown, passId: unknown) => {
    handed = renderObject; hObject = object; hMaterial = material; hScene = scene; hCamera = camera; hLights = lightsNode;
    hContext = renderContext; hClipping = clippingContext; hPass = passId;
    return true;
  };
  const pending = new Map<number, Promise<void>>();
  const waiting = new Map<number, Job>();
  let draining: Promise<void> | null = null;
  let built = 0, dropped = 0;
  /** Finished builds: how long each waited from its first ask, and whether a draw still wanted it (visible). */
  const waits: { at: number; ms: number; visible: boolean }[] = [];
  /** The next job: the nearest visible one, else the most recently wanted; jobs whose object left the scene go. */
  const next = (): Job | undefined => {
    const t = now();
    let hot: Job | undefined, cold: Job | undefined;
    for (const job of waiting.values()) {
      if (t - job.lastAsked < HOT_MS) { if (!hot || job.distance < hot.distance) hot = job; }
      else if (!inScene(job.object)) { waiting.delete(job.key); job.drop(); }
      else if (!cold || job.lastAsked > cold.lastAsked) cold = job;
    }
    return hot ?? cold;
  };
  let shareAt = -Infinity, shareMs = 0;
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
    handed = null;
    if (!active() || (object as { isQuadMesh?: boolean }).isQuadMesh === true) return true;
    const nodes = internal._nodes, context = internal._currentRenderContext;
    const renderObject = objects.get(object, material, scene, camera, lightsNode, context, clippingContext, passId);
    if (nodes.get(renderObject).nodeBuilderState !== undefined) return handOver(renderObject, object, material, scene, camera, lightsNode, context, clippingContext, passId);
    const key = nodes.getForRenderCacheKey(renderObject);
    if (nodes.nodeBuilderCache.has(key)) return handOver(renderObject, object, material, scene, camera, lightsNode, context, clippingContext, passId);
    const at = now(), o = object.matrixWorld?.elements, c = camera.matrixWorld?.elements;
    const distance = o && c ? Math.hypot(o[12] - c[12], o[13] - c[13], o[14] - c[14]) : Infinity;
    const job = waiting.get(key);
    if (job) {
      if (at - job.lastAsked >= HOT_MS) job.visibleSince = at;
      job.lastAsked = at; job.distance = Math.min(distance, job.distance); return false;
    }
    if (!pending.has(key)) {
      deferred++;
      // The build reads renderer pass state again after each yield: pin this draw's pass, as compileModel does.
      const pass = { target: internal._renderTarget, mrt: internal._mrt, context: internal.contextNode, renderContext: internal._currentRenderContext };
      // r186 dresses a scene's override material (the shadow passes' shared one) with each caster's colour, position
      // and depth nodes, side and alpha test for that draw only, and undresses it after the draw. Built after the
      // frame, the program read the bare override: every alpha-tested caster built an opaque shadow (tree crowns cast
      // solid leaf cards: park groves and forests in full shade) and swaying casters an unswayed one. The build gets a
      // copy of the override as this draw dressed it.
      const dressed = (scene as THREE.Scene).overrideMaterial === material ? dressedCopy(material) : null;
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
        // r186 hands the render object's material to the node builder synchronously, before its first stage.
        const own = renderObject.material;
        if (dressed) renderObject.material = dressed;
        try { promise = nodes.getForRenderAsync(renderObject); }
        finally { removeHook(); renderObject.material = own; }
        await promise;
        const end = now();
        built++;
        // A visible build's wait counts from when it became visible this time; the first ask may be the boot warm-up.
        const visible = end - job.lastAsked < HOT_MS;
        waits.push({ at: end, ms: end - (visible ? job.visibleSince : job.firstAsked), visible });
        while (waits.length && end - waits[0].at > STATS_MS) waits.shift();
        streamTrace.event('gfx.deferred', undefined, { material: material.name || material.type, wait: Math.round(started - job.firstAsked), ms: Math.round(end - started) });
      };
      let settle: () => void = () => {};
      const job: Job = { key, object, firstAsked: at, lastAsked: at, visibleSince: at, distance, run: async () => { try { await build(); } catch (error) { console.warn(`[async node build] ${error}`); } finally { settle(); } },
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
    /** Milliseconds per frame gap shader builds may use while a visible build waits (0 when none waits); re-read at
     *  most once per 16 ms. */
    get share() {
      const t = now();
      if (t - shareAt < 16) return shareMs;
      let oldest = -1;
      for (const job of waiting.values()) if (t - job.lastAsked < HOT_MS) oldest = Math.max(oldest, t - job.visibleSince);
      shareAt = t;
      return shareMs = oldest < 0 ? 0 : Math.min(SHARE_MAX_MS, SHARE_BASE_MS + oldest / 1000 * SHARE_PER_S);
    },
    /** window.__perf.builds: the queue now (hot: asked within HOT_MS; oldestHotMs since it became visible) and, over the
     *  last 30 s, how long built programs waited: visible ones since they became visible, all since their first ask. */
    stats() {
      const t = now();
      let hot = 0, oldestHotMs = 0;
      for (const job of waiting.values()) if (t - job.lastAsked < HOT_MS) { hot++; oldestHotMs = Math.max(oldestHotMs, t - job.visibleSince); }
      const recent = waits.filter(w => t - w.at <= STATS_MS), visible = recent.filter(w => w.visible).map(w => w.ms), all = recent.map(w => w.ms);
      return { queued: waiting.size, hot, oldestHotMs: Math.round(oldestHotMs), built, dropped,
        visibleWaitMs: { n: visible.length, p50: percentile(visible, 0.5), p90: percentile(visible, 0.9), max: visible.length ? Math.round(Math.max(...visible)) : null },
        allWaitMs: { n: all.length, p50: percentile(all, 0.5), p90: percentile(all, 0.9) } };
    },
  };
}
