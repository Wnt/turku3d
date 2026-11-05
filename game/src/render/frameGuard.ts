import * as THREE from 'three/webgpu';

/** r186 sets pass state on the renderer around every nested render (PassNode: target and MRT; ShadowNode: target,
 * render-object function, clear colour and the scene's override material; Renderer._renderScene: call depth, render
 * context and the lights stack) and puts it back only on a normal return. One exception inside a frame left beauty's
 * MRT on the renderer for good: every program built afterwards for another target (the output quad, deferred and
 * prepared builds) had no matching attachment, so its fragment output struct came out empty ("structures must have
 * at least one member"), its pipeline invalid, and the screen stayed black (Jonni's :5220 session 2026-11-05 02:23, after
 * one "writeBuffer ... parameter 1 is not of type 'GPUBuffer'"). `guardFrame` restores the state the frame started with
 * when it throws and rethrows, so a failed frame costs that frame only. */
interface Internals {
  _renderTarget: THREE.RenderTarget | null; _activeCubeFace: number; _activeMipmapLevel: number;
  _mrt: ReturnType<THREE.WebGPURenderer['getMRT']>; contextNode: THREE.WebGPURenderer['contextNode'];
  _callDepth: number; _currentRenderContext: unknown; _renderObjectFunction: unknown; _currentRenderObjectFunction: unknown;
  _handleObjectFunction: unknown; _currentRenderBundle: unknown; _clearColor: THREE.Color & { a: number };
  lighting: { _cache?: unknown[] };
}

export function guardFrame(renderer: THREE.WebGPURenderer, scene: THREE.Scene, draw: () => void) {
  const r = renderer as unknown as Internals;
  const target = r._renderTarget, face = r._activeCubeFace, mip = r._activeMipmapLevel, mrt = r._mrt, context = r.contextNode;
  const depth = r._callDepth, renderContext = r._currentRenderContext, objectFunction = r._renderObjectFunction;
  const currentObjectFunction = r._currentRenderObjectFunction, handleObject = r._handleObjectFunction, bundle = r._currentRenderBundle;
  const clear = r._clearColor, red = clear.r, green = clear.g, blue = clear.b, alpha = clear.a;
  const toneMapping = renderer.toneMapping, colorSpace = renderer.outputColorSpace, autoClear = renderer.autoClear;
  const override = scene.overrideMaterial, lights = r.lighting._cache?.length ?? 0;
  try { draw(); } catch (error) {
    renderer.setRenderTarget(target, face, mip); renderer.setMRT(mrt); r.contextNode = context;
    r._callDepth = depth; r._currentRenderContext = renderContext; r._renderObjectFunction = objectFunction;
    r._currentRenderObjectFunction = currentObjectFunction; r._handleObjectFunction = handleObject; r._currentRenderBundle = bundle;
    clear.r = red; clear.g = green; clear.b = blue; clear.a = alpha;
    renderer.toneMapping = toneMapping; renderer.outputColorSpace = colorSpace; renderer.autoClear = autoClear;
    scene.overrideMaterial = override;
    if (r.lighting._cache && r.lighting._cache.length > lights) r.lighting._cache.length = lights;
    console.error('[frame] a render threw; renderer pass state restored', error);
    throw error;
  }
}
