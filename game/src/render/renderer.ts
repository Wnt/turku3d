// WebGPURenderer with an explicit WebGL2 switch (?backend=webgl). TSL-only code keeps both backends equal.
import { loading } from '../core/loading';
import * as THREE from 'three/webgpu';
import { GameRenderer } from './shadowMaterials';
import { APPLE_REDUCED_PROBE_MS, QUALITY, renderPixelRatio, type QualityTier } from './quality';
import { GameLighting } from './lighting';
import { retainRendererPrograms } from './programCache';
import { gpuProbe, type GpuProbe } from './gpuProbe';

/**
 * Mobile performance guard: without an explicit ?backend=, touch devices (phones, tablets) use WebGL2 unless
 * WebGPU reports an adapter. Other devices keep the requested backend.
 */
export async function defaultBackend(want: 'webgpu' | 'webgl', given: boolean): Promise<'webgpu' | 'webgl'> {
  if (given || want === 'webgl' || !((navigator.maxTouchPoints ?? 0) > 0)) return want;
  try { return await navigator.gpu?.requestAdapter() ? 'webgpu' : 'webgl'; } catch { return 'webgl'; }
}

/** The WebGPU adapter's vendor and architecture before the renderer exists (render/quality.ts deviceQuality). On Apple
 *  GPUs, whose adapter info does not tell the chip apart, the startup probe also runs here on a short-lived device. */
export async function adapterInfo(): Promise<{ vendor: string; architecture: string; probe: GpuProbe | null } | null> {
  try {
    const adapter = await navigator.gpu?.requestAdapter();
    const info = adapter?.info;
    if (!adapter || !info) return null;
    let probe: GpuProbe | null = null;
    if (info.vendor === 'apple') {
      const device = await adapter.requestDevice({ requiredFeatures: adapter.features.has('timestamp-query') ? ['timestamp-query'] : [] });
      try {
        // A loaded host (other GPU clients, a hot chip) only makes the probe slower: when the first read is past the
        // threshold, read twice more after short pauses and keep the fastest.
        probe = await gpuProbe(device, 5);
        for (let retry = 0; retry < 2 && probe && probe.ms > APPLE_REDUCED_PROBE_MS; retry++) {
          await new Promise(resolve => setTimeout(resolve, 150));
          const again = await gpuProbe(device, 5);
          if (again && again.ms < probe.ms) probe = again;
        }
      } finally { device.destroy(); }
    }
    return { vendor: info.vendor, architecture: info.architecture, probe };
  } catch { return null; }
}

interface RendererInfo { renderer: THREE.WebGPURenderer; backend: 'WebGPU' | 'WebGL2' }

/** opts.qa: canvas 1:1 with the viewport (pixel ratio 1, qa/README.md §2); opts.idPass: no antialiasing. */
export async function createRenderer(want: 'webgpu' | 'webgl', canvasParent: HTMLElement,
  opts: { qa?: boolean; idPass?: boolean; mobile?: boolean; quality?: QualityTier } = {}): Promise<RendererInfo> {
  // TRAA handles color antialiasing; MSAA must be off for its depth/velocity history.
  const renderer = new GameRenderer({ antialias: false, forceWebGL: want === 'webgl', trackTimestamp: true });
  // Pooled uniform and geometry buffers (render/uniformPool.ts, render/geometryPool.ts); ?uniformPool=0 / ?geomPool=0
  // turn them off. The M1 Max GPU hang came from stale bytes in reused uniform slots, fixed by c4a42e96 and validated
  // in Jonni's Chrome (venue static night and day for 170 s, the full drive replay: no device loss).
  const search = new URLSearchParams(location.search);
  renderer.poolUniforms = search.get('uniformPool') !== '0';
  renderer.poolGeometry = search.get('geomPool') !== '0';
  // Installed once, before any compile: the tier's light slots exist for the whole session.
  renderer.lighting = new GameLighting(QUALITY[opts.quality ?? 'medium'].lights);
  renderer.setPixelRatio(renderPixelRatio(innerWidth, innerHeight, devicePixelRatio, !!opts.qa, opts.mobile, QUALITY[opts.quality ?? 'medium'].maxPixels));
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
