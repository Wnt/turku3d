import { test, expect, vi, afterEach } from 'vitest';
import { driveSegments, interpolateDrive, type DrivePose } from '../../src/qa/drive';
import { parseParams } from '../../src/core/params';
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const pose = (t: number, paused = false): DrivePose => ({ t, paused, mode: 'car', id: 'player', E: 23459500 + t, N: 6704500, H: 0,
 feet: [t, 0, 0], vel: [1, 0, 0], cam: [t, 1, 2, 0, 0, 0, 1, 60], car: [t, 0, 0, 0, 0, 0, 1, 1, 0, 0], speed: 1, lights: 1 });
test('drive params validate capture paths and one-based segments', () => {
 expect(parseParams('?bench=drive&capture=drive-01&segment=1').drive).toEqual({ capture: 'drive-01', segment: 1, view: 'recorded' });
 expect(parseParams('?bench=drive&capture=drive-01&view=live').drive).toEqual({ capture: 'drive-01', segment: 1, view: 'live' });
 for (const search of ['?bench=drive&capture=../x', '?bench=drive&capture=x&segment=0', '?bench=drive&capture=x&segment=1.5',
   '?bench=drive&capture=x&view=native']) expect(() => parseParams(search)).toThrow();
});
test('legacy recordings segment on pause transitions, including long frame gaps', () => {
 const segments = driveSegments([pose(0, true), pose(10), pose(1000), pose(1100, true), pose(1200), pose(1300)]);
 expect(segments.map(s => [s.start, s.end])).toEqual([[10, 1000], [1200, 1300]]);
});
test('marks delimit quick pause/resume transitions between pose samples', () => {
 const segments = driveSegments([pose(10), pose(20), pose(30), pose(40)], [{ k: 'pause', t: 25 }, { k: 'resume', t: 26 }]);
 expect(segments.map(s => [s.start, s.end])).toEqual([[10, 20], [30, 40]]);
});
test('camera and car interpolate recorded time and shortest arc quaternion', () => {
 const a = pose(0), b = pose(100); b.cam = [100, 3, 4, 0, 0, 0, -1, 80];
 const mid = interpolateDrive(a, b, 50);
 expect(mid.cam).toEqual([50, 2, 3, 0, 0, 0, 1, 70]); expect(mid.car![0]).toBe(50); expect(mid.feet[0]).toBe(50);
});

test('replaying a replay uses its source scene epoch, not the wall time of the later recording', async () => {
 const { driveEpoch, driveClockMode } = await import('../../src/qa/drive');
 const meta = { start: { wall: 900000, performanceNow: 1000 }, replay: { epochMs: 100000, seconds: 12 },
   clockMode: { kind: 'date', date: '2026-10-15T11:00:00.000Z' } };
 expect(driveEpoch({ meta, start: 4000 })).toBe(115000);
 expect(driveEpoch({ meta: { ...meta, replay: null }, start: 4000 })).toBe(903000);
 expect(driveClockMode(meta)).toEqual({ kind: 'date', date: new Date(meta.clockMode.date) });
 expect(driveClockMode({ params: { timeGiven: false, cycleMinutes: 30 } })).toEqual({ kind: 'cycle', periodMs: 1800000 });
 expect(driveClockMode({ params: { timeGiven: true, hour: 14 }, hour: 14.5 })).toEqual({ kind: 'hour', hour: 14.5 });
});

test('beacon arrival order does not split or reverse legacy or marked drive segments', () => {
 const poses = [pose(40), pose(0, true), pose(50), pose(30, true), pose(20), pose(10)];
 expect(driveSegments(poses).map(s => s.poses.map(p => p.t))).toEqual([[10, 20], [40, 50]]);
 expect(driveSegments(poses, [{ k: 'resume', t: 35 }, { k: 'pause', t: 30 }, { k: 'resume', t: 5 }])
   .map(s => s.poses.map(p => p.t))).toEqual([[10, 20], [40, 50]]);
 expect(poses.map(p => p.t)).toEqual([40, 0, 50, 30, 20, 10]);
});

test('replay uses the last resolution sample before segment start, despite late beacon delivery', async () => {
 const { loadDrive } = await import('../../src/qa/drive');
 vi.stubEnv('VITE_PUBLIC_RELEASE', '0');
 vi.stubGlobal('location', { hostname: 'localhost', search: '' });
 const documents: Record<string, string> = {
   'meta.json': JSON.stringify({ pixelRatio: 2, viewport: [1600, 800] }),
   'poses.jsonl': [pose(20), pose(0, true), pose(10), pose(30, true)].map(p => JSON.stringify(p)).join('\n'),
   'marks.jsonl': '',
   'stats.jsonl': [
     { t: 25, pixelRatio: .5, viewport: [400, 300] },
     { t: 0, pixelRatio: .85, viewport: [1600, 800] },
     { t: 12, pixelRatio: .75, viewport: [1600, 800] },
   ].map(s => JSON.stringify(s)).join('\n'),
 };
 vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, text: async () => documents[url.split('/').at(-1)!] })));
 const drive = await loadDrive('out-of-order', 1);
 expect(drive.poses.map(p => p.t)).toEqual([10, 20]);
 expect(drive.settings).toEqual({ pixelRatio: .85, viewport: [1600, 800] });
});

test('explicit local release=0 permits capture while public builds and Pages origins remain blocked', async () => {
 const { loadDrive } = await import('../../src/qa/drive');
 const { captureAllowed } = await import('../../src/telemetry/capture');
 const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
 vi.stubGlobal('location', { hostname: 'localhost', search: '' });
 vi.stubEnv('VITE_PUBLIC_RELEASE', '0'); expect(captureAllowed()).toBe(true);
 vi.stubEnv('VITE_PUBLIC_RELEASE', '1'); expect(captureAllowed()).toBe(false);
 await expect(loadDrive('drive-01')).rejects.toThrow('local only'); expect(fetch).not.toHaveBeenCalled();
 vi.stubEnv('VITE_PUBLIC_RELEASE', '0');
 vi.stubGlobal('location', { hostname: 'example.github.io', search: '?capture=1' });
 expect(captureAllowed()).toBe(false);
 await expect(loadDrive('drive-01')).rejects.toThrow('local only'); expect(fetch).not.toHaveBeenCalled();
});
