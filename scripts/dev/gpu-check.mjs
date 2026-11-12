#!/usr/bin/env node
// What GPU does a browser on some host give WebGPU, and how fast is it? No dependencies (Node 22+ WebSocket).
//
//   scripts/dev/gpu-check.mjs --cdp http://127.0.0.1:PORT [--port P]
//
// Opens a tab in the browser behind --cdp (a preview session, or BROWSER_CDP of host-lease/session.py), asks WebGPU
// for its adapter and times a fixed workload: 60 frames of 400 000 instanced triangles into a 1920x1080 target.
// WebGPU needs a secure context, so the tab loads an empty page that this script serves on 127.0.0.1:P (default
// TURKU3D_PORT_BASE + 9); for a browser on the Air or Windows pass the same port to `session.py --forward P`.
// Prints one JSON line. A software adapter (SwiftShader, llvmpipe) or a missing one means the host cannot render
// captures.
import http from 'node:http';

const argv = process.argv.slice(2);
const opt = (name, fallback) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback;
const cdp = opt('--cdp', process.env.BROWSER_CDP);
if (!cdp) throw new Error('give --cdp http://127.0.0.1:PORT (or BROWSER_CDP)');

const port = Number(opt('--port', Number(process.env.TURKU3D_PORT_BASE ?? 5170) + 9));
const server = http.createServer((request, response) => response.end('<!doctype html><title>gpu-check</title>'));
await new Promise((resolve, reject) => server.once('error', reject).listen(port, '127.0.0.1', resolve));
const version = await (await fetch(`${cdp}/json/version`)).json();
const socket = new WebSocket(version.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let next = 0;
const waiting = new Map();
socket.onmessage = event => {
  const message = JSON.parse(event.data);
  const done = waiting.get(message.id);
  if (!done) return;
  waiting.delete(message.id);
  message.error ? done.reject(new Error(message.error.message)) : done.resolve(message.result);
};
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  waiting.set(++next, { resolve, reject });
  socket.send(JSON.stringify({ id: next, method, params, sessionId }));
});

const workload = async () => {
  if (!navigator.gpu) return { error: `no navigator.gpu (secure context: ${isSecureContext})` };
  const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
  if (!adapter) return { error: 'no WebGPU adapter' };
  const info = adapter.info ?? {};
  const device = await adapter.requestDevice();
  const format = 'rgba8unorm';
  const target = device.createTexture({ size: [1920, 1080], format, usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC });
  const depth = device.createTexture({ size: [1920, 1080], format: 'depth24plus', usage: GPUTextureUsage.RENDER_ATTACHMENT });
  const module = device.createShaderModule({ code: `
    struct V { @builtin(position) p: vec4f, @location(0) c: vec3f }
    @vertex fn vs(@builtin(vertex_index) v: u32, @builtin(instance_index) i: u32) -> V {
      let f = f32(i); let a = f * 0.618; let r = fract(f * 0.0001) * 1.4;
      let corner = vec2f(f32(v == 1u) - 0.5, f32(v == 2u) - 0.5) * 0.05;
      var o: V; o.p = vec4f(cos(a) * r * 0.7 + corner.x, sin(a) * r * 0.7 + corner.y, fract(f * 0.37), 1.0);
      o.c = vec3f(fract(f * 0.13), fract(f * 0.71), fract(f * 0.29)); return o; }
    @fragment fn fs(i: V) -> @location(0) vec4f { var c = i.c; for (var k = 0; k < 8; k++) { c = sin(c * 3.1 + 0.2) * 0.5 + 0.5; } return vec4f(c, 1.0); }` });
  const pipeline = device.createRenderPipeline({ layout: 'auto', vertex: { module }, fragment: { module, targets: [{ format }] },
    depthStencil: { format: 'depth24plus', depthWriteEnabled: true, depthCompare: 'less' } });
  const frame = () => {
    const encoder = device.createCommandEncoder();
    const pass = encoder.beginRenderPass({ colorAttachments: [{ view: target.createView(), loadOp: 'clear', storeOp: 'store' }],
      depthStencilAttachment: { view: depth.createView(), depthClearValue: 1, depthLoadOp: 'clear', depthStoreOp: 'store' } });
    pass.setPipeline(pipeline); pass.draw(3, 400000); pass.end();
    device.queue.submit([encoder.finish()]);
  };
  frame(); await device.queue.onSubmittedWorkDone();
  const start = performance.now();
  for (let i = 0; i < 60; i++) frame();
  await device.queue.onSubmittedWorkDone();
  const ms = (performance.now() - start) / 60;
  return { vendor: info.vendor, architecture: info.architecture, device: info.device, description: info.description,
    fallback: info.isFallbackAdapter ?? adapter.isFallbackAdapter ?? false, maxTextureDimension2D: adapter.limits.maxTextureDimension2D,
    ms_per_frame: Math.round(ms * 100) / 100, userAgent: navigator.userAgent.replace(/^.*(Chrome\/[\d.]+).*$/, '$1'),
    cores: navigator.hardwareConcurrency };
};

const { targetId } = await send('Target.createTarget', { url: `http://localhost:${port}/` });
const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
try {
  let page = '';
  for (let i = 0; i < 40 && page !== `http://localhost:${port}/ complete`; i++) {
    await new Promise(resolve => setTimeout(resolve, 500));
    page = (await send('Runtime.evaluate', { expression: 'location.href + " " + document.readyState', returnByValue: true }, sessionId)).result.value;
  }
  if (!page.startsWith('http://localhost')) throw new Error(`the browser could not load http://localhost:${port}/ (it shows ${page}): forward the port to its host`);
  const result = await send('Runtime.evaluate', { expression: `(${workload})()`, awaitPromise: true, returnByValue: true }, sessionId);
  console.log(JSON.stringify(result.exceptionDetails ? { error: result.exceptionDetails.exception?.description ?? 'evaluate failed' } : result.result.value));
} finally {
  await send('Target.closeTarget', { targetId });
  socket.close();
  server.closeAllConnections();
  server.close();
}
