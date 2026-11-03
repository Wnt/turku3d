export type QualityTier = 'mobile' | 'low' | 'reduced' | 'medium' | 'high';
/** How a tier's river reflects the city: the planar reflector pass, a screen-space march over TRAA's last frame, or sky only. */
export type RiverReflection = 'planar' | 'ssr' | 'sky';

// Permanent user quality settings, not alternative rendering implementations.
// Vegetation caps full meshes and distant impostors across all tiles. Trees within the mesh range always retain
// at least a reduced mesh; a full tree is about 2-10k triangles, a reduced one about 0.4-2k.
// `grass` sets clumps per m² of tile grass surface, the range (m) at which they have dissolved, and the clump cap.
// parkedBodies caps parked cars with physics bodies (the nearest within 120 m; the rest stay visual).
// Mobile keeps the same ~0.6x-of-low ratio as its vegetation near range: phone physics cores are the
// slowest, and 24 still covers a ~14 m radius around the player in the densest 2.65 m-bay lot.
// `lights` are the fixed DynamicLighting point slots created at startup (render/lighting.ts).
// Screen-space GTAO (`aoSamples` > 0) needs a separate normal/depth geometry prepass before beauty, about as many
// triangles and draws again as beauty's opaque set. Only high keeps it; the other tiers take TRAA velocity and
// puddle normals from beauty's MRT, and static geometry carries its occlusion in baked vertex visibility (COLOR_1).
// `maxPixels` caps desktop play's drawing buffer before AdaptiveResolution scales it (mobile devices always use 720p).
const MEDIUM = { parkedBodies: 60, vegetation: { near: 50, mid: 180, far: 650, nearInstances: 300, farInstances: 10000, barrierInstances: 2500 }, grass: { density: 2.5, range: 34, instances: 12000 }, shadowSize: 2048, splits: [16, 50, 200], aoScale: 0.5, aoSamples: 0, aoStrength: 0,
  bloomScale: 0.25, bloomStrength: 0.045, temporalSubpixel: true, reflectionScale: 0.5, reflectionSlowFps: 12, riverReflection: 'planar', detail: 0.055, saturation: 0.97,
  lights: { point: 4 }, maxPixels: 2560 * 1440 } as const;
export const QUALITY = {
  mobile: { parkedBodies: 24, vegetation: { near: 18, mid: 90, far: 350, nearInstances: 64, farInstances: 3000, barrierInstances: 400 }, grass: { density: 0, range: 0, instances: 0 }, shadowSize: 1024, splits: [90], aoScale: 0.5, aoSamples: 0, aoStrength: 0,
    bloomScale: 0.25, bloomStrength: 0, temporalSubpixel: false, reflectionScale: 0, reflectionSlowFps: 8, riverReflection: 'planar', detail: 0.025, saturation: 0.98,
    lights: { point: 0 }, maxPixels: 1280 * 720 },
  low: { parkedBodies: 40, vegetation: { near: 30, mid: 130, far: 450, nearInstances: 128, farInstances: 5000, barrierInstances: 1000 }, grass: { density: 1.5, range: 24, instances: 6000 }, shadowSize: 1024, splits: [50, 180], aoScale: 0.5, aoSamples: 0, aoStrength: 0,
    bloomScale: 0.25, bloomStrength: 0.035, temporalSubpixel: false, reflectionScale: 0.25, reflectionSlowFps: 8, riverReflection: 'planar', detail: 0.035, saturation: 0.98,
    lights: { point: 0 }, maxPixels: 2560 * 1440 },
  // Medium's look for lower-end GPUs (deviceQuality). On a Radeon Pro 560X (gcn-4) at 2880x1800 the beauty pass costs
  // 3x the M1 Max's at the same pixel count, mostly the alpha-tested leaf overdraw of the reduced (mid) tree meshes.
  // Trees hand over to impostors at 100 m instead of 180, the river reflection renders at a quarter of the view and
  // refreshes at 8 FPS while the camera is still, and the drawing buffer is capped at 1080p instead of 1440p (at 1440p
  // its 4 GB of VRAM thrashed: 200 ms frames in the 2880x1800 window).
  // GTA V's lowest preset draws no planar water pass (loops/techstudy REPORT.md §8, W1): the river reflects what TRAA's
  // last frame shows by a screen-space march in its own fragments (render/riverReflection.ts), the sky elsewhere. The
  // planar pass was the 560X's spikiest (p95 8.9-15.3 ms); `?water=planar` keeps it at 0.25 and 8 FPS for the A/B.
  // Everything else, simulation included (parked bodies, traffic and walker caps), is medium's.
  reduced: { ...MEDIUM, vegetation: { ...MEDIUM.vegetation, mid: 100 }, grass: { ...MEDIUM.grass }, splits: [...MEDIUM.splits], lights: { ...MEDIUM.lights },
    reflectionScale: 0.25, reflectionSlowFps: 8, riverReflection: 'ssr', maxPixels: 1920 * 1080 },
  medium: MEDIUM,
  high: { parkedBodies: 80, vegetation: { near: 70, mid: 260, far: 850, nearInstances: 600, farInstances: 16000, barrierInstances: 5000 }, grass: { density: 3.5, range: 48, instances: 24000 }, shadowSize: 2048, splits: [12, 32, 80, 240], aoScale: 0.75, aoSamples: 16, aoStrength: 0.3,
    bloomScale: 0.5, bloomStrength: 0.05, temporalSubpixel: true, reflectionScale: 0.75, reflectionSlowFps: 20, riverReflection: 'planar', detail: 0.07, saturation: 0.97,
    lights: { point: 8 }, maxPixels: 2560 * 1440 },
} as const;

export function parseQuality(value: string | null, fallback: QualityTier = 'medium'): QualityTier {
  if (value === null) return fallback;
  if (value === 'mobile' || value === 'low' || value === 'reduced' || value === 'medium' || value === 'high') return value;
  throw new Error(`bad ?quality= (mobile|low|reduced|medium|high): ${value}`);
}

/** The tier this session renders with and why: `url` (?quality=), `recorded` (a recorded drive replay's), `device`
 *  (deviceQuality lowered the default) or `default`. main.ts resolves it before the renderer exists; streaming
 *  residency (stream2) reads it. */
export interface RenderTier {
  tier: QualityTier; source: 'url' | 'recorded' | 'device' | 'default'; gpu: { vendor: string; architecture: string } | null;
  /** Startup GPU throughput probe (render/gpuProbe.ts), filled in shortly after start; recorded for calibration only. */
  probe?: { ms: number; method: 'timestamp' | 'wall'; gflop: number } | null;
}
let current: RenderTier | null = null;
export function setRenderTier(value: RenderTier) { current = value; }
/** The session's resolved tier; null until main.ts has resolved it. */
export function renderTier(): RenderTier | null { return current; }

/** GPU classes whose default is `reduced` instead of `medium`, by WebGPU adapter info: AMD GCN (Polaris/Vega,
 *  measured on the Radeon Pro 560X) and Intel's integrated `gen-*` graphics. Apple, NVIDIA and AMD RDNA keep medium. */
export function deviceQuality(gpu: { vendor?: string; architecture?: string } | null | undefined, fallback: QualityTier): QualityTier {
  if (fallback !== 'medium' || !gpu) return fallback;
  const vendor = (gpu.vendor ?? '').toLowerCase(), architecture = (gpu.architecture ?? '').toLowerCase();
  if ((vendor === 'amd' && architecture.startsWith('gcn')) || (vendor === 'intel' && architecture.startsWith('gen'))) return 'reduced';
  return fallback;
}

/** Includes iPad desktop-mode browsers, without classifying touch laptops as phones. */
export function mobileDevice(userAgent: string, maxTouchPoints: number): boolean {
  return /Android|iPhone|iPad|iPod/i.test(userAgent) || (/Macintosh/i.test(userAgent) && maxTouchPoints > 1);
}

/** Bound play-mode fill rate to 720p on mobile and to the tier's `maxPixels` (1440p) on desktop. QA uses one pixel per CSS pixel. */
export function renderPixelRatio(width: number, height: number, dpr: number, qa: boolean, mobile = false, maxPixels = 2560 * 1440): number {
  const pixels = mobile ? 1280 * 720 : maxPixels;
  return qa ? 1 : Math.min(dpr, mobile ? 1.5 : 2, Math.sqrt(pixels / Math.max(1, width * height)));
}
