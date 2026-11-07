/** New WGSL shader modules per frame. Dawn validates and translates a module (Tint) on the GPU process's main thread
 *  the moment it is created, and that thread also runs every frame's commands and presents them: on the Radeon 560X
 *  laptop (D3D12) DeviceBase::APICreateShaderModule took 12 ms on average and up to 32 ms each (win-trace1). r186
 *  creates a program's two modules synchronously when the first draw needs its pipeline, even when the pipeline itself
 *  compiles asynchronously, so a burst of first-use pipelines froze the page: at the switch to play every city stood
 *  still 1.7-2.9 s on Windows with almost no script running (verify-all r3, win-after3, win-trace1), right when
 *  110 modules were created in one second.
 *
 *  Live first-use draws (render/asyncPipelines.ts) take new modules from a budget of estimated GPU-process time that
 *  refills with wall time (DUTY ms per ms, at most MAX_SAVED_MS saved up). A module's time is its WGSL length times the
 *  device's translation rate: the big lit fragment programs cost three times a flat average (fleet, nature and eye
 *  fragments 36-51 ms each on the 560X right after the play click, 84-102 KB, win-trace3), so a count-based budget let
 *  six of them through at once and the first seconds of play held 150-300 ms frames, one 1.6 s (win-after5). The rate
 *  is the 560X's on the reduced tiers and a quarter of it on medium and up (render/renderer.ts): getCompilationInfo
 *  cannot time a module (it resolved 40-180 times later than the translation took, win-mod1). A draw over the budget
 *  waits for a later frame, as it already waits for its pipeline. A full budget lets one program through whatever its
 *  size; the debt then holds the next ones. A fixed three per frame starved at slow frames: d2ce6df2 took ~15 s at 4-8
 *  FPS to create the ~180 modules after the play switch (win-trace2). Streaming preparation (compileModel) is paced by
 *  main-thread time already and does not wait: starving it moved those programs into in-frame builds. Its modules
 *  still spend the budget. */
export const DUTY = 0.5, MAX_SAVED_MS = 48;
/** Translation time per WGSL character (ms): the 560X's (37 ms for 84 KB, win-trace3) and a desktop-class GPU process's. */
export const SLOW_MS_PER_CHAR = 0.00044, FAST_MS_PER_CHAR = SLOW_MS_PER_CHAR / 4;

interface Programs { vertex: Map<string, unknown>; fragment: Map<string, unknown> }
interface Stages { vertexShader: string; fragmentShader: string }

export class ShaderModuleBudget {
  private tokens: number;
  private at: number;
  /** Estimated translation ms per WGSL character on this device. */
  msPerChar: number;
  /** Modules created by the renderer so far (the device's createShaderModule calls). */
  created = 0;
  /** First-use draws turned away for lack of room since the last `takeDeferred`. */
  private deferred = 0;
  constructor(msPerChar = SLOW_MS_PER_CHAR, private now = () => performance.now(), public duty = DUTY, private max = MAX_SAVED_MS) {
    this.msPerChar = msPerChar; this.tokens = max; this.at = now();
  }
  private refill() { const t = this.now(); this.tokens = Math.min(this.max, this.tokens + (t - this.at) * this.duty); this.at = t; }
  /** Charges each module the device creates. */
  wrap(device: { createShaderModule(descriptor: { code?: string }): unknown }) {
    const create = device.createShaderModule;
    device.createShaderModule = (descriptor: { code?: string }) => {
      this.refill(); this.tokens -= (descriptor?.code?.length ?? 0) * this.msPerChar; this.created++;
      return create.call(device, descriptor);
    };
    return () => { device.createShaderModule = create; };
  }
  /** Whether the budget has room for modules of `chars` WGSL characters now (a full budget lets one program through
   *  whatever its size). */
  room(chars = 0) { this.refill(); return chars === 0 || this.tokens >= Math.min(chars * this.msPerChar, this.max); }
  /** A draw waits for a later frame's room. */
  defer() { this.deferred++; }
  /** Draws turned away since the last call: the loading warmup renders again until none are. */
  takeDeferred() { const n = this.deferred; this.deferred = 0; return n; }
  /** WGSL characters of the modules a draw's pipeline needs: its program's stages not created yet. */
  static missing(programs: Programs, state: Stages | null | undefined) {
    if (!state) return 0;
    return (programs.vertex.has(state.vertexShader) ? 0 : state.vertexShader.length)
      + (programs.fragment.has(state.fragmentShader) ? 0 : state.fragmentShader.length);
  }
}
