// Measurements for docs/design/renderer-path.md, run in the page by measure.mjs. Each part is independent and
// reports under its own key; a part that throws reports { error }.
const W = 1920, H = 1080;
const only = new Set((new URLSearchParams(location.search).get('only') ?? 'adapter,raster,three,shared,field').split(','));
const result = {};
const stage = name => { window.__stage = name; document.getElementById('log').textContent = name; };
const round = (x, digits = 2) => Math.round(x * 10 ** digits) / 10 ** digits;
const median = values => [...values].sort((a, b) => a - b)[values.length >> 1];

async function openDevice() {
  const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
  const limits = {};
  for (const key in adapter.limits) limits[key] = adapter.limits[key];
  const features = [...adapter.features].sort();
  const device = await adapter.requestDevice({ requiredFeatures: features, requiredLimits: limits });
  device.addEventListener('uncapturederror', event => (result.gpu_errors ??= []).push(String(event.error.message).slice(0, 500)));
  return { adapter, device, limits, features };
}

// Wall time per frame: `frames` frames submitted back to back, then the queue drained; the best of `runs`.
async function timed(device, frame, frames = 12, runs = 5) {
  frame();
  await device.queue.onSubmittedWorkDone();
  const each = [];
  for (let run = 0; run < runs; run++) {
    const start = performance.now();
    for (let i = 0; i < frames; i++) frame();
    await device.queue.onSubmittedWorkDone();
    each.push((performance.now() - start) / frames);
  }
  return { ms: round(Math.min(...each)), median_ms: round(median(each)) };
}

function stopwatch(device, features) {
  if (!features.includes('timestamp-query')) return null;
  const set = device.createQuerySet({ type: 'timestamp', count: 2 });
  return { set, writes: { querySet: set, beginningOfPassWriteIndex: 0, endOfPassWriteIndex: 1 },
    resolved: device.createBuffer({ size: 16, usage: GPUBufferUsage.QUERY_RESOLVE | GPUBufferUsage.COPY_SRC }),
    read: device.createBuffer({ size: 16, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ }) };
}

// GPU time of the one pass that `encode(encoder, writes)` gives `timestampWrites: writes`; the best of `samples`.
async function passTime(device, watch, encode, samples = 5) {
  if (!watch) return null;
  const each = [];
  for (let i = 0; i < samples; i++) {
    const encoder = device.createCommandEncoder();
    encode(encoder, watch.writes);
    encoder.resolveQuerySet(watch.set, 0, 2, watch.resolved, 0);
    encoder.copyBufferToBuffer(watch.resolved, 0, watch.read, 0, 16);
    device.queue.submit([encoder.finish()]);
    await watch.read.mapAsync(GPUMapMode.READ);
    const stamps = new BigUint64Array(watch.read.getMappedRange().slice(0));
    watch.read.unmap();
    each.push(Number(stamps[1] - stamps[0]) / 1e6);
  }
  return round(Math.min(...each));
}

async function readBuffer(device, source, size) {
  const read = device.createBuffer({ size, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
  const encoder = device.createCommandEncoder();
  encoder.copyBufferToBuffer(source, 0, read, 0, size);
  device.queue.submit([encoder.finish()]);
  await read.mapAsync(GPUMapMode.READ);
  const copy = read.getMappedRange().slice(0);
  read.destroy();
  return copy;
}

async function readTexture(device, texture, bytesPerPixel) {
  const size = texture.width * texture.height * bytesPerPixel;
  const read = device.createBuffer({ size, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
  const encoder = device.createCommandEncoder();
  encoder.copyTextureToBuffer({ texture }, { buffer: read, bytesPerRow: texture.width * bytesPerPixel }, [texture.width, texture.height]);
  device.queue.submit([encoder.finish()]);
  await read.mapAsync(GPUMapMode.READ);
  const copy = read.getMappedRange().slice(0);
  read.destroy();
  return copy;
}

const CLUSTER = 'struct Cluster { centre: vec3f, radius: f32, meshlet: u32, error: f32, parentError: f32, pad: u32 }';

// Clusters of 128 triangles (a jittered 8x8 quad patch, 81 vertices) from a pool of 16,384 different ones. A compute
// pass keeps the records that pass a level-of-detail cut and a frustum test and counts them into the indirect
// arguments; one drawIndirect then draws 384 vertices per kept cluster, the vertex stage pulling indices and
// positions from storage buffers, into an r32uint visibility buffer (cluster << 7 | triangle) with a depth buffer.
async function raster(device, features) {
  const MESHLETS = 16384, VERTS = 81, CORNERS = 384, MOST = 4_000_000;
  const positions = new Float32Array(MESHLETS * VERTS * 4);
  for (let m = 0, o = 0; m < MESHLETS; m++) for (let y = 0; y < 9; y++) for (let x = 0; x < 9; x++, o += 4) {
    const edge = x === 0 || x === 8 || y === 0 || y === 8;
    positions[o] = (x + (edge ? 0 : Math.random() * 0.6 - 0.3)) / 8;
    positions[o + 1] = (y + (edge ? 0 : Math.random() * 0.6 - 0.3)) / 8;
    positions[o + 2] = Math.random();
  }
  const indices = new Uint32Array(MESHLETS * CORNERS);
  for (let m = 0, o = 0; m < MESHLETS; m++) for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) {
    const a = y * 9 + x;
    for (const corner of (x + y + m) & 1 ? [a, a + 1, a + 10, a, a + 10, a + 9] : [a, a + 1, a + 9, a + 1, a + 10, a + 9]) indices[o++] = corner;
  }
  const storage = GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST;
  const positionBuffer = device.createBuffer({ size: positions.byteLength, usage: storage });
  const indexBuffer = device.createBuffer({ size: indices.byteLength, usage: storage });
  device.queue.writeBuffer(positionBuffer, 0, positions);
  device.queue.writeBuffer(indexBuffer, 0, indices);
  const clusterBuffer = device.createBuffer({ size: MOST * 32, usage: storage });
  const visibleBuffer = device.createBuffer({ size: MOST * 4, usage: storage });
  const args = device.createBuffer({ size: 16, usage: GPUBufferUsage.STORAGE | GPUBufferUsage.INDIRECT | GPUBufferUsage.COPY_DST | GPUBufferUsage.COPY_SRC });
  const cut = device.createBuffer({ size: 16, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST });
  const visibility = device.createTexture({ size: [W, H], format: 'r32uint', usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_SRC });
  const depth = device.createTexture({ size: [W, H], format: 'depth32float', usage: GPUTextureUsage.RENDER_ATTACHMENT });
  const shaded = device.createTexture({ size: [W, H], format: 'rgba8unorm', usage: GPUTextureUsage.RENDER_ATTACHMENT });

  const cullPipeline = await device.createComputePipelineAsync({ layout: 'auto', compute: { module: device.createShaderModule({ code: `${CLUSTER}
    struct Args { vertexCount: u32, instanceCount: atomic<u32>, firstVertex: u32, firstInstance: u32 }
    @group(0) @binding(0) var<storage, read> clusters: array<Cluster>;
    @group(0) @binding(1) var<storage, read_write> visible: array<u32>;
    @group(0) @binding(2) var<storage, read_write> args: Args;
    @group(0) @binding(3) var<uniform> cut: vec4f;   // error threshold, record count
    @compute @workgroup_size(64) fn main(@builtin(global_invocation_id) g: vec3u) {
      if (g.x >= u32(cut.y)) { return; }
      let c = clusters[g.x];
      if (c.error > cut.x || c.parentError <= cut.x) { return; }
      if (abs(c.centre.x) > 1.0 + c.radius || abs(c.centre.y) > 1.0 + c.radius) { return; }
      visible[atomicAdd(&args.instanceCount, 1u)] = g.x;
    }` }) } });
  const drawModule = device.createShaderModule({ code: `${CLUSTER}
    @group(0) @binding(0) var<storage, read> clusters: array<Cluster>;
    @group(0) @binding(1) var<storage, read> visible: array<u32>;
    @group(0) @binding(2) var<storage, read> positions: array<vec4f>;
    @group(0) @binding(3) var<storage, read> indices: array<u32>;
    struct V { @builtin(position) p: vec4f, @location(0) @interpolate(flat) id: u32 }
    @vertex fn vs(@builtin(vertex_index) v: u32, @builtin(instance_index) i: u32) -> V {
      let slot = visible[i];
      let c = clusters[slot];
      let p = positions[c.meshlet * ${VERTS}u + indices[c.meshlet * ${CORNERS}u + v]];
      var o: V;
      o.p = vec4f(c.centre.xy + (p.xy - 0.5) * 2.0 * c.radius * vec2f(${H / W}, 1.0), clamp(c.centre.z + p.z * 0.0005, 0.0, 1.0), 1.0);
      o.id = (slot << 7u) | (v / 3u);
      return o;
    }
    @fragment fn fs(i: V) -> @location(0) u32 { return i.id; }` });
  const drawPipeline = await device.createRenderPipelineAsync({ layout: 'auto', vertex: { module: drawModule },
    fragment: { module: drawModule, targets: [{ format: 'r32uint' }] }, primitive: { topology: 'triangle-list', cullMode: 'none' },
    depthStencil: { format: 'depth32float', depthWriteEnabled: true, depthCompare: 'less' } });
  const resolveModule = device.createShaderModule({ code: `${CLUSTER}
    @group(0) @binding(0) var visibility: texture_2d<u32>;
    @group(0) @binding(1) var<storage, read> clusters: array<Cluster>;
    @group(0) @binding(2) var<storage, read> positions: array<vec4f>;
    @group(0) @binding(3) var<storage, read> indices: array<u32>;
    @vertex fn vs(@builtin(vertex_index) v: u32) -> @builtin(position) vec4f { return vec4f(f32(v & 1u) * 4.0 - 1.0, f32(v >> 1u) * 4.0 - 1.0, 0.0, 1.0); }
    @fragment fn fs(@builtin(position) p: vec4f) -> @location(0) vec4f {
      let id = textureLoad(visibility, vec2u(p.xy), 0).r;
      if (id == 0xffffffffu) { return vec4f(0.0); }
      let c = clusters[id >> 7u];
      let corner = c.meshlet * ${CORNERS}u + (id & 127u) * 3u;
      let a = positions[c.meshlet * ${VERTS}u + indices[corner]].xyz;
      let b = positions[c.meshlet * ${VERTS}u + indices[corner + 1u]].xyz;
      let d = positions[c.meshlet * ${VERTS}u + indices[corner + 2u]].xyz;
      return vec4f(normalize(cross(b - a, d - a) + vec3f(0.0, 0.0, 1e-6)) * 0.5 + 0.5, 1.0);
    }` });
  const resolvePipeline = await device.createRenderPipelineAsync({ layout: 'auto', vertex: { module: resolveModule },
    fragment: { module: resolveModule, targets: [{ format: 'rgba8unorm' }] } });

  const entries = buffers => buffers.map((buffer, binding) => ({ binding, resource: buffer instanceof GPUBuffer ? { buffer } : buffer }));
  const cullGroup = device.createBindGroup({ layout: cullPipeline.getBindGroupLayout(0), entries: entries([clusterBuffer, visibleBuffer, args, cut]) });
  const drawGroup = device.createBindGroup({ layout: drawPipeline.getBindGroupLayout(0), entries: entries([clusterBuffer, visibleBuffer, positionBuffer, indexBuffer]) });
  const resolveGroup = device.createBindGroup({ layout: resolvePipeline.getBindGroupLayout(0), entries: entries([visibility.createView(), clusterBuffer, positionBuffer, indexBuffer]) });
  const visibilityView = visibility.createView(), depthView = depth.createView(), shadedView = shaded.createView();
  const watch = stopwatch(device, features);
  const reset = new Uint32Array([CORNERS, 0, 0, 0]);

  const fill = (records, kept, radius) => {
    const data = new ArrayBuffer(records * 32), f = new Float32Array(data), u = new Uint32Array(data);
    for (let i = 0, o = 0; i < records; i++, o += 8) {
      const keep = i % Math.round(records / kept) === 0;
      f[o] = Math.random() * 2 - 1; f[o + 1] = Math.random() * 2 - 1; f[o + 2] = Math.random() * 0.99; f[o + 3] = radius;
      u[o + 4] = Math.random() * MESHLETS | 0; f[o + 5] = keep ? 0.5 : 2; f[o + 6] = keep ? 2 : 4;
    }
    device.queue.writeBuffer(clusterBuffer, 0, data);
    device.queue.writeBuffer(cut, 0, new Float32Array([1, records, 0, 0]));
  };
  const cullPass = (encoder, records, writes) => {
    const pass = encoder.beginComputePass(writes ? { timestampWrites: writes } : {});
    pass.setPipeline(cullPipeline); pass.setBindGroup(0, cullGroup); pass.dispatchWorkgroups(Math.ceil(records / 64)); pass.end();
  };
  const drawPass = (encoder, writes) => {
    const pass = encoder.beginRenderPass({ colorAttachments: [{ view: visibilityView, loadOp: 'clear', storeOp: 'store', clearValue: [4294967295, 0, 0, 0] }],
      depthStencilAttachment: { view: depthView, depthClearValue: 1, depthLoadOp: 'clear', depthStoreOp: 'store' }, ...(writes ? { timestampWrites: writes } : {}) });
    pass.setPipeline(drawPipeline); pass.setBindGroup(0, drawGroup); pass.drawIndirect(args, 0); pass.end();
  };
  const resolvePass = (encoder, writes) => {
    const pass = encoder.beginRenderPass({ colorAttachments: [{ view: shadedView, loadOp: 'clear', storeOp: 'store' }], ...(writes ? { timestampWrites: writes } : {}) });
    pass.setPipeline(resolvePipeline); pass.setBindGroup(0, resolveGroup); pass.draw(3); pass.end();
  };

  const report = { pool: { clusters: MESHLETS, bytes: positions.byteLength + indices.byteLength }, target: `${W}x${H} r32uint + depth32float`, runs: [] };
  // [triangles drawn, pixels per triangle]: depth complexity = triangles * pixels / (W * H)
  for (const [triangles, pixels] of [[1e6, 2], [2e6, 1], [4e6, 0.5], [8e6, 0.25], [4e6, 2], [16e6, 0.25], [16e6, 0.5], [1e6, 8], [1e6, 16], [0.25e6, 32], [32e6, 0.0625]]) {
    stage(`raster ${triangles / 1e6}M x ${pixels}px`);
    const kept = Math.round(triangles / 128), records = kept * 2;
    fill(records, kept, Math.sqrt(128 * pixels) / H);
    const frame = () => {
      device.queue.writeBuffer(args, 0, reset);
      const encoder = device.createCommandEncoder();
      cullPass(encoder, records);
      drawPass(encoder);
      device.queue.submit([encoder.finish()]);
    };
    const wall = await timed(device, frame, triangles > 8e6 ? 6 : 12);
    const withResolve = await timed(device, () => {
      device.queue.writeBuffer(args, 0, reset);
      const encoder = device.createCommandEncoder();
      cullPass(encoder, records); drawPass(encoder); resolvePass(encoder);
      device.queue.submit([encoder.finish()]);
    }, triangles > 8e6 ? 6 : 12, 3);
    const gpu = await passTime(device, watch, (encoder, writes) => { device.queue.writeBuffer(args, 0, reset); cullPass(encoder, records); drawPass(encoder, writes); });
    const resolveGpu = await passTime(device, watch, (encoder, writes) => resolvePass(encoder, writes));
    const drawn = new Uint32Array(await readBuffer(device, args, 16))[1];
    const ids = new Uint32Array(await readTexture(device, visibility, 4));
    let covered = 0;
    for (let i = 0; i < ids.length; i += 7) if (ids[i] !== 4294967295) covered++;
    report.runs.push({ triangles, pixels_per_triangle: pixels, depth_complexity: round(triangles * pixels / (W * H), 1), clusters_tested: records, clusters_drawn: drawn,
      cull_and_raster_ms: wall.ms, median_ms: wall.median_ms, with_resolve_ms: withResolve.ms, raster_pass_gpu_ms: gpu, resolve_pass_gpu_ms: resolveGpu,
      covered_share: round(covered / (ids.length / 7), 3), million_triangles_per_ms: round(triangles / 1e6 / wall.ms, 2) });
  }
  // Culling alone at the size of a city's cluster table: 4 M records, 1 in 16 kept.
  stage('cull 4M');
  fill(MOST, MOST / 16, 0.001);
  const cullWall = await timed(device, () => {
    device.queue.writeBuffer(args, 0, reset);
    const encoder = device.createCommandEncoder();
    cullPass(encoder, MOST);
    device.queue.submit([encoder.finish()]);
  });
  const cullGpu = await passTime(device, watch, (encoder, writes) => { device.queue.writeBuffer(args, 0, reset); cullPass(encoder, MOST, writes); });
  report.cull = { records: MOST, record_bytes: 32, kept: new Uint32Array(await readBuffer(device, args, 16))[1], ms: cullWall.ms, median_ms: cullWall.median_ms, pass_gpu_ms: cullGpu };
  for (const buffer of [positionBuffer, indexBuffer, clusterBuffer, visibleBuffer]) buffer.destroy();
  return report;
}

// CPU time of three.js WebGPURenderer.render() for N static meshes that share one geometry and one material, and
// for one BatchedMesh of N instances. Matrices are frozen, so the time is three's render list and draw encoding.
async function three(shared) {
  const THREE = await import('/three/three.webgpu.js');
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  let renderer = new THREE.WebGPURenderer({ canvas, antialias: false, device: shared.device });
  await renderer.init();
  const report = { revision: THREE.REVISION, device_given: renderer.backend.device === shared.device, runs: [] };
  const target = new THREE.RenderTarget(W, H);
  const camera = new THREE.PerspectiveCamera(50, W / H, 1, 5000);
  camera.position.set(0, 600, 600); camera.lookAt(0, 0, 0); camera.updateMatrixWorld();
  const geometry = new THREE.BoxGeometry(1, 1, 1);
  const material = new THREE.MeshBasicNodeMaterial({ color: 0x8899aa });
  const place = (i, n, matrix) => { const side = Math.ceil(Math.sqrt(n)); return matrix.makeTranslation((i % side - side / 2) * 2, 0, (Math.floor(i / side) - side / 2) * 2); };
  const cpu = async (scene, frames = 30) => {
    renderer.setRenderTarget(target);
    for (let i = 0; i < 6; i++) { renderer.render(scene, camera); await new Promise(resolve => setTimeout(resolve, 0)); }
    await renderer.backend.device.queue.onSubmittedWorkDone();
    const each = [];
    for (let i = 0; i < frames; i++) {
      const start = performance.now();
      renderer.render(scene, camera);
      each.push(performance.now() - start);
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    await renderer.backend.device.queue.onSubmittedWorkDone();
    return { render_cpu_ms: round(median(each)), min_ms: round(Math.min(...each)), draw_calls: renderer.info.render.drawCalls, triangles: renderer.info.render.triangles };
  };
  for (const n of [2000, 10000]) {
    stage(`three ${n} meshes`);
    const scene = new THREE.Scene();
    for (let i = 0; i < n; i++) {
      const mesh = new THREE.Mesh(geometry, material);
      place(i, n, mesh.matrix); mesh.matrixAutoUpdate = false;
      scene.add(mesh);
    }
    scene.updateMatrixWorld(true);
    scene.matrixWorldAutoUpdate = false;
    const run = await cpu(scene);
    report.runs.push({ kind: 'Mesh', objects: n, ...run, cpu_us_per_object: round(run.render_cpu_ms * 1000 / n, 1) });
  }
  for (const [n, culled] of [[20000, true], [20000, false]]) {
    stage(`three batch ${n}`);
    const scene = new THREE.Scene();
    const batch = new THREE.BatchedMesh(n, 24, 36, material);
    batch.sortObjects = false; batch.perObjectFrustumCulled = culled;
    const id = batch.addGeometry(geometry), matrix = new THREE.Matrix4();
    for (let i = 0; i < n; i++) batch.setMatrixAt(batch.addInstance(id), place(i, n, matrix));
    scene.add(batch);
    scene.updateMatrixWorld(true);
    const run = await cpu(scene);
    report.runs.push({ kind: `BatchedMesh${culled ? ', per-instance culling' : ''}`, objects: n, ...run, cpu_us_per_object: round(run.render_cpu_ms * 1000 / n, 1) });
  }
  return { report, THREE, renderer };
}

// Raw passes on three's device against three's own targets: (1) a raw pass writes a texture that three then samples,
// (2) a raw draw depth-tests against the depth buffer three has just rendered and writes into three's colour target.
async function sharedTargets({ THREE, renderer }) {
  const device = renderer.backend.device;
  const report = {};
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const first = new THREE.RenderTarget(64, 64, { depthBuffer: false });
  renderer.initRenderTarget(first);
  const firstTexture = renderer.backend.get(first.texture).texture;
  report.three_colour_format = firstTexture.format;
  let encoder = device.createCommandEncoder();
  encoder.beginRenderPass({ colorAttachments: [{ view: firstTexture.createView(), clearValue: [0.25, 0.5, 0.75, 1], loadOp: 'clear', storeOp: 'store' }] }).end();
  device.queue.submit([encoder.finish()]);
  const sampling = new THREE.MeshBasicNodeMaterial();
  sampling.map = first.texture;
  const quad = new THREE.Scene();
  quad.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), sampling));
  const second = new THREE.RenderTarget(16, 16, { depthBuffer: false });
  renderer.setRenderTarget(second);
  renderer.render(quad, camera);
  report.three_samples_raw_write = [...await renderer.readRenderTargetPixelsAsync(second, 8, 8, 1, 1)];
  report.raw_write_expected = [64, 128, 191, 255];

  const third = new THREE.RenderTarget(64, 64);
  third.depthTexture = new THREE.DepthTexture(64, 64);
  const half = new THREE.Scene();
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(1, 2), new THREE.MeshBasicNodeMaterial({ color: 0x00ff00 }));
  plane.position.set(-0.5, 0, -0.5);
  half.add(plane);
  renderer.setRenderTarget(third);
  renderer.render(half, camera);
  const colour = renderer.backend.get(third.texture).texture, depth = renderer.backend.get(third.depthTexture).texture;
  report.three_depth_format = depth.format;
  const module = device.createShaderModule({ code: `
    @vertex fn vs(@builtin(vertex_index) v: u32) -> @builtin(position) vec4f { return vec4f(f32(v & 1u) * 4.0 - 1.0, f32(v >> 1u) * 4.0 - 1.0, 0.9, 1.0); }
    @fragment fn fs() -> @location(0) vec4f { return vec4f(1.0, 0.0, 1.0, 1.0); }` });
  const stencil = depth.format.includes('stencil');
  const pipeline = await device.createRenderPipelineAsync({ layout: 'auto', vertex: { module }, fragment: { module, targets: [{ format: colour.format }] },
    depthStencil: { format: depth.format, depthWriteEnabled: true, depthCompare: 'less' } });
  encoder = device.createCommandEncoder();
  const pass = encoder.beginRenderPass({ colorAttachments: [{ view: colour.createView(), loadOp: 'load', storeOp: 'store' }],
    depthStencilAttachment: { view: depth.createView(), depthLoadOp: 'load', depthStoreOp: 'store', ...(stencil ? { stencilLoadOp: 'load', stencilStoreOp: 'store' } : {}) } });
  pass.setPipeline(pipeline); pass.draw(3); pass.end();
  device.queue.submit([encoder.finish()]);
  report.pixel_where_three_drew_nearer = [...await renderer.readRenderTargetPixelsAsync(third, 16, 32, 1, 1)];
  report.pixel_where_only_raw_drew = [...await renderer.readRenderTargetPixelsAsync(third, 48, 32, 1, 1)];
  report.depth_test_expected = [[0, 255, 0, 255], [255, 0, 255, 255]];
  return report;
}

// A four-level distance-field clipmap (4 x 128^3 r16float, voxels 0.2 to 1.6 m, 205 m across) of a street grid of
// box buildings, sphere traced in a compute pass: one primary ray per pixel, then `rays` diffuse rays from each hit.
async function field(device, features) {
  const N = 128, LEVELS = 4, NEAR = 25.6;
  const scene = `
    fn hash2(c: vec2f) -> f32 { return fract(sin(dot(c, vec2f(127.1, 311.7))) * 43758.5453); }
    fn scene(p: vec3f) -> f32 {
      let cell = floor(p.xz / 24.0);
      let h = 6.0 + 14.0 * hash2(cell);
      let d = abs(vec3f(p.x - (cell.x + 0.5) * 24.0, p.y, p.z - (cell.y + 0.5) * 24.0)) - vec3f(8.0, h, 8.0);
      return min(length(max(d, vec3f(0.0))) + min(max(d.x, max(d.y, d.z)), 0.0), p.y);
    }`;
  const fillModule = device.createShaderModule({ code: `${scene}
    @group(0) @binding(0) var<uniform> level: vec4f;   // minimum corner, voxel size
    struct V { @builtin(position) p: vec4f, @location(0) @interpolate(flat) slice: u32 }
    @vertex fn vs(@builtin(vertex_index) v: u32, @builtin(instance_index) i: u32) -> V {
      var o: V; o.p = vec4f(f32(v & 1u) * 4.0 - 1.0, f32(v >> 1u) * 4.0 - 1.0, 0.0, 1.0); o.slice = i; return o; }
    @fragment fn fs(i: V) -> @location(0) vec4f { return vec4f(scene(level.xyz + vec3f(i.p.xy, f32(i.slice) + 0.5) * level.w), 0.0, 0.0, 1.0); }` });
  const fillPipeline = await device.createRenderPipelineAsync({ layout: 'auto', vertex: { module: fillModule }, fragment: { module: fillModule, targets: [{ format: 'r16float' }] } });
  const centre = [0, 10, 0];
  const levels = [];
  const fillStart = performance.now();
  for (let l = 0; l < LEVELS; l++) {
    const extent = NEAR * 2 ** l, voxel = extent / N;
    const texture = device.createTexture({ size: [N, N, N], dimension: '3d', format: 'r16float', usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.TEXTURE_BINDING });
    const uniform = device.createBuffer({ size: 16, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST });
    device.queue.writeBuffer(uniform, 0, new Float32Array([centre[0] - extent / 2, centre[1] - extent / 2, centre[2] - extent / 2, voxel]));
    const group = device.createBindGroup({ layout: fillPipeline.getBindGroupLayout(0), entries: [{ binding: 0, resource: { buffer: uniform } }] });
    const view = texture.createView({ dimension: '3d' });
    const encoder = device.createCommandEncoder();
    for (let z = 0; z < N; z++) {
      const pass = encoder.beginRenderPass({ colorAttachments: [{ view, depthSlice: z, loadOp: 'clear', storeOp: 'store' }] });
      pass.setPipeline(fillPipeline); pass.setBindGroup(0, group); pass.draw(3, 1, 0, z); pass.end();
    }
    device.queue.submit([encoder.finish()]);
    levels.push(texture);
  }
  await device.queue.onSubmittedWorkDone();
  const report = { clipmap: `${LEVELS} x ${N}^3 r16float`, bytes: LEVELS * N ** 3 * 2, voxel_m: [0.2, 0.4, 0.8, 1.6], fill_ms: round(performance.now() - fillStart), runs: [] };

  const half = l => NEAR * 2 ** l / 2;
  const sample = l => `textureSampleLevel(c${l}, s, r / ${NEAR * 2 ** l} + 0.5, 0.0).r`;
  const traceModule = device.createShaderModule({ code: `
    struct U { eye: vec4f, right: vec4f, up: vec4f, forward: vec4f, size: vec4f }   // size: width, height, diffuse rays, seed
    @group(0) @binding(0) var s: sampler;
    @group(0) @binding(1) var c0: texture_3d<f32>;
    @group(0) @binding(2) var c1: texture_3d<f32>;
    @group(0) @binding(3) var c2: texture_3d<f32>;
    @group(0) @binding(4) var c3: texture_3d<f32>;
    @group(0) @binding(5) var traced: texture_storage_2d<rgba16float, write>;
    @group(0) @binding(6) var<uniform> u: U;
    fn field(p: vec3f) -> f32 {
      let r = p - vec3f(${centre.join('.0, ')}.0);
      let m = max(abs(r.x), max(abs(r.y), abs(r.z)));
      if (m < ${half(0) - 0.4}) { return ${sample(0)}; }
      if (m < ${half(1) - 0.8}) { return ${sample(1)}; }
      if (m < ${half(2) - 1.6}) { return ${sample(2)}; }
      if (m < ${half(3) - 3.2}) { return ${sample(3)}; }
      return 1e4;
    }
    fn trace(origin: vec3f, direction: vec3f, most: u32, reach: f32) -> vec3f {   // distance, hit, steps
      var t = 0.0;
      for (var i = 0u; i < most; i++) {
        let d = field(origin + direction * t);
        if (d > 5e3) { return vec3f(t, 0.0, f32(i + 1u)); }
        if (d < 0.02 + t * 0.002) { return vec3f(t, 1.0, f32(i + 1u)); }
        t += max(d, 0.05);
        if (t > reach) { return vec3f(t, 0.0, f32(i + 1u)); }
      }
      return vec3f(t, 0.0, f32(most));
    }
    fn normalAt(p: vec3f) -> vec3f {
      let e = vec2f(0.1, -0.1);
      return normalize(e.xyy * field(p + e.xyy) + e.yyx * field(p + e.yyx) + e.yxy * field(p + e.yxy) + e.xxx * field(p + e.xxx));
    }
    fn pcg(v: u32) -> u32 { let x = v * 747796405u + 2891336453u; let w = ((x >> ((x >> 28u) + 4u)) ^ x) * 277803737u; return (w >> 22u) ^ w; }
    fn random(seed: ptr<function, u32>) -> f32 { *seed = pcg(*seed); return f32(*seed) / 4294967296.0; }
    @compute @workgroup_size(8, 8) fn main(@builtin(global_invocation_id) g: vec3u) {
      if (g.x >= u32(u.size.x) || g.y >= u32(u.size.y)) { return; }
      let uv = (vec2f(g.xy) + 0.5) / u.size.xy * 2.0 - 1.0;
      let direction = normalize(u.forward.xyz + u.right.xyz * uv.x * (u.size.x / u.size.y) * 0.7 - u.up.xyz * uv.y * 0.7);
      let first = trace(u.eye.xyz, direction, 64u, 200.0);
      var sky = 1.0;
      var steps = first.z;
      if (first.y > 0.5) {
        let p = u.eye.xyz + direction * first.x;
        let n = normalAt(p);
        steps += 4.0;
        var seed = g.x + g.y * 4096u + u32(u.size.w) * 16777259u;
        let rays = u32(u.size.z);
        var open = 0.0;
        for (var k = 0u; k < rays; k++) {
          let a = random(&seed) * 6.2831853;
          let z = random(&seed) * 2.0 - 1.0;
          let w = sqrt(1.0 - z * z);
          let hit = trace(p + n * 0.3, normalize(n + vec3f(cos(a) * w, sin(a) * w, z) * 0.999), 32u, 60.0);
          open += 1.0 - hit.y;
          steps += hit.z;
        }
        sky = open / max(f32(rays), 1.0);
      }
      textureStore(traced, g.xy, vec4f(sky, first.x, steps, first.y));
    }` });
  const pipeline = await device.createComputePipelineAsync({ layout: 'auto', compute: { module: traceModule } });
  const sampler = device.createSampler({ magFilter: 'linear', minFilter: 'linear' });
  const uniform = device.createBuffer({ size: 80, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST });
  const forward = [0.28, 0.05, 0.96], length = Math.hypot(...forward);
  const f = forward.map(x => x / length), right = [f[2], 0, -f[0]].map(x => x / Math.hypot(f[2], f[0]));
  const up = [right[1] * f[2] - right[2] * f[1], right[2] * f[0] - right[0] * f[2], right[0] * f[1] - right[1] * f[0]].map(x => -x);
  const watch = stopwatch(device, features);
  for (const [width, height, rays] of [[960, 540, 0], [960, 540, 1], [960, 540, 4], [960, 540, 16], [1920, 1080, 0], [1920, 1080, 1], [1920, 1080, 4]]) {
    stage(`field ${width}x${height} ${rays} rays`);
    const target = device.createTexture({ size: [width, height], format: 'rgba16float', usage: GPUTextureUsage.STORAGE_BINDING | GPUTextureUsage.COPY_SRC });
    device.queue.writeBuffer(uniform, 0, new Float32Array([0, 1.7, 0, 0, ...right, 0, ...up, 0, ...f, 0, width, height, rays, 1]));
    const group = device.createBindGroup({ layout: pipeline.getBindGroupLayout(0), entries: [{ binding: 0, resource: sampler },
      ...levels.map((texture, l) => ({ binding: 1 + l, resource: texture.createView({ dimension: '3d' }) })),
      { binding: 5, resource: target.createView() }, { binding: 6, resource: { buffer: uniform } }] });
    const tracePass = (encoder, writes) => {
      const pass = encoder.beginComputePass(writes ? { timestampWrites: writes } : {});
      pass.setPipeline(pipeline); pass.setBindGroup(0, group); pass.dispatchWorkgroups(Math.ceil(width / 8), Math.ceil(height / 8)); pass.end();
    };
    const wall = await timed(device, () => { const encoder = device.createCommandEncoder(); tracePass(encoder); device.queue.submit([encoder.finish()]); }, 8, 4);
    const gpu = await passTime(device, watch, tracePass);
    const texels = new Float16Array(await readTexture(device, target, 8));
    let steps = 0, hits = 0, sky = 0;
    for (let i = 0; i < texels.length; i += 4) { steps += texels[i + 2]; hits += texels[i + 3]; sky += texels[i + 3] * texels[i]; }
    const pixels = width * height;
    report.runs.push({ size: `${width}x${height}`, diffuse_rays_per_hit: rays, ms: wall.ms, median_ms: wall.median_ms, pass_gpu_ms: gpu, primary_hit_share: round(hits / pixels, 3),
      field_samples_per_pixel: round(steps / pixels, 1), million_field_samples_per_ms: round(steps / 1e6 / wall.ms, 1), mean_sky_visibility_at_hits: round(sky / Math.max(hits, 1), 3) });
    target.destroy();
  }
  return report;
}

try {
  stage('adapter');
  const gpu = await openDevice();
  const info = gpu.adapter.info ?? {};
  if (only.has('adapter')) {
    result.adapter = { vendor: info.vendor, architecture: info.architecture, device: info.device, description: info.description,
      fallback: info.isFallbackAdapter ?? false, features: gpu.features, limits: gpu.limits, wgsl_language_features: [...(navigator.gpu.wgslLanguageFeatures ?? [])].sort(),
      preferred_canvas_format: navigator.gpu.getPreferredCanvasFormat(), device_with_every_feature_and_limit: true,
      user_agent: navigator.userAgent.replace(/^.*(Chrome\/[\d.]+).*$/, '$1'), cores: navigator.hardwareConcurrency, device_memory_gb: navigator.deviceMemory };
  }
  const part = async (name, run) => {
    if (!only.has(name)) return undefined;
    try { stage(name); return await run(); } catch (error) { result[name] = { error: String(error?.stack ?? error).slice(0, 900) }; return undefined; }
  };
  const rasterReport = await part('raster', () => raster(gpu.device, gpu.features));
  if (rasterReport) result.raster = rasterReport;
  const host = await part('three', () => three(gpu));
  if (host) result.three = host.report;
  if (host && only.has('shared')) {
    try { stage('shared'); result.shared = await sharedTargets(host); } catch (error) { result.shared = { error: String(error?.stack ?? error).slice(0, 900) }; }
  }
  const fieldReport = await part('field', () => field(gpu.device, gpu.features));
  if (fieldReport) result.field = fieldReport;
} catch (error) {
  result.error = String(error?.stack ?? error).slice(0, 900);
}
window.__result = result;
window.__done = true;
stage('done');
