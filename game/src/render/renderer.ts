// WebGPURenderer with an explicit WebGL2 switch (?backend=webgl). TSL-only code keeps both backends equal.
import * as THREE from 'three/webgpu';

export interface RendererInfo { renderer: THREE.WebGPURenderer; backend: 'WebGPU' | 'WebGL2' }

/** opts.qa: canvas 1:1 with the viewport (pixel ratio 1, qa/README.md §2); opts.idPass: no antialiasing. */
export async function createRenderer(want: 'webgpu' | 'webgl', canvasParent: HTMLElement, opts: { qa?: boolean; idPass?: boolean } = {}): Promise<RendererInfo> {
  const renderer = new THREE.WebGPURenderer({ antialias: !opts.idPass, forceWebGL: want === 'webgl' });
  renderer.setPixelRatio(opts.qa ? 1 : Math.min(devicePixelRatio, 2));
  renderer.setSize(innerWidth, innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap; // PCFSoftShadowMap is not supported by WebGPURenderer
  renderer.toneMapping = THREE.AgXToneMapping;
  renderer.toneMappingExposure = 1.0;
  canvasParent.appendChild(renderer.domElement);
  await renderer.init();
  const backend = (renderer.backend as { isWebGPUBackend?: boolean }).isWebGPUBackend ? 'WebGPU' : 'WebGL2';
  return { renderer, backend };
}
