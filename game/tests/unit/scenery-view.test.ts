import { expect, test } from 'vitest';
import { sceneryTiles, skylinePriority, type SceneryView } from '../../src/world/sceneryView';

const heading = 273 * Math.PI / 180;
const view: SceneryView = { x: 3751.26, y: 164.15, z: 430.05, dx: Math.sin(heading), dz: -Math.cos(heading), halfAngle: .8 };
test('fixed west-facing six-kilometre sector includes the chimney and excludes the rear half', () => {
  const wanted = sceneryTiles(6000, view);
  expect(wanted).toContain('t0_-6_5');
  expect(wanted).not.toContain('t0_30_1');
  expect(wanted.length).toBeLessThan(600);
  expect(sceneryTiles(5000, view)).not.toContain('t0_-6_5');
});
test('skyline priority brings tall distant silhouettes ahead of foreground terrain', () => {
  const source = (tile: string, height: number) => ({ tile, h: [0, height] as [number, number], key: 'test', source_sha256: 'test' });
  expect(skylinePriority(source('t0_-6_5', 107), view)).toBeGreaterThan(skylinePriority(source('t0_9_1', 30), view));
});
