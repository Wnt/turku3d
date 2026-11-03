import { expect, test } from 'vitest';
import { QUALITY, deviceQuality, parseQuality, renderPixelRatio } from '../../src/render/quality';
import { AdaptiveResolution } from '../../src/render/adaptiveResolution';
import { TRAFFIC_CAPS } from '../../src/traffic/constants';
import { WALKER_CAPS } from '../../src/world/walkers/index';

test('the M1 Max keeps medium, its 1440p-capped pixel ratio and the 0.5 adaptive floor', () => {
  // Chrome on the M1 Max reports vendor apple, architecture metal-3 (capture meta.gpu); full screen is 1728x941 at DPR 2.
  expect(deviceQuality({ vendor: 'apple', architecture: 'metal-3' }, 'medium')).toBe('medium');
  expect(renderPixelRatio(1728, 941, 2, false) * QUALITY.medium.resolutionFloor).toBeCloseTo(0.7528432350710724, 12);
  expect(QUALITY.medium).toMatchObject({ shadowSize: 2048, splits: [16, 50, 200], reflectionScale: 0.5, reflectionSlowFps: 12,
    bloomStrength: 0.045, temporalSubpixel: true, lights: { point: 4 }, resolutionFloor: 0.5,
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
  // The 2880x1800 Windows screen at DPR 1.5: the 1440p cap, then down to 0.4 of it.
  expect(renderPixelRatio(1920, 1200, 1.5, false)).toBeCloseTo(Math.sqrt(2560 * 1440 / (1920 * 1200)));
});

test('reduced changes rendering only: simulation caps and the rest of the tier are medium', () => {
  const { vegetation, reflectionScale, reflectionSlowFps, resolutionFloor, grass, splits, lights, ...rest } = QUALITY.reduced;
  const { vegetation: v, reflectionScale: _r, reflectionSlowFps: _s, resolutionFloor: _f, grass: g, splits: sp, lights: l, ...medium } = QUALITY.medium;
  expect(rest).toEqual(medium);
  expect({ grass, splits, lights }).toEqual({ grass: g, splits: sp, lights: l });
  expect(vegetation).toEqual({ ...v, mid: 100 });
  expect({ reflectionScale, reflectionSlowFps, resolutionFloor }).toEqual({ reflectionScale: 0.25, reflectionSlowFps: 8, resolutionFloor: 0.4 });
  expect(QUALITY.reduced.parkedBodies).toBe(QUALITY.medium.parkedBodies);
  expect(TRAFFIC_CAPS.reduced).toBe(TRAFFIC_CAPS.medium);
  expect(WALKER_CAPS.reduced).toBe(WALKER_CAPS.medium);
});

test('the adaptive resolution floor is per tier', () => {
  const run = (floor?: number) => {
    const controller = new AdaptiveResolution(60, floor);
    let time = 0;
    for (let n = 0; n < 900; n++) controller.update(time += 35);
    return controller.scale;
  };
  expect(run()).toBe(0.5);
  expect(run(QUALITY.medium.resolutionFloor)).toBe(0.5);
  expect(run(QUALITY.reduced.resolutionFloor)).toBe(0.4);
});

test('the resolved tier is readable for streaming residency', async () => {
  const { renderTier, setRenderTier } = await import('../../src/render/quality');
  const gpu = { vendor: 'amd', architecture: 'gcn-4' };
  setRenderTier({ tier: 'reduced', source: 'device', gpu });
  expect(renderTier()).toEqual({ tier: 'reduced', source: 'device', gpu });
});
