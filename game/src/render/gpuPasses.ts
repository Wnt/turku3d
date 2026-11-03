import type { WebGPURenderer } from 'three/webgpu';

export const GPU_PASSES = ['prepass', 'shadows', 'beauty', 'reflection', 'traa', 'ao', 'bloom', 'post', 'puddles', 'other'] as const;
export type GpuPass = typeof GPU_PASSES[number];
export type GpuPassTimes = Partial<Record<GpuPass, number>>;
const targets = new WeakMap<object, GpuPass>();
export function labelGpuPass(target: object, label: GpuPass) { targets.set(target, label); }
interface PassContext { renderTarget?: object | null; textures?: { name: string }[] | null; fullscreenPass?: boolean }
export function gpuPassName(context: PassContext): GpuPass {
  const explicit = context.renderTarget && targets.get(context.renderTarget);
  if (explicit) return explicit;
  const name = context.textures?.[0]?.name ?? '';
  if (name === 'ShadowMap' || name.startsWith('VSM')) return 'shadows';
  if (name.startsWith('TRAANode.')) return 'traa';
  if (name.startsWith('GTAONode.')) return 'ao';
  if (name.startsWith('UnrealBloomPass.')) return 'bloom';
  if (name.startsWith('weather puddles')) return 'puddles';
  if (!context.renderTarget) return context.fullscreenPass ? 'post' : 'beauty';
  return 'other';
}

/** Timestamp each actual GPU render pass, including individual array-camera layers.
 * Hooking Three's descriptor preparation alone would reuse indices for those layers.
 * Encoders retain the initiating render context's label through finishRender(). */
export function installGpuPassTimestamps(renderer: WebGPURenderer,
  timestamps: (name: GpuPass) => GPURenderPassTimestampWrites | undefined) {
  const backend = renderer.backend as unknown as { device: GPUDevice; beginRender?: (context: PassContext) => void };
  if (!backend.beginRender) return undefined;
  const device = backend.device, originalEncoder = device.createCommandEncoder, originalBegin = backend.beginRender;
  let name: GpuPass = 'other';
  const begin = function(this: typeof backend, context: PassContext) {
    const previous = name; name = gpuPassName(context);
    try { return originalBegin.call(this, context); } finally { name = previous; }
  };
  const create = function(this: GPUDevice, options?: GPUCommandEncoderDescriptor) {
    const encoder = originalEncoder.call(this, options), originalPass = encoder.beginRenderPass, label = name;
    encoder.beginRenderPass = descriptor => originalPass.call(encoder,
      { ...descriptor, timestampWrites: timestamps(label) });
    return encoder;
  };
  backend.beginRender = begin; device.createCommandEncoder = create;
  return () => {
    if (backend.beginRender === begin) backend.beginRender = originalBegin;
    if (device.createCommandEncoder === create) device.createCommandEncoder = originalEncoder;
  };
}
