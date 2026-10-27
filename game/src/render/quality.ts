export type QualityTier = 'low' | 'medium' | 'high';

// Permanent user quality settings, not alternative rendering implementations.
// Vegetation budgets count submitted prototype/material instances across all tiles per frame.
export const QUALITY = {
  low: { vegetation: { near: 40, mid: 130, far: 450, nearInstances: 384, midInstances: 1800, farInstances: 5000 }, shadowSize: 1024, splits: [50, 180], aoScale: 0.5, aoSamples: 4, aoStrength: 0.22,
    bloomScale: 0.25, bloomStrength: 0.035, temporalSubpixel: false, reflectionScale: 0.25, detail: 0.035, saturation: 0.98 },
  medium: { vegetation: { near: 65, mid: 190, far: 650, nearInstances: 1024, midInstances: 4000, farInstances: 10000 }, shadowSize: 2048, splits: [16, 50, 200], aoScale: 0.5, aoSamples: 8, aoStrength: 0.28,
    bloomScale: 0.25, bloomStrength: 0.045, temporalSubpixel: true, reflectionScale: 0.5, detail: 0.055, saturation: 0.97 },
  high: { vegetation: { near: 90, mid: 260, far: 850, nearInstances: 2048, midInstances: 6000, farInstances: 16000 }, shadowSize: 2048, splits: [12, 32, 80, 240], aoScale: 0.75, aoSamples: 16, aoStrength: 0.3,
    bloomScale: 0.5, bloomStrength: 0.05, temporalSubpixel: true, reflectionScale: 0.75, detail: 0.07, saturation: 0.97 },
} as const;

export function parseQuality(value: string | null): QualityTier {
  if (value === null) return 'medium';
  if (value === 'low' || value === 'medium' || value === 'high') return value;
  throw new Error(`bad ?quality= (low|medium|high): ${value}`);
}

/** Bound play-mode fill rate to 1440p. QA is exactly one output pixel per CSS pixel. */
export function renderPixelRatio(width: number, height: number, dpr: number, qa: boolean): number {
  return qa ? 1 : Math.min(dpr, 2, Math.sqrt((2560 * 1440) / Math.max(1, width * height)));
}
