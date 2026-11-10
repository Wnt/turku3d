export type QualityTier = 'mobile' | 'low' | 'reduced' | 'medium' | 'high' | 'ultra';
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
// `maxPixels` caps desktop play's drawing buffer before AdaptiveResolution scales it (mobile devices always use 720p);
// `governor` lets render/settingGovernor.ts step rendering settings down when frames miss the 40 FPS goal at the
// resolution floor (tiers below medium only: medium and high never change at runtime).
// Medium and reduced draw no planar water pass, as GTA V's lower presets (loops/techstudy REPORT.md §8, W1): the river
// reflects what TRAA's last frame shows by a screen-space march in its own fragments (render/riverReflection.ts), the
// sky where it finds nothing. Blind A/B against planar on the Radeon 560X, the M1 Max and the M1 Air (2026-11-04): as
// close or closer to the Aura references in every pair, for 3-11 ms less GPU p95 on the Air; `?water=planar` is the A/B.
// High and low keep the planar pass; without TRAA history (`aa=fxaa|off`) the march has nothing to read and the river
// shows the sky (render/water.ts riverReflection).
// Medium trees hand over to their impostors at 120 m (180 before the hemi-octahedral impostors, world/nature/impostor.ts:
// they turn with the view and keep the crown's depth, so the reduced meshes' alpha-tested leaves need not reach as far).
// midInstances: trees drawn with the reduced (LOD 1) meshes before the mid band shrinks towards impostors (world/
// vegetationLod.ts). Those alpha-tested leaves are the costliest vegetation pool on the Radeon 560X; the budgets sit
// above every pose measured before forests were populated from lidar crowns (at most 635 at medium), so parks and
// streets keep their bands and only dense forests hand over earlier.
const MEDIUM = { parkedBodies: 60, vegetation: { near: 50, mid: 120, far: 650, nearInstances: 300, midInstances: 700, farInstances: 10000, barrierInstances: 2500 }, grass: { density: 2.5, range: 34, instances: 12000 }, shadowSize: 2048, splits: [16, 50, 200], aoScale: 0.5, aoSamples: 0, aoStrength: 0,
  bloomScale: 0.25, bloomStrength: 0.045, temporalSubpixel: true, reflectionScale: 0.5, reflectionSlowFps: 12, riverReflection: 'ssr', detail: 0.055, saturation: 0.97,
  lights: { point: 4 }, maxPixels: 2560 * 1440, governor: false } as const;
export const QUALITY = {
  mobile: { parkedBodies: 24, vegetation: { near: 18, mid: 90, far: 350, nearInstances: 64, midInstances: 200, farInstances: 3000, barrierInstances: 400 }, grass: { density: 0, range: 0, instances: 0 }, shadowSize: 1024, splits: [90], aoScale: 0.5, aoSamples: 0, aoStrength: 0,
    bloomScale: 0.25, bloomStrength: 0, temporalSubpixel: false, reflectionScale: 0, reflectionSlowFps: 8, riverReflection: 'planar', detail: 0.025, saturation: 0.98,
    lights: { point: 0 }, maxPixels: 1280 * 720, governor: false },
  low: { parkedBodies: 40, vegetation: { near: 30, mid: 130, far: 450, nearInstances: 128, midInstances: 400, farInstances: 5000, barrierInstances: 1000 }, grass: { density: 1.5, range: 24, instances: 6000 }, shadowSize: 1024, splits: [50, 180], aoScale: 0.5, aoSamples: 0, aoStrength: 0,
    bloomScale: 0.25, bloomStrength: 0.035, temporalSubpixel: false, reflectionScale: 0.25, reflectionSlowFps: 8, riverReflection: 'planar', detail: 0.035, saturation: 0.98,
    lights: { point: 0 }, maxPixels: 2560 * 1440, governor: true },
  // Medium's look for lower-end GPUs (deviceQuality). On a Radeon Pro 560X (gcn-4) at 2880x1800 the beauty pass costs
  // 3x the M1 Max's at the same pixel count, mostly the alpha-tested leaf overdraw of the reduced (mid) tree meshes.
  // Trees hand over to impostors at 100 m instead of 120, the river reflection renders at a quarter of the view and
  // refreshes at 8 FPS while the camera is still, and the drawing buffer is capped at 1080p instead of 1440p (at 1440p
  // its 4 GB of VRAM thrashed: 200 ms frames in the 2880x1800 window).
  // Its river reflects by medium's screen-space march (see MEDIUM); `?water=planar` keeps the planar pass at 0.25 and 8 FPS.
  // Everything else, simulation included (parked bodies, traffic and walker caps), is medium's.
  reduced: { ...MEDIUM, vegetation: { ...MEDIUM.vegetation, mid: 100 }, grass: { ...MEDIUM.grass }, splits: [...MEDIUM.splits], lights: { ...MEDIUM.lights },
    reflectionScale: 0.25, reflectionSlowFps: 8, maxPixels: 1920 * 1080, governor: true },
  medium: MEDIUM,
  high: { parkedBodies: 80, vegetation: { near: 70, mid: 260, far: 850, nearInstances: 600, midInstances: 1400, farInstances: 16000, barrierInstances: 5000 }, grass: { density: 3.5, range: 48, instances: 24000 }, shadowSize: 2048, splits: [12, 32, 80, 240], aoScale: 0.75, aoSamples: 16, aoStrength: 0.3,
    bloomScale: 0.5, bloomStrength: 0.05, temporalSubpixel: true, reflectionScale: 0.75, reflectionSlowFps: 20, riverReflection: 'planar', detail: 0.07, saturation: 0.97,
    lights: { point: 8 }, maxPixels: 2560 * 1440, governor: false },
  // For screenshots and judging the look, not for frame rate; chosen only in the pause menu or by `?quality=ultra`,
  // never by deviceQuality. Trees keep full meshes to 160 m and reduced ones to 600 m, grass reaches 90 m, the sun's
  // four 4096 px cascades reach 400 m, GTAO runs at full resolution, the river reflects at full size every frame, and
  // the drawing buffer is the display's native one (no pixel cap; main.ts also keeps AdaptiveResolution at 1.0).
  // Its draw distances and streaming share are WORLD_TIERS.ultra and STREAMING_TIERS.ultra below.
  ultra: { parkedBodies: 120, vegetation: { near: 160, mid: 600, far: 2500, nearInstances: 2500, midInstances: 6000, farInstances: 60000, barrierInstances: 15000 }, grass: { density: 5, range: 90, instances: 80000 }, shadowSize: 4096, splits: [12, 36, 110, 400], aoScale: 1, aoSamples: 16, aoStrength: 0.3,
    bloomScale: 0.5, bloomStrength: 0.05, temporalSubpixel: true, reflectionScale: 1, reflectionSlowFps: 240, riverReflection: 'planar', detail: 0.07, saturation: 0.97,
    lights: { point: 16 }, maxPixels: Infinity, governor: false },
} as const;

/** The presets the pause menu offers (ui/graphicsMenu.ts), lowest first; `mobile` is only a device default. */
export const MENU_TIERS = ['low', 'reduced', 'medium', 'high', 'ultra'] as const satisfies readonly QualityTier[];

/** Draw distances and install concurrency of a tier (world/tierWorld.ts applies them to the engine's constants):
 *  `window`: full tiles each way around the camera's tile (1: 3x3); `viewDetail`: full tiles in the view wedge (m);
 *  `tileDetail`: 3D distance beyond which a resident full tile yields to its scenery cell; `hd3d`: a flying camera's
 *  full-install distance; `blockHd`: buildings' HD children instead of their block LOD (null: per block, 280 m);
 *  `buildingView`: scale of a building's own view distance; `props`: street furniture hide distance; `cells`: 250 m
 *  scenery cell refinement (null: the pixel rule, 350..2000 m); `scenery`: distant city radius; `lead`: scale of the
 *  path lead a driving player prepares; `detailSlots`/`previewSlots`: concurrent full installs and previews;
 *  `returnTiles`/`returnBytes`: hidden full visuals kept for a return. */
export interface WorldTier { window: number; viewDetail: number; tileDetail: number; hd3d: number; blockHd: number | null; buildingView: number;
  props: number; cells: number | null; scenery: number; lead: number; detailSlots: number; previewSlots: number; returnTiles: number; returnBytes: number }
const WORLD: WorldTier = { window: 1, viewDetail: 400, tileDetail: 450, hd3d: 250, blockHd: null, buildingView: 1, props: 125, cells: null,
  scenery: 4000, lead: 1, detailSlots: 6, previewSlots: 6, returnTiles: 16, returnBytes: 96 * 2 ** 20 };
export const WORLD_TIERS: Readonly<Record<QualityTier, WorldTier>> = {
  mobile: { ...WORLD, scenery: 2500 }, low: { ...WORLD, scenery: 2500 }, reduced: WORLD, medium: WORLD, high: WORLD,
  // A 5x5 full-tile window, full tiles to 1 km in the view, HD buildings to 900 m, the distant city to 8 km.
  ultra: { window: 2, viewDetail: 1000, tileDetail: 1150, hd3d: 900, blockHd: 900, buildingView: 4, props: 450, cells: 2000,
    scenery: 8000, lead: 2, detailSlots: 12, previewSlots: 12, returnTiles: 48, returnBytes: 512 * 2 ** 20 },
};

/** How much of a frame streaming takes (render/preparationBudget.ts StreamingAllowance): the main-thread frame targets
 *  (ms) while the window waits (`window`), for prefetch (`prefetch`) and while moving fast (`moving`); the caps of the
 *  total per frame (`cap`, `urgentCap`) and of one budget's window (`limit`, `urgentLimit`); the share of the frame
 *  interval (at most `floorInterval` ms of it) streaming keeps while urgent; `always`: urgent all the time. */
export interface StreamingTier { window: number; prefetch: number; moving: number; cap: number; urgentCap: number; limit: number; urgentLimit: number;
  floorShare: number; floorInterval: number; always: boolean }
const STREAMING: StreamingTier = { window: 24, prefetch: 20.5, moving: 17.5, cap: 6, urgentCap: 12, limit: 3, urgentLimit: 6, floorShare: 0.2, floorInterval: 30, always: false };
export const STREAMING_TIERS: Readonly<Record<QualityTier, StreamingTier>> = {
  mobile: STREAMING, low: STREAMING, reduced: STREAMING, medium: STREAMING, high: STREAMING,
  // Loading before frames: a 66 ms frame target (15 FPS) whatever the speed, up to 40 ms of streaming a frame in
  // 12 ms windows, and never less than half of the frame interval.
  ultra: { window: 66, prefetch: 66, moving: 66, cap: 40, urgentCap: 40, limit: 12, urgentLimit: 12, floorShare: 0.5, floorInterval: 60, always: true },
};

export function parseQuality(value: string | null, fallback: QualityTier = 'medium'): QualityTier {
  if (value === null) return fallback;
  if (value === 'mobile' || value === 'low' || value === 'reduced' || value === 'medium' || value === 'high' || value === 'ultra') return value;
  throw new Error(`bad ?quality= (mobile|low|reduced|medium|high|ultra): ${value}`);
}

/** localStorage key of the preset chosen in the pause menu's Graphics page. */
export const PRESET_KEY = 'turku3d.graphics';
/** The stored menu preset, or null (none, unreadable storage, or a value this build does not offer). */
export function storedPreset(storage: Pick<Storage, 'getItem'> | null): QualityTier | null {
  try {
    const value = storage?.getItem(PRESET_KEY);
    return (MENU_TIERS as readonly string[]).includes(value ?? '') ? value as QualityTier : null;
  } catch { return null; }
}
export function storePreset(storage: Pick<Storage, 'setItem'> | null, tier: QualityTier) {
  try { storage?.setItem(PRESET_KEY, tier); } catch { /* blocked storage: the choice lasts for this page */ }
}

/** The tier this session renders with and why: `url` (?quality=), `recorded` (a recorded drive replay's), `menu` (the
 *  pause menu's stored preset), `device` (deviceQuality lowered the default) or `default`. main.ts resolves it before the renderer exists; streaming
 *  residency (stream2) reads it. */
export interface RenderTier {
  tier: QualityTier; source: 'url' | 'recorded' | 'menu' | 'device' | 'default'; gpu: { vendor: string; architecture: string } | null;
  /** navigator.hardwareConcurrency, the second Apple signal. */
  cores?: number | null;
  /** Startup GPU throughput probe (render/gpuProbe.ts): before the renderer on Apple GPUs, shortly after start elsewhere. */
  probe?: { ms: number; method: 'timestamp' | 'wall'; gflop: number } | null;
}
let current: RenderTier | null = null;
export function setRenderTier(value: RenderTier) { current = value; }
/** The session's resolved tier; null until main.ts has resolved it. */
export function renderTier(): RenderTier | null { return current; }

/** Startup GPU probe time (render/gpuProbe.ts) above which an Apple GPU may be a base M-series chip: M1 Max 1.76-1.86 ms,
 *  M1 Air 5.96 ms cool and 7.01 ms under heavy thermal pressure (airperf, 2026-11-03). A loaded Max (about ten agents,
 *  load average ~25) can still read above it, so the probe alone never demotes: the CPU must also look like a base chip. */
export const APPLE_REDUCED_PROBE_MS = 3.5;
/** navigator.hardwareConcurrency of a base M-series chip (M1/M2/M3: 8); the M1 Max reports 10. */
export const APPLE_BASE_MAX_CORES = 8;

/** GPU classes whose default is `reduced` instead of `medium`: by WebGPU adapter info, AMD GCN (Polaris/Vega, measured
 *  on the Radeon Pro 560X) and Intel's integrated `gen-*` graphics; Apple GPUs by the startup probe together with the
 *  CPU core count, since an M1 Air and an M1 Max report the same apple/metal-3. NVIDIA, AMD RDNA and the larger Apple
 *  chips keep medium. */
export function deviceQuality(gpu: { vendor?: string; architecture?: string } | null | undefined, fallback: QualityTier,
  probeMs?: number | null, cores?: number | null): QualityTier {
  if (fallback !== 'medium' || !gpu) return fallback;
  const vendor = (gpu.vendor ?? '').toLowerCase(), architecture = (gpu.architecture ?? '').toLowerCase();
  if ((vendor === 'amd' && architecture.startsWith('gcn')) || (vendor === 'intel' && architecture.startsWith('gen'))) return 'reduced';
  // Both signals: a slow probe and a base chip's core count (unknown cores keep medium).
  if (vendor === 'apple' && probeMs != null && Number.isFinite(probeMs) && probeMs > APPLE_REDUCED_PROBE_MS
    && cores != null && cores > 0 && cores <= APPLE_BASE_MAX_CORES) return 'reduced';
  return fallback;
}

/** Includes iPad desktop-mode browsers, without classifying touch laptops as phones. */
export function mobileDevice(userAgent: string, maxTouchPoints: number): boolean {
  return /Android|iPhone|iPad|iPod/i.test(userAgent) || (/Macintosh/i.test(userAgent) && maxTouchPoints > 1);
}

/** Bound play-mode fill rate to 720p on mobile and to the tier's `maxPixels` (1440p) on desktop. QA uses one pixel per CSS pixel. */
export function renderPixelRatio(width: number, height: number, dpr: number, qa: boolean, mobile = false, maxPixels = 2560 * 1440): number {
  const pixels = mobile ? 1280 * 720 : maxPixels;
  // No pixel cap (ultra): the display's own ratio.
  if (!qa && !mobile && pixels === Infinity) return dpr;
  return qa ? 1 : Math.min(dpr, mobile ? 1.5 : 2, Math.sqrt(pixels / Math.max(1, width * height)));
}
