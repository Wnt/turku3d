import { test, expect } from 'vitest';
import { driveSegments, interpolateDrive, type DrivePose } from '../../src/qa/drive';
import { parseParams } from '../../src/core/params';
const pose = (t: number, paused = false): DrivePose => ({ t, paused, mode: 'car', id: 'player', E: 23459500 + t, N: 6704500, H: 0,
 feet: [t, 0, 0], vel: [1, 0, 0], cam: [t, 1, 2, 0, 0, 0, 1, 60], car: [t, 0, 0, 0, 0, 0, 1, 1, 0, 0], speed: 1, lights: 1 });
test('drive params validate capture paths and one-based segments', () => {
 expect(parseParams('?bench=drive&capture=drive-01&segment=1').drive).toEqual({ capture: 'drive-01', segment: 1 });
 for (const search of ['?bench=drive&capture=../x', '?bench=drive&capture=x&segment=0', '?bench=drive&capture=x&segment=1.5']) expect(() => parseParams(search)).toThrow();
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
