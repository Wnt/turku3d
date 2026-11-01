import { describe, expect, it } from 'vitest';
import { benchPose, CameraBench, classifyTarget, type BenchHost } from '../../src/qa/bench';
import { parseParams } from '../../src/core/params';
import streetCameras from '../../src/qa/street-cameras.json';

describe('street camera bench pose', () => {
  it('converts every listed camera to its QA pose with the COMMON.md transform', () => {
    for (const c of streetCameras.cameras) {
      const pose = benchPose(c.id, null);
      expect(pose.qa.E).toBeCloseTo(c.pos[0] + 23459500, 6);
      expect(pose.qa.N).toBeCloseTo(6704500 - c.pos[2], 6);
      expect(pose.qa.H).toBe(c.pos[1]);
      expect([pose.qa.yawDeg, pose.qa.pitchDeg, pose.qa.fovDeg]).toEqual([c.yaw_deg, c.pitch_deg, c.fov]);
      // The cam string in the JSON is the same pose for a manual ?cam= run.
      const cam = c.cam.split(',').map(Number);
      expect(cam[0]).toBeCloseTo(pose.qa.E, 2);
      expect(cam[1]).toBeCloseTo(pose.qa.N, 2);
      expect(cam.slice(2)).toEqual([pose.qa.H, pose.qa.yawDeg, pose.qa.pitchDeg, pose.qa.fovDeg]);
    }
  });

  it('uses the listed local times on the fixed render date', () => {
    expect(benchPose('riverside', null).time.toISOString()).toBe('2026-10-15T10:00:00.000Z');
    expect(benchPose('riverside', '22:00').time.toISOString()).toBe('2026-10-15T19:00:00.000Z');
    expect(() => benchPose('riverside', '25:00')).toThrow(/at=/);
    expect(() => benchPose('nowhere', null)).toThrow(/riverside/);
  });

  it('is selected by ?bench=camera&id= and keeps the medium default', () => {
    const p = parseParams('?bench=camera&id=kupittaa&at=22:00');
    expect(p.bench?.id).toBe('kupittaa');
    expect(p.qa?.fovDeg).toBe(65);
    expect(p.timeGiven).toBe(true);
    expect(p.time.toISOString()).toBe('2026-10-15T19:00:00.000Z');
    expect(p.quality).toBe('medium');
    expect(parseParams('?bench=camera&id=shopping&quality=high').quality).toBe('high');
    expect(() => parseParams('?bench=route&id=shopping')).toThrow(/bench=/);
    expect(() => parseParams('?bench=camera&id=shopping&cam=1,2,3,4,5,60')).toThrow(/cam=/);
    expect(parseParams('?cam=23459668,6704254,5,0,0,60').bench).toBeNull();
  });
});

describe('bench draw attribution', () => {
  const prepass = {}, beauty = {};
  it('classifies draws by render target', () => {
    expect(classifyTarget(prepass, { prepass, beauty })).toBe('prepass');
    expect(classifyTarget(beauty, { prepass, beauty })).toBe('beauty');
    expect(classifyTarget({ depthTexture: { name: 'ShadowDepthTexture' } }, { prepass, beauty })).toBe('csm');
    expect(classifyTarget({ texture: { name: 'output' } }, { prepass, beauty }, { type: 'OrthographicCamera' })).toBe('post');
    expect(classifyTarget({ texture: { name: '' } }, { prepass, beauty }, { type: 'PerspectiveCamera' })).toBe('reflection');
    expect(classifyTarget(null, { prepass, beauty }, { type: 'OrthographicCamera' })).toBe('post');
  });

  it('warms, measures a fixed window and reports per-frame draws', async () => {
    const drawn: string[] = [];
    const backend = { draw: (ro: { context: { renderTarget: unknown } }) => { drawn.push(String(ro.context.renderTarget === beauty)); } };
    const paused: boolean[] = [];
    let resets = 0, flushed = 0, pipelines = 10, clock = 0, loading = 'a';
    const host: BenchHost = {
      backend, pipelines: () => pipelines, passes: () => ({ prepass, beauty }), pause: v => paused.push(v),
      info: () => ({ tiles: ['t0_0_0'] }), now: () => clock, signature: () => loading,
      perf: {
        reset: () => { resets++; }, flush: async () => { flushed++; },
        snapshot: () => ({ gpuMs: { p50: 2 }, cpuMs: { p50: 1 }, workMs: {}, frameMs: {}, gpuStatus: 'timestamp-query',
          gpuSkipped: 0, drawCalls: 4, triangles: 1000, windowFrames: 3, resolution: [100, 50], backend: 'webgpu' }),
      },
    };
    const bench = new CameraBench(host, benchPose('riverside', null), 'medium', 2, 3, 1000);
    const frame = async (draws: number) => {
      for (let i = 0; i < draws; i++) backend.draw({ context: { renderTarget: i % 2 ? beauty : prepass }, object: { isInstancedMesh: true, count: 5 } } as never);
      return bench.frame();
    };
    // Settle: the scene must stay unchanged for 1000 ms of wall time; a change restarts the wait.
    expect(await frame(9)).toBeNull();
    clock = 900; loading = 'b';
    expect(await frame(9)).toBeNull();
    clock = 1800;
    expect(await frame(9)).toBeNull();
    clock = 1950;
    expect(await frame(9)).toBeNull(); // 1050 ms unchanged: settled, warm-up starts with the next frame
    expect(resets).toBe(0);
    expect(await frame(9)).toBeNull(); // warm 1 (not counted)
    expect(await frame(9)).toBeNull(); // warm 2: measurement starts after this frame
    expect(resets).toBe(1);
    pipelines = 12;
    expect(await frame(4)).toBeNull();
    expect(await frame(4)).toBeNull();
    const result = await frame(4);
    expect(drawn).toHaveLength(66); // the wrapper always forwards
    expect(result).toMatchObject({ id: 'riverside', at: '13:00', quality: 'medium', frames: 3,
      submittedDraws: { total: 4, prepass: 2, beauty: 2, csm: 0, reflection: 0, post: 0 }, instances: 20,
      pipelinesAfterWarmup: 10, pipelinesAfterMeasure: 12, adaptiveScale: 1, tiles: ['t0_0_0'],
      settleMs: 1950, settleTimedOut: false });
    expect(flushed).toBe(1);
    expect(paused).toEqual([true, false]);
    expect(await frame(1)).toBeNull(); // done: further frames are ignored
  });
});
