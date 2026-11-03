import type { WebGPURenderer } from 'three/webgpu';
import { installGpuPassTimestamps, type GpuPass, type GpuPassTimes } from './gpuPasses';

// TypeScript 7's DOM library includes WebGPU interfaces but omits these browser constant namespaces.
declare const GPUBufferUsage: Readonly<{ QUERY_RESOLVE: number; COPY_SRC: number; COPY_DST: number; MAP_READ: number; STORAGE: number }>;
declare const GPUMapMode: Readonly<{ READ: number }>;

interface TimerBackend {
  isWebGPUBackend?: boolean;
  trackTimestamp: boolean;
  device?: GPUDevice;
  gl?: WebGL2RenderingContext;
}
interface TimerExtension { TIME_ELAPSED_EXT: number; GPU_DISJOINT_EXT: number }
interface Slot {
  query?: GPUQuerySet; resolve?: GPUBuffer; read?: GPUBuffer; glQuery?: WebGLQuery;
  pending: Promise<void> | null;
  passes: GpuPass[]; overflow: boolean;
}
const PASS_LIMIT = 128, QUERY_COUNT = 2 + PASS_LIMIT * 2;

/** Per-pass GPU time on the frame's serialized timeline. `values` holds the frame begin/end markers, then one
 * begin/end pair per pass in encoding order. Tile-based GPUs (Apple M-series) start a pass's vertex stage, and so
 * its beginning timestamp, while earlier passes still shade: chained fullscreen passes then overlap, and summing
 * each pass's own begin..end counted bloom's 12 tiny passes as 20 ms of a 16 ms frame (capture 20261103-133034).
 * Each pass is credited only with the time its end extends past every earlier pass's end (never before its own
 * start), so the totals add up to at most the passes' span; the remainder is idle, copy or compute time. */
export function serialPassTimes(passes: readonly GpuPass[], values: BigUint64Array): GpuPassTimes | undefined {
  const times: GpuPassTimes = {};
  // The timeline starts at the first pass, not the frame's begin marker: on Apple GPUs that dependency-free compute
  // marker can execute after the frame's render passes (capture 20261103-134213 read 0 ms for every pass but post).
  let covered: bigint | null = null;
  for (const [i, name] of passes.entries()) {
    const start = values[2 + i * 2], end = values[3 + i * 2];
    if (end < start) return undefined;
    const from = covered === null || start > covered ? start : covered;
    times[name] = (times[name] ?? 0) + (end > from ? Number(end - from) / 1e6 : 0);
    if (covered === null || end > covered) covered = end;
  }
  return times;
}

/** Whole-frame GPU interval. Independent readback slots prevent reusing queries still in flight.
 * WebGPU markers also include command-copy work and GPU idle gaps between this frame's submissions.
 * WebGL uses one disjoint timer query around the entire frame, never nested per-pass timers.
 */
export class GpuFrameTimer {
  private device?: GPUDevice;
  private gl?: WebGL2RenderingContext;
  private extension: TimerExtension | null = null;
  private slots: Slot[] = [];
  private active: Slot | null = null;
  private activeFrame = 0;
  private error: string | null = null;
  private markerBuffer?: GPUBuffer;
  private markerPipeline?: GPUComputePipeline;
  private markerBindings?: GPUBindGroup;
  private disjointEpoch = 0;
  private releasePassHooks?: () => void;
  skipped = 0;
  passSkipped = 0;

  constructor(renderer: WebGPURenderer, private accept: (frame: number, ms: number, passes?: GpuPassTimes) => void) {
    const backend = renderer.backend as unknown as TimerBackend;
    // Request the timestamp feature through renderer.init(), then own whole-frame queries here.
    backend.trackTimestamp = false;
    if (backend.isWebGPUBackend && backend.device?.features.has('timestamp-query')) {
      this.device = backend.device;
      // Metal can optimise an empty compute pass away, producing two zero timestamps. Dispatch one real write.
      this.markerBuffer = this.device.createBuffer({ size: 4, usage: GPUBufferUsage.STORAGE });
      this.markerPipeline = this.device.createComputePipeline({ layout: 'auto', compute: {
        module: this.device.createShaderModule({ code: `@group(0) @binding(0) var<storage, read_write> marker: u32;
          @compute @workgroup_size(1) fn main() { marker = marker + 1u; }` }), entryPoint: 'main',
      } });
      this.markerBindings = this.device.createBindGroup({ layout: this.markerPipeline.getBindGroupLayout(0),
        entries: [{ binding: 0, resource: { buffer: this.markerBuffer } }] });
      this.releasePassHooks = installGpuPassTimestamps(renderer, name => {
        const slot = this.active;
        if (!slot) return undefined;
        if (slot.passes.length >= PASS_LIMIT) { slot.overflow = true; return undefined; }
        const index = 2 + slot.passes.length * 2;
        slot.passes.push(name);
        return { querySet: slot.query!, beginningOfPassWriteIndex: index, endOfPassWriteIndex: index + 1 };
      });
    }
    else if (backend.gl) {
      this.gl = backend.gl;
      this.extension = this.gl.getExtension('EXT_disjoint_timer_query_webgl2');
    }
  }

  get status() { return this.error ?? (this.device || this.extension ? 'timestamp-query' : 'unsupported'); }
  get passStatus() { return this.error ?? (this.releasePassHooks ? 'timestamp-query' : 'unsupported'); }

  private marker(encoder: GPUCommandEncoder, timestampWrites: GPUComputePassTimestampWrites) {
    const pass = encoder.beginComputePass({ timestampWrites });
    pass.setPipeline(this.markerPipeline!); pass.setBindGroup(0, this.markerBindings!);
    pass.dispatchWorkgroups(1); pass.end();
  }

  private slot(): Slot | null {
    const free = this.slots.find(s => s.pending === null);
    if (free) return free;
    // An unlocked benchmark can submit its complete short route before Chrome delivers readbacks.
    // Grow lazily; frame-owned pass queries/readbacks remain bounded (about 1.1 MiB).
    if (this.slots.length >= 256) { this.skipped++; return null; }
    const slot: Slot = { pending: null, passes: [], overflow: false };
    if (this.device) {
      slot.query = this.device.createQuerySet({ type: 'timestamp', count: QUERY_COUNT });
      slot.resolve = this.device.createBuffer({ size: Math.ceil(QUERY_COUNT * 8 / 256) * 256, usage: GPUBufferUsage.QUERY_RESOLVE | GPUBufferUsage.COPY_SRC });
      slot.read = this.device.createBuffer({ size: QUERY_COUNT * 8, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
    } else if (this.gl && this.extension) slot.glQuery = this.gl.createQuery()!;
    this.slots.push(slot);
    return slot;
  }

  begin(frame: number) {
    if (this.status !== 'timestamp-query') return;
    this.active = this.slot();
    this.activeFrame = frame;
    if (!this.active) return;
    this.active.passes = []; this.active.overflow = false;
    if (this.device) {
      const encoder = this.device.createCommandEncoder({ label: 'frame-timer-start' });
      this.marker(encoder, { querySet: this.active.query!, beginningOfPassWriteIndex: 0 });
      this.device.queue.submit([encoder.finish()]);
    } else this.gl!.beginQuery(this.extension!.TIME_ELAPSED_EXT, this.active.glQuery!);
  }

  end() {
    const slot = this.active, frame = this.activeFrame;
    this.active = null;
    if (!slot) return;
    let measurement: Promise<number | null>;
    let passTimes: GpuPassTimes | undefined;
    if (this.device) {
      const encoder = this.device.createCommandEncoder({ label: 'frame-timer-end' });
      this.marker(encoder, { querySet: slot.query!, endOfPassWriteIndex: 1 });
      const count = 2 + slot.passes.length * 2;
      encoder.resolveQuerySet(slot.query!, 0, count, slot.resolve!, 0);
      encoder.copyBufferToBuffer(slot.resolve!, 0, slot.read!, 0, count * 8);
      this.device.queue.submit([encoder.finish()]);
      measurement = slot.read!.mapAsync(GPUMapMode.READ).then(() => {
        const values = new BigUint64Array(slot.read!.getMappedRange());
        const ms = Number(values[1] - values[0]) / 1e6;
        if (this.releasePassHooks && !slot.overflow) {
          passTimes = serialPassTimes(slot.passes, values);
          if (!passTimes) this.passSkipped++;
        } else if (slot.overflow) this.passSkipped++;
        slot.read!.unmap();
        return ms;
      });
    } else {
      const gl = this.gl!, ext = this.extension!;
      const epoch = this.disjointEpoch;
      gl.endQuery(ext.TIME_ELAPSED_EXT);
      const start = performance.now();
      measurement = new Promise((resolve, reject) => {
        const poll = () => {
          if (gl.isContextLost()) { reject(new Error('GPU context lost')); return; }
          if (gl.getParameter(ext.GPU_DISJOINT_EXT)) this.disjointEpoch++;
          // A clock discontinuity invalidates every query that was in flight, not only the first polled query.
          if (epoch !== this.disjointEpoch) { this.skipped++; resolve(null); return; }
          if (gl.getQueryParameter(slot.glQuery!, gl.QUERY_RESULT_AVAILABLE)) {
            resolve(Number(gl.getQueryParameter(slot.glQuery!, gl.QUERY_RESULT)) / 1e6);
          } else if (performance.now() - start > 5000) reject(new Error('GPU query timeout'));
          else setTimeout(poll, 1);
        };
        setTimeout(poll, 0);
      });
    }
    slot.pending = measurement.then(ms => {
      if (ms !== null && Number.isFinite(ms) && ms >= 0) this.accept(frame, ms, passTimes);
    }).catch((error: unknown) => { this.error = String(error); }).finally(() => { slot.pending = null; });
  }

  async flush() { await Promise.all(this.slots.map(s => s.pending)); }

  /** Queue completion is separate from timestamp readback (which Chrome may deliver several tasks later). */
  async completed() {
    if (this.device) { await this.device.queue.onSubmittedWorkDone(); return; }
    const gl = this.gl;
    if (!gl) return;
    const fence = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
    gl.flush();
    await new Promise<void>((resolve, reject) => {
      const started = performance.now();
      const poll = () => {
        const status = gl.clientWaitSync(fence!, 0, 0);
        if (status === gl.ALREADY_SIGNALED || status === gl.CONDITION_SATISFIED) {
          gl.deleteSync(fence); resolve();
        } else if (status === gl.WAIT_FAILED || performance.now() - started > 5000) {
          gl.deleteSync(fence); reject(new Error('GPU completion fence failed'));
        } else setTimeout(poll, 0);
      };
      poll();
    });
  }

  async dispose() {
    this.releasePassHooks?.();
    await this.flush();
    for (const slot of this.slots) {
      slot.query?.destroy(); slot.resolve?.destroy(); slot.read?.destroy();
      if (slot.glQuery) this.gl!.deleteQuery(slot.glQuery);
    }
    this.slots = [];
    this.markerBuffer?.destroy();
  }
}
