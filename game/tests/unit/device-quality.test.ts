import { expect, test } from 'vitest';
import { QUALITY, deviceQuality, parseQuality, renderPixelRatio } from '../../src/render/quality';
import { AdaptiveResolution } from '../../src/render/adaptiveResolution';
import { TRAFFIC_CAPS } from '../../src/traffic/constants';
import { WALKER_CAPS } from '../../src/world/walkers/index';

test('the M1 Max keeps medium, its 1440p-capped pixel ratio and the 0.5 adaptive floor', () => {
  // Chrome on the M1 Max reports vendor apple, architecture metal-3 (capture meta.gpu); full screen is 1728x941 at DPR 2.
  expect(deviceQuality({ vendor: 'apple', architecture: 'metal-3' }, 'medium')).toBe('medium');
  // Its play floor (AdaptiveResolution 0.5) is the pixel ratio its recorded drives carry.
  expect(renderPixelRatio(1728, 941, 2, false, false, QUALITY.medium.maxPixels)).toBe(renderPixelRatio(1728, 941, 2, false));
  expect(renderPixelRatio(1728, 941, 2, false, false, QUALITY.medium.maxPixels) * 0.5).toBeCloseTo(0.7528432350710724, 12);
  expect(QUALITY.medium).toMatchObject({ shadowSize: 2048, splits: [16, 50, 200], reflectionScale: 0.5, reflectionSlowFps: 12,
    bloomStrength: 0.045, temporalSubpixel: true, lights: { point: 4 }, maxPixels: 2560 * 1440,
    vegetation: { near: 50, mid: 180, far: 650, nearInstances: 300, farInstances: 10000, barrierInstances: 2500 } });
  for (const gpu of [{ vendor: 'nvidia', architecture: 'ampere' }, { vendor: 'amd', architecture: 'rdna-3' }, null, undefined])
    expect(deviceQuality(gpu, 'medium')).toBe('medium');
});

test('GCN and Intel gen GPUs default to reduced; explicit and mobile tiers are kept', () => {
  expect(deviceQuality({ vendor: 'amd', architecture: 'gcn-4' }, 'medium')).toBe('reduced');
  expect(deviceQuality({ vendor: 'intel', architecture: 'gen-9' }, 'medium')).toBe('reduced');
  expect(deviceQuality({ vendor: 'amd', architecture: 'gcn-4' }, 'mobile')).toBe('mobile');
  expect(deviceQuality({ vendor: 'amd', architecture: 'gcn-4' }, 'high')).toBe('high');
  expect(parseQuality('reduced')).toBe('reduced');
  // The 2880x1800 Windows screen (1920x1200 CSS at DPR 1.5): 1080p instead of 1440p before the adaptive scale.
  expect(renderPixelRatio(1920, 1200, 1.5, false, false, QUALITY.reduced.maxPixels)).toBeCloseTo(Math.sqrt(1920 * 1080 / (1920 * 1200)));
  expect(renderPixelRatio(1920, 1200, 1.5, false, true, QUALITY.reduced.maxPixels)).toBeCloseTo(Math.sqrt(1280 * 720 / (1920 * 1200)));
});

test('reduced changes rendering only: simulation caps and the rest of the tier are medium', () => {
  const { vegetation, reflectionScale, reflectionSlowFps, riverReflection, maxPixels, grass, splits, lights, ...rest } = QUALITY.reduced;
  const { vegetation: v, reflectionScale: _r, reflectionSlowFps: _s, riverReflection: _m, maxPixels: _p, grass: g, splits: sp, lights: l, ...medium } = QUALITY.medium;
  expect(rest).toEqual(medium);
  expect({ grass, splits, lights }).toEqual({ grass: g, splits: sp, lights: l });
  expect(vegetation).toEqual({ ...v, mid: 100 });
  expect({ reflectionScale, reflectionSlowFps, riverReflection, maxPixels }).toEqual({ reflectionScale: 0.25, reflectionSlowFps: 8, riverReflection: 'ssr', maxPixels: 1920 * 1080 });
  expect(QUALITY.medium.riverReflection).toBe('planar');
  expect(QUALITY.reduced.parkedBodies).toBe(QUALITY.medium.parkedBodies);
  expect(TRAFFIC_CAPS.reduced).toBe(TRAFFIC_CAPS.medium);
  expect(WALKER_CAPS.reduced).toBe(WALKER_CAPS.medium);
});

test('sustained frames slower than the hitch limit step the resolution down; isolated hitches do not', () => {
  const run = (pattern: number[], count: number) => {
    const controller = new AdaptiveResolution(60);
    let time = 0;
    for (let n = 0; n < count; n++) controller.update(time += pattern[n % pattern.length]);
    return controller.scale;
  };
  expect(run([200], 100)).toBe(0.5); // 5 FPS: ~20 s, one step per 6 slow frames and at most one per second
  expect(run([200], 7)).toBe(0.9);
  expect(run([16.7, 16.7, 16.7, 16.7, 16.7, 120], 2000)).toBe(1); // a hitch every sixth frame is not a run
  expect(run([35], 900)).toBe(0.5); // ordinary slow delivery, unchanged
});

test('the resolved tier is readable for streaming residency', async () => {
  const { renderTier, setRenderTier } = await import('../../src/render/quality');
  const gpu = { vendor: 'amd', architecture: 'gcn-4' };
  setRenderTier({ tier: 'reduced', source: 'device', gpu });
  expect(renderTier()).toEqual({ tier: 'reduced', source: 'device', gpu });
});

test('a 40 FPS resolution target keeps full resolution at the goal and steps down only below it', () => {
  const run = (target: number, ms: number, count = 900) => {
    const controller = new AdaptiveResolution(target);
    let time = 0;
    for (let n = 0; n < count; n++) controller.update(time += ms);
    return controller.scale;
  };
  expect(run(60, 24)).toBe(0.5); // today's desktop target: 41 FPS still reads as slow
  expect(run(40, 24)).toBe(1);
  expect(run(40, 32)).toBe(0.5);
});

test('leaf A/B variants parse strictly', async () => {
  const { parseLeafVariant } = await import('../../src/world/nature/materials');
  expect(parseLeafVariant(null)).toBe(null);
  expect(parseLeafVariant('single')).toBe('single');
  expect(parseLeafVariant('noshadow')).toBe('noshadow');
  expect(() => parseLeafVariant('cheap')).toThrow();
});
