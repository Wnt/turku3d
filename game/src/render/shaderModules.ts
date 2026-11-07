/** New WGSL shader modules per frame. Dawn validates and translates a module (Tint) on the GPU process's main thread
 *  the moment it is created, and that thread also runs every frame's commands and presents them: on the Radeon 560X
 *  laptop (D3D12) DeviceBase::APICreateShaderModule took 12 ms on average and up to 32 ms each (win-trace1). r186
 *  creates a program's two modules synchronously when the first draw needs its pipeline, even when the pipeline itself
 *  compiles asynchronously, so a burst of first-use pipelines froze the page: at the switch to play every city stood
 *  still 1.7-2.9 s on Windows with almost no script running (verify-all r3, win-after3, win-trace1), right when
 *  110 modules were created in one second.
 *  Live first-use draws (render/asyncPipelines.ts) and streaming preparation (render/compileModel.ts) take new modules
 *  from a budget that refills with wall time, MS_PER_MODULE per module and at most MAX_SAVED saved up: the GPU process
 *  translates them while the page's main thread works, so a slow frame (a burst of program builds at play start,
 *  150-250 ms on the 560X) may take several and a fast one about one. 16 ms a module keeps the 560X's GPU process
 *  main thread about three quarters busy with them at most (12 ms each on average), so frames still present. A draw over the budget waits for a later frame,
 *  as it already waits for its pipeline. A fixed three per frame starved at those slow frames: d2ce6df2 took ~15 s at
 *  4-8 FPS to create the ~180 modules after the play switch (win-trace2). */
export const MS_PER_MODULE = 16, MAX_SAVED = 6;

interface Programs { vertex: Map<string, unknown>; fragment: Map<string, unknown> }
interface Stages { vertexShader: string; fragmentShader: string }

export class ShaderModuleBudget {
  private tokens: number;
  private at: number;
  /** Modules created by the renderer so far (the device's createShaderModule calls). */
  created = 0;
  /** First-use draws turned away for lack of room since the last `takeDeferred`. */
  private deferred = 0;
  constructor(private now = () => performance.now(), private msPerModule = MS_PER_MODULE, private max = MAX_SAVED) {
    this.tokens = max; this.at = now();
  }
  private refill() { const t = this.now(); this.tokens = Math.min(this.max, this.tokens + (t - this.at) / this.msPerModule); this.at = t; }
  /** Counts each module the device creates. */
  wrap(device: { createShaderModule(descriptor: unknown): unknown }) {
    const create = device.createShaderModule;
    device.createShaderModule = (descriptor: unknown) => { this.refill(); this.tokens--; this.created++; return create.call(device, descriptor); };
    return () => { device.createShaderModule = create; };
  }
  /** Whether the budget has `count` new modules now (a full budget lets one program through whatever its size). */
  room(count = 1) { this.refill(); return count === 0 || this.tokens >= Math.min(count, this.max); }
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
