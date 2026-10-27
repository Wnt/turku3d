import type { WebGPURenderer } from 'three/webgpu';

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
  skipped = 0;

  constructor(renderer: WebGPURenderer, private accept: (frame: number, ms: number) => void) {
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
    }
    else if (backend.gl) {
      this.gl = backend.gl;
      this.extension = this.gl.getExtension('EXT_disjoint_timer_query_webgl2');
    }
  }

  get status() { return this.error ?? (this.device || this.extension ? 'timestamp-query' : 'unsupported'); }

  private marker(encoder: GPUCommandEncoder, timestampWrites: GPUComputePassTimestampWrites) {
    const pass = encoder.beginComputePass({ timestampWrites });
    pass.setPipeline(this.markerPipeline!); pass.setBindGroup(0, this.markerBindings!);
    pass.dispatchWorkgroups(1); pass.end();
  }

  private slot(): Slot | null {
    const free = this.slots.find(s => s.pending === null);
    if (free) return free;
    // An unlocked benchmark can submit its complete short route before Chrome delivers readbacks.
    // Grow lazily; the bounded worst case uses 68 KiB of GPU resolve/readback buffers.
    if (this.slots.length >= 256) { this.skipped++; return null; }
    const slot: Slot = { pending: null };
    if (this.device) {
      slot.query = this.device.createQuerySet({ type: 'timestamp', count: 2 });
      slot.resolve = this.device.createBuffer({ size: 256, usage: GPUBufferUsage.QUERY_RESOLVE | GPUBufferUsage.COPY_SRC });
      slot.read = this.device.createBuffer({ size: 16, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
    } else if (this.gl && this.extension) slot.glQuery = this.gl.createQuery()!;
    this.slots.push(slot);
    return slot;
  }

  begin(frame: number) {
    if (this.status !== 'timestamp-query') return;
    this.active = this.slot();
    this.activeFrame = frame;
    if (!this.active) return;
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
    if (this.device) {
      const encoder = this.device.createCommandEncoder({ label: 'frame-timer-end' });
      this.marker(encoder, { querySet: slot.query!, endOfPassWriteIndex: 1 });
      encoder.resolveQuerySet(slot.query!, 0, 2, slot.resolve!, 0);
      encoder.copyBufferToBuffer(slot.resolve!, 0, slot.read!, 0, 16);
      this.device.queue.submit([encoder.finish()]);
      measurement = slot.read!.mapAsync(GPUMapMode.READ).then(() => {
        const values = new BigUint64Array(slot.read!.getMappedRange());
        const ms = Number(values[1] - values[0]) / 1e6;
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
      if (ms !== null && Number.isFinite(ms) && ms >= 0) this.accept(frame, ms);
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
    await this.flush();
    for (const slot of this.slots) {
      slot.query?.destroy(); slot.resolve?.destroy(); slot.read?.destroy();
      if (slot.glQuery) this.gl!.deleteQuery(slot.glQuery);
    }
    this.slots = [];
    this.markerBuffer?.destroy();
  }
}
