/** New WGSL shader modules per frame. Dawn validates and translates a module (Tint) on the GPU process's main thread
 *  the moment it is created, and that thread also runs every frame's commands and presents them: on the Radeon 560X
 *  laptop (D3D12) DeviceBase::APICreateShaderModule took 12 ms on average and up to 32 ms each (win-trace1). r186
 *  creates a program's two modules synchronously when the first draw needs its pipeline, even when the pipeline itself
 *  compiles asynchronously, so a burst of first-use pipelines froze the page: at the switch to play every city stood
 *  still 1.7-2.9 s on Windows with almost no script running (verify-all r3, win-after3, win-trace1), right when
 *  110 modules were created in one second.
 *  Live first-use draws (render/asyncPipelines.ts) and streaming preparation (render/compileModel.ts) create at most
 *  PER_FRAME new modules a frame; a draw over the limit waits for a later frame, as it already waits for its pipeline. */
export const PER_FRAME = 3;

interface Programs { vertex: Map<string, unknown>; fragment: Map<string, unknown> }
interface Stages { vertexShader: string; fragmentShader: string }

/** Animation frames of the page so far: the loading warmup renders outside the game's animation loop
 *  (renderer.info.frame stands still there), one warmup frame per requestAnimationFrame. */
export function animationFrames(): () => number {
  let frames = 0;
  if (typeof requestAnimationFrame === 'function') { const tick = () => { frames++; requestAnimationFrame(tick); }; requestAnimationFrame(tick); }
  return () => frames;
}

export class ShaderModuleBudget {
  private frame = -1;
  private used = 0;
  /** Modules created by the renderer so far (the device's createShaderModule calls). */
  created = 0;
  /** First-use draws turned away for lack of room since the last `takeDeferred`. */
  private deferred = 0;
  constructor(private currentFrame: () => number, private perFrame = PER_FRAME) {}
  private roll() { const frame = this.currentFrame(); if (frame !== this.frame) { this.frame = frame; this.used = 0; } }
  /** Counts each module the device creates. */
  wrap(device: { createShaderModule(descriptor: unknown): unknown }) {
    const create = device.createShaderModule;
    device.createShaderModule = (descriptor: unknown) => { this.roll(); this.used++; this.created++; return create.call(device, descriptor); };
    return () => { device.createShaderModule = create; };
  }
  /** Whether this frame can still create `count` new modules (one draw's program never waits behind its own size). */
  room(count = 1) { this.roll(); return count === 0 || this.used === 0 || this.used + count <= this.perFrame; }
  /** A draw waits for a later frame's room. */
  defer() { this.deferred++; }
  /** Draws turned away since the last call: the loading warmup renders again until none are. */
  takeDeferred() { const n = this.deferred; this.deferred = 0; return n; }
  /** New modules a draw's pipeline needs: its program's stages not created yet. */
  static missing(programs: Programs, state: Stages | null | undefined) {
    if (!state) return 0;
    return (programs.vertex.has(state.vertexShader) ? 0 : 1) + (programs.fragment.has(state.fragmentShader) ? 0 : 1);
  }
}
