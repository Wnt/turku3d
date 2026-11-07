/** r186 creates a draw's render pipeline synchronously the first time the draw needs it
 * (Pipelines.updateForRender -> getForRender(renderObject) without a promise list). Dawn on Metal then compiles the
 * shader on the GPU process's own thread, and every later command waits: capture 20261103-134213 froze 9.7 s at
 * spawn (LoAF 9,744 ms, 91 ms of script) right after a draw built its program mid-frame.
 * While `active()` holds (the per-frame post pipeline), a missing pipeline is instead requested with
 * createRenderPipelineAsync; r186 already skips the draw until `isReady`, so a first-use draw appears a few frames
 * later instead of stalling the frame. Fullscreen effect quads and every other render (IBL bakes, compileAsync
 * preparation, one-shot captures) keep the synchronous path. */
import { ShaderModuleBudget } from './shaderModules';

interface DrawObject { object?: { isQuadMesh?: boolean }; getNodeBuilderState?(): { vertexShader: string; fragmentShader: string } }
interface Pipelines {
  updateForRender(renderObject: DrawObject): void; getForRender(renderObject: DrawObject, promises?: Promise<void>[] | null): unknown;
  get?(renderObject: DrawObject): { pipeline?: unknown };
  programs?: { vertex: Map<string, unknown>; fragment: Map<string, unknown> };
}

/** `modules`: a live first-use draw whose program's shader modules do not exist yet waits for a frame with room for
 *  them (render/shaderModules.ts); it is not drawn until then, as it is not drawn before its pipeline compiles. */
export function asyncFirstUsePipelines(pipelines: Pipelines, active: () => boolean, modules: ShaderModuleBudget | null = null) {
  const update = pipelines.updateForRender;
  const pending = new Set<Promise<void>>();
  pipelines.updateForRender = function (this: Pipelines, renderObject: DrawObject) {
    if (!active() || renderObject.object?.isQuadMesh === true) { update.call(this, renderObject); return; }
    if (modules && this.programs && this.get && this.get(renderObject).pipeline === undefined
      && !modules.room(ShaderModuleBudget.missing(this.programs, renderObject.getNodeBuilderState?.()))) { modules.defer(); return; }
    const promises: Promise<void>[] = [];
    this.getForRender(renderObject, promises);
    for (const promise of promises) { pending.add(promise); void promise.finally(() => pending.delete(promise)); }
  };
  return {
    release: () => { pipelines.updateForRender = update; },
    /** Resolves once every pipeline requested so far is compiled; the loading warmup awaits it (Dawn compiles them in parallel). */
    settled: async () => { while (pending.size) await Promise.all([...pending]); },
  };
}
