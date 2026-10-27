import { CAR, FOOT, parseRoadGraph } from '../../src/gameplay/roadGraph';
import { StreetRouter } from '../../src/gameplay/router';

const STREET = FOOT | CAR | 2 << 3;

/** A k x k grid of 20 m street blocks from (0, 0) (unit and perf router tests). */
export function streetGrid(k: number): StreetRouter {
  const nodes: number[] = [], edges: number[][] = [];
  for (let j = 0; j < k; j++) for (let i = 0; i < k; i++) {
    nodes.push(i * 20, j * 20);
    if (i) edges.push([j * k + i - 1, j * k + i, STREET, 20]);
    if (j) edges.push([(j - 1) * k + i, j * k + i, STREET, 20]);
  }
  const size = (k - 1) * 20;
  return new StreetRouter(parseRoadGraph({ format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', bbox: [0, 0, size, size], nodes, edges }));
}
