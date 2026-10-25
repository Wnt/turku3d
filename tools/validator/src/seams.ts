import { materialClass } from '../../../game/src/shared/col/materials.ts';
import type { ColTile } from '../../../game/src/shared/col/index.ts';

type Point = [number, number, number];
const bits = (p: Point) => Buffer.from(new Float64Array(p).buffer).toString('hex');

/** Compare referenced solid vertices AND triangle-edge partitions: equal point sets alone miss T junctions. */
export function checkSeams(tiles: ColTile[], size: number) {
  const grid = new Map(tiles.map(t => [`${t.header.tileI},${t.header.tileJ}`, t]));
  const failures: any[] = [];
  let sharedEdges = 0;
  function boundary(t: ColTile, axis: number, coordinate: number) {
    const vertices = new Map<string, Point>();
    const edges = new Map<string, Point[]>();
    const point = (i: number): Point => [0, 1, 2].map(c => t.header.origin[c] + t.positions[i * 3 + c]) as Point;
    for (let i = 0; i < t.indices.length; i += 3) {
      if (materialClass(t.materials[i / 3]) !== 'solid') continue;
      const p = [0, 1, 2].map(c => point(t.indices[i + c]));
      for (let c = 0; c < 3; c++) {
        const a = p[c], b = p[(c + 1) % 3];
        if (a[axis] !== coordinate) continue;
        vertices.set(bits(a), a);
        if (b[axis] === coordinate) edges.set([bits(a), bits(b)].sort().join(':'), [a, b]);
      }
    }
    return { vertices, edges };
  }
  for (const a of tiles) for (const [di, dj, axis] of [[1, 0, 0], [0, 1, 2]]) {
    const b = grid.get(`${a.header.tileI + di},${a.header.tileJ + dj}`);
    if (!b) continue;
    sharedEdges++;
    const coordinate = b.header.origin[axis];
    const left = boundary(a, axis, coordinate), right = boundary(b, axis, coordinate);
    const missing = <T>(x: Map<string, T>, y: Map<string, T>) => [...x].filter(([k]) => !y.has(k)).map(([, v]) => v);
    const onlyA = missing(left.vertices, right.vertices), onlyB = missing(right.vertices, left.vertices);
    const edgesOnlyA = missing(left.edges, right.edges), edgesOnlyB = missing(right.edges, left.edges);
    if (onlyA.length || onlyB.length || edgesOnlyA.length || edgesOnlyB.length || !left.edges.size || !right.edges.size) {
      failures.push({ tiles: [a.id, b.id], axis: axis === 0 ? 'x' : 'z', coordinate,
        at: axis === 0 ? [coordinate, a.header.origin[2] + size / 2] : [a.header.origin[0] + size / 2, coordinate],
        onlyA, onlyB, edgesOnlyA, edgesOnlyB, reason: 'shared solid boundary is missing or not bitwise identical' });
    }
  }
  return { status: failures.length ? 'fail' : sharedEdges ? 'pass' : 'skipped', sharedEdges, failures };
}
