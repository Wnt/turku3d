import { afterEach, expect, test, vi } from 'vitest';
import { StreetLayer } from '../../src/gameplay/streetLayer';
import { parseRoadGraph } from '../../src/gameplay/roadGraph';

afterEach(() => vi.unstubAllGlobals());

function fixture() {
  const context = { setTransform: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn() };
  const canvas = { width: 0, height: 0, getContext: () => context };
  const create = vi.fn(() => canvas);
  vi.stubGlobal('document', { createElement: create });
  const graph = parseRoadGraph({ format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz',
    bbox: [-100000, -100000, 100000, 100000],
    nodes: [-10, 0, 10, 0, 990, 0, 1010, 0], edges: [[0, 1, 0, 0], [2, 3, 0, 0]] });
  const target = { drawImage: vi.fn<(image: unknown, x: number, z: number, width: number, height: number) => void>() };
  return { layer: new StreetLayer(graph), graph, context, canvas, create,
    target: target as unknown as CanvasRenderingContext2D, image: target.drawImage };
}

test('city growth does not allocate a city-sized bitmap; small movement reuses the viewport raster', () => {
  const f = fixture();
  expect(f.create).not.toHaveBeenCalled(); // Loading many graphs before the first frame allocates no pixels.
  for (let i = 0; i < 5; i++) f.layer.setGraph(f.graph);
  f.layer.draw(f.target, { x: 0, z: 0 }, 200, 720);
  expect(f.canvas.width * f.canvas.height * 4).toBeLessThanOrEqual(16 * 1024 ** 2);
  expect(f.context.moveTo).toHaveBeenCalledWith(-10, 0);
  expect(f.context.moveTo).not.toHaveBeenCalledWith(990, 0);
  const draws = f.context.stroke.mock.calls.length;
  f.layer.draw(f.target, { x: 20, z: 10 }, 200, 720);
  expect(f.context.stroke).toHaveBeenCalledTimes(draws);
  f.layer.draw(f.target, { x: 1000, z: 0 }, 200, 720);
  expect(f.context.moveTo).toHaveBeenCalledWith(990, 0);
  expect(f.create).toHaveBeenCalledTimes(1);
  // The whole rotated viewport remains inside the cached world rectangle after moving.
  const [, x, z, width, height] = f.image.mock.lastCall!;
  expect(x).toBeLessThanOrEqual(1000 - 200 / Math.SQRT2);
  expect(x + width).toBeGreaterThanOrEqual(1000 + 200 / Math.SQRT2);
  expect(z).toBeLessThanOrEqual(-200 / Math.SQRT2);
  expect(z + height).toBeGreaterThanOrEqual(200 / Math.SQRT2);
});

test('expanded routes, graph updates and disposal keep one bounded raster', () => {
  const f = fixture();
  f.layer.draw(f.target, { x: 0, z: 0 }, 200, 720);
  f.layer.draw(f.target, { x: 0, z: 0 }, 10000, 6000);
  expect(f.canvas.width).toBeLessThanOrEqual(2048);
  expect(f.canvas.height).toBeLessThanOrEqual(2048);
  expect(f.context.moveTo).toHaveBeenCalledWith(990, 0);
  const draws = f.context.stroke.mock.calls.length;
  f.layer.setGraph(f.graph);
  f.layer.draw(f.target, { x: 0, z: 0 }, 10000, 6000);
  expect(f.context.stroke.mock.calls.length).toBeGreaterThan(draws);
  expect(f.create).toHaveBeenCalledTimes(1);
  f.layer.dispose();
  expect(f.canvas.width * f.canvas.height).toBe(0);
});
