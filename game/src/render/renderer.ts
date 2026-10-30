// WebGPURenderer with an explicit WebGL2 switch (?backend=webgl). TSL-only code keeps both backends equal.
import { loading } from '../core/loading';
import * as THREE from 'three/webgpu';
import { GameRenderer } from './shadowMaterials';
import { renderPixelRatio } from './quality';
import { retainRendererPrograms } from './programCache';

/**
 * Mobile performance guard: without an explicit ?backend=, touch devices (phones, tablets) use WebGL2 unless
 * WebGPU reports an adapter. Other devices keep the requested backend.
 */
export async function defaultBackend(want: 'webgpu' | 'webgl', given: boolean): Promise<'webgpu' | 'webgl'> {
  if (given || want === 'webgl' || !((navigator.maxTouchPoints ?? 0) > 0)) return want;
  try { return await navigator.gpu?.requestAdapter() ? 'webgpu' : 'webgl'; } catch { return 'webgl'; }
}

interface RendererInfo { renderer: THREE.WebGPURenderer; backend: 'WebGPU' | 'WebGL2' }

/** opts.qa: canvas 1:1 with the viewport (pixel ratio 1, qa/README.md §2); opts.idPass: no antialiasing. */
export async function createRenderer(want: 'webgpu' | 'webgl', canvasParent: HTMLElement, opts: { qa?: boolean; idPass?: boolean; mobile?: boolean } = {}): Promise<RendererInfo> {
  // TRAA handles color antialiasing; MSAA must be off for its depth/velocity history.
  const renderer = new GameRenderer({ antialias: false, forceWebGL: want === 'webgl', trackTimestamp: true });
  renderer.setPixelRatio(renderPixelRatio(innerWidth, innerHeight, devicePixelRatio, !!opts.qa, opts.mobile));
  renderer.setSize(innerWidth, innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap; // PCFSoftShadowMap is not supported by WebGPURenderer
  renderer.toneMapping = THREE.AgXToneMapping;
  renderer.toneMappingExposure = 0.65;
  canvasParent.appendChild(renderer.domElement);
  await renderer.init();
  retainRendererPrograms(renderer);
  const backend = (renderer.backend as { isWebGPUBackend?: boolean }).isWebGPUBackend ? 'WebGPU' : 'WebGL2';
  const device = (renderer.backend as { device?: GPUDevice }).device;
  const info = device?.adapterInfo;
  const gl = backend === 'WebGL2' ? renderer.getContext() as WebGL2RenderingContext : null;
  loading.note(`${backend} adapter: ${info?.description || info?.device || info?.vendor || gl?.getParameter(gl.RENDERER) || 'ready'}`);
  return { renderer, backend };
}
