export type QualityTier = 'mobile' | 'low' | 'medium' | 'high';

// Permanent user quality settings, not alternative rendering implementations.
// Vegetation budgets count nature-library trees/shrubs per LOD (full mesh, reduced mesh, impostor) across all tiles
// per frame; a full tree is about 2-10k triangles of bark and alpha-tested cards, a reduced one about 0.4-2k.
// parkedBodies caps parked cars with physics bodies (the nearest within 120 m; the rest stay visual).
// Mobile keeps the same ~0.6x-of-low ratio as its vegetation near range: phone physics cores are the
// slowest, and 24 still covers a ~14 m radius around the player in the densest 2.65 m-bay lot.
// `lights` are the fixed DynamicLighting point slots created at startup (render/lighting.ts).
export const QUALITY = {
  mobile: { parkedBodies: 24, vegetation: { near: 18, mid: 90, far: 350, nearInstances: 64, midInstances: 700, farInstances: 3000 }, shadowSize: 1024, splits: [90], aoScale: 0.5, aoSamples: 0, aoStrength: 0,
    bloomScale: 0.25, bloomStrength: 0, temporalSubpixel: false, reflectionScale: 0, reflectionSlowFps: 8, detail: 0.025, saturation: 0.98,
    lights: { point: 0 } },
  low: { parkedBodies: 40, vegetation: { near: 30, mid: 130, far: 450, nearInstances: 128, midInstances: 1500, farInstances: 5000 }, shadowSize: 1024, splits: [50, 180], aoScale: 0.5, aoSamples: 4, aoStrength: 0.22,
    bloomScale: 0.25, bloomStrength: 0.035, temporalSubpixel: false, reflectionScale: 0.25, reflectionSlowFps: 8, detail: 0.035, saturation: 0.98,
    lights: { point: 0 } },
  medium: { parkedBodies: 60, vegetation: { near: 50, mid: 180, far: 650, nearInstances: 300, midInstances: 2500, farInstances: 10000 }, shadowSize: 2048, splits: [16, 50, 200], aoScale: 0.5, aoSamples: 8, aoStrength: 0.28,
    bloomScale: 0.25, bloomStrength: 0.045, temporalSubpixel: true, reflectionScale: 0.5, reflectionSlowFps: 12, detail: 0.055, saturation: 0.97,
    lights: { point: 4 } },
  high: { parkedBodies: 80, vegetation: { near: 70, mid: 260, far: 850, nearInstances: 600, midInstances: 4000, farInstances: 16000 }, shadowSize: 2048, splits: [12, 32, 80, 240], aoScale: 0.75, aoSamples: 16, aoStrength: 0.3,
    bloomScale: 0.5, bloomStrength: 0.05, temporalSubpixel: true, reflectionScale: 0.75, reflectionSlowFps: 20, detail: 0.07, saturation: 0.97,
    lights: { point: 8 } },
} as const;

export function parseQuality(value: string | null, fallback: QualityTier = 'medium'): QualityTier {
  if (value === null) return fallback;
  if (value === 'mobile' || value === 'low' || value === 'medium' || value === 'high') return value;
  throw new Error(`bad ?quality= (mobile|low|medium|high): ${value}`);
}

/** Includes iPad desktop-mode browsers, without classifying touch laptops as phones. */
export function mobileDevice(userAgent: string, maxTouchPoints: number): boolean {
  return /Android|iPhone|iPad|iPod/i.test(userAgent) || (/Macintosh/i.test(userAgent) && maxTouchPoints > 1);
}

/** Bound play-mode fill rate to 720p on mobile and 1440p on desktop. QA uses one pixel per CSS pixel. */
export function renderPixelRatio(width: number, height: number, dpr: number, qa: boolean, mobile = false): number {
  const pixels = mobile ? 1280 * 720 : 2560 * 1440;
  return qa ? 1 : Math.min(dpr, mobile ? 1.5 : 2, Math.sqrt(pixels / Math.max(1, width * height)));
}
