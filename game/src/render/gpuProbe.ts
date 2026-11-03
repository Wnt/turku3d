/**
 * Startup GPU throughput probe: a fixed amount of ALU-bound compute work (independent FMA chains), timed with
 * timestamp queries when the device has them, else by the submit-to-done wall time. The best of three dispatches
 * is the result, so a busy compositor frame does not count. Adapter info alone cannot tell an M1 Air from an
 * M1 Max (both `apple` / `metal-3`); this measures the difference instead. Recorded in renderTier() and captures.
 */
const THREADS = 1 << 18, ITERATIONS = 2048;
// WebGPU flag values (GPUBufferUsage, GPUMapMode), spelled out for environments without the globals.
const STORAGE = 0x80, QUERY_RESOLVE = 0x200, COPY_SRC = 0x4, COPY_DST = 0x8, MAP_READ = 0x1;
const SHADER = /* wgsl */`
@group(0) @binding(0) var<storage, read_write> sink: array<f32>;
@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) id: vec3u) {
  var a = f32(id.x) * 1e-7; var b = 0.999; var c = 1.001; var d = 0.5;
  for (var i = 0u; i < ${ITERATIONS}u; i++) {
    a = fma(a, b, c); b = fma(b, c, d); c = fma(c, d, a); d = fma(d, a, b);
  }
  if (a + b + c + d == 12345.678) { sink[id.x] = a; }
}`;

export interface GpuProbe { ms: number; method: 'timestamp' | 'wall'; gflop: number }

export async function gpuProbe(device: GPUDevice): Promise<GpuProbe | null> {
  try {
    const pipeline = await device.createComputePipelineAsync({ layout: 'auto', compute: { module: device.createShaderModule({ code: SHADER }), entryPoint: 'main' } });
    const sink = device.createBuffer({ size: THREADS * 4, usage: STORAGE });
    const bind = device.createBindGroup({ layout: pipeline.getBindGroupLayout(0), entries: [{ binding: 0, resource: { buffer: sink } }] });
    const timed = device.features.has('timestamp-query');
    const query = timed ? device.createQuerySet({ type: 'timestamp', count: 2 }) : null;
    const resolve = timed ? device.createBuffer({ size: 16, usage: QUERY_RESOLVE | COPY_SRC }) : null;
    const read = timed ? device.createBuffer({ size: 16, usage: MAP_READ | COPY_DST }) : null;
    const times: number[] = [];
    try {
      for (let run = 0; run < 3; run++) {
        const encoder = device.createCommandEncoder();
        const pass = encoder.beginComputePass(query ? { timestampWrites: { querySet: query, beginningOfPassWriteIndex: 0, endOfPassWriteIndex: 1 } } : undefined);
        pass.setPipeline(pipeline); pass.setBindGroup(0, bind); pass.dispatchWorkgroups(THREADS / 64); pass.end();
        if (query) { encoder.resolveQuerySet(query, 0, 2, resolve!, 0); encoder.copyBufferToBuffer(resolve!, 0, read!, 0, 16); }
        const start = performance.now();
        device.queue.submit([encoder.finish()]);
        await device.queue.onSubmittedWorkDone();
        const wall = performance.now() - start;
        if (read) {
          await read.mapAsync(MAP_READ);
          const [begin, end] = new BigUint64Array(read.getMappedRange());
          read.unmap();
          times.push(end > begin ? Number(end - begin) / 1e6 : wall);
        } else times.push(wall);
      }
    } finally { sink.destroy(); query?.destroy(); resolve?.destroy(); read?.destroy(); }
    return { ms: Math.min(...times), method: timed ? 'timestamp' : 'wall', gflop: THREADS * ITERATIONS * 4 * 2 / 1e9 };
  } catch (error) {
    console.warn(`[gpu probe] ${error}`);
    return null;
  }
}
