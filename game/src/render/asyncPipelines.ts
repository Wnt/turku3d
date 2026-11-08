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

interface PipelineBackend {
  createRenderPipeline(renderObject: { pipeline?: unknown }, promises?: Promise<void>[] | null): void;
  get(object: unknown): { pipeline?: unknown; error?: boolean };
}

/** r186's async render pipeline (WebGPUPipelineUtils.createRenderPipeline with promises) pushes a validation error
 *  scope, awaits createRenderPipelineAsync and only then pops it. Scopes nest per device, so while it waits any other
 *  GPU call's validation error, or another pending pipeline's scope, lands on it: the pipeline is flagged `error` and
 *  WebGPUBackend.draw skips every draw of it for as long as it stays cached. Every material sharing that pipeline then
 *  vanishes: Jonni's captures 20261108-163711-r7k3p1 and 20261108-172319-aprmmj lost a tile's ground (surface-array)
 *  and walls (facade-kit) this way, and only a pipeline rebuild brought them back. createRenderPipelineAsync rejects
 *  on its own failures, so a pipeline it resolved is valid: keep it drawing. r186 has already logged the scope's
 *  error (an unrelated GPU call's) under this pipeline's label. */
export function trustResolvedPipelines(backend: PipelineBackend) {
  const create = backend.createRenderPipeline;
  backend.createRenderPipeline = function (this: PipelineBackend, renderObject, promises = null) {
    const before = promises?.length ?? 0;
    create.call(this, renderObject, promises);
    if (!promises || promises.length === before) return;
    const data = this.get(renderObject.pipeline);
    const last = promises.length - 1;
    promises[last] = promises[last].then(() => {
      if (!data.error || !data.pipeline) return;
      data.error = false;
      console.warn(`[pipelines] ${(data.pipeline as { label?: string }).label ?? 'render pipeline'} compiled and keeps drawing: `
        + 'the error logged for it above came from another GPU call during its compile');
    });
  };
  return { release: () => { backend.createRenderPipeline = create; } };
}
