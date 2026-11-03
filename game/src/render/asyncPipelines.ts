/** r186 creates a draw's render pipeline synchronously the first time the draw needs it
 * (Pipelines.updateForRender -> getForRender(renderObject) without a promise list). Dawn on Metal then compiles the
 * shader on the GPU process's own thread, and every later command waits: capture 20261103-134213 froze 9.7 s at
 * spawn (LoAF 9,744 ms, 91 ms of script) right after a draw built its program mid-frame.
 * While `active()` holds (the per-frame post pipeline), a missing pipeline is instead requested with
 * createRenderPipelineAsync; r186 already skips the draw until `isReady`, so a first-use draw appears a few frames
 * later instead of stalling the frame. Fullscreen effect quads and every other render (IBL bakes, compileAsync
 * preparation, one-shot captures) keep the synchronous path. */
interface DrawObject { object?: { isQuadMesh?: boolean } }
interface Pipelines { updateForRender(renderObject: DrawObject): void; getForRender(renderObject: DrawObject, promises?: Promise<void>[] | null): unknown }

export function asyncFirstUsePipelines(pipelines: Pipelines, active: () => boolean) {
  const update = pipelines.updateForRender;
  pipelines.updateForRender = function (this: Pipelines, renderObject: DrawObject) {
    if (!active() || renderObject.object?.isQuadMesh === true) { update.call(this, renderObject); return; }
    this.getForRender(renderObject, []);
  };
  return () => { pipelines.updateForRender = update; };
}
