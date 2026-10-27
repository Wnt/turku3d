// A routed fake of the tools/dev/coarse-tiles service for the synthetic dev tiles (ground H 2): flat draft tiles with
// their .col cells and a green 250 m quad, an optional street graph; every other tile answers "no coverage" (404).
import type { Page } from '@playwright/test';
import { Document, NodeIO } from '@gltf-transform/core';
import { writeCol } from '../../scripts/colWriter.mjs';

export const G = 2;
const COL = 62.5, L0 = 250, N = 4;

/** One flat collision cell at H 2: an N x N grid of road triangles. */
function flatCol(i: number, j: number) {
  const pos: number[] = [], idx: number[] = [];
  const s = COL / N;
  for (let b = 0; b <= N; b++) for (let a = 0; a <= N; a++) pos.push(a * s, G, b * s);
  for (let b = 0; b < N; b++) for (let a = 0; a < N; a++) {
    const v = b * (N + 1) + a;
    idx.push(v, v + N + 1, v + 1, v + 1, v + N + 1, v + N + 2);
  }
  return writeCol({ i, j, positions: new Float32Array(pos), indices: new Uint32Array(idx), materials: new Uint8Array(idx.length / 3).fill(1),
    prims: [], flags: 0, meta: { tile: `c_${i}_${j}`, code: 'e2e-draft' } });
}

/** The draft's visual: one green 250 m quad at H 2 under a node translated to the tile origin. */
async function flatGlb(ti: number, tj: number) {
  const doc = new Document();
  const buffer = doc.createBuffer();
  const position = doc.createAccessor().setType('VEC3').setBuffer(buffer)
    .setArray(new Float32Array([0, G, 0, L0, G, 0, L0, G, L0, 0, G, L0]));
  const indices = doc.createAccessor().setType('SCALAR').setBuffer(buffer).setArray(new Uint32Array([0, 3, 1, 1, 3, 2]));
  const material = doc.createMaterial('draft').setBaseColorFactor([0.25, 0.6, 0.3, 1]).setRoughnessFactor(1);
  const mesh = doc.createMesh('ground').addPrimitive(doc.createPrimitive().setAttribute('POSITION', position).setIndices(indices).setMaterial(material));
  const node = doc.createNode('tile').setMesh(mesh).setTranslation([ti * L0, 0, tj * L0]);
  doc.createScene().addChild(node);
  return Buffer.from(await new NodeIO().writeBinary(doc));
}

/** The 16 cells of L0 tile (ti, tj). */
export const cells = (ti: number, tj: number) => Array.from({ length: 16 }, (_, k) => [ti * 4 + (k % 4), tj * 4 + Math.floor(k / 4)] as const);

interface FakeDraft {
  /** Collision cells the draft ships (a draft missing one is rejected by the game). */
  cells: (readonly [number, number])[];
  /** A street graph (turku3d-roadgraph JSON) shipped with it. */
  roads?: { json: unknown; nodes: number; edges: number };
}

/** Routes /coarse/* of `page` to a fake service serving `drafts` (L0 id -> draft). */
export async function routeCoarse(page: Page, drafts: Record<string, FakeDraft>) {
  const files = new Map<string, Buffer>();
  const fragments = new Map<string, unknown>();
  for (const [id, d] of Object.entries(drafts)) {
    const [ti, tj] = id.split('_').slice(1).map(Number);
    const glb = await flatGlb(ti, tj);
    files.set(`/coarse/${id}/vis.glb`, glb);
    const collision: Record<string, unknown> = {};
    for (const [i, j] of d.cells) {
      const buf = flatCol(i, j);
      files.set(`/coarse/${id}/col/c_${i}_${j}.col`, buf);
      collision[`c_${i}_${j}`] = { url: `col/c_${i}_${j}.col`, sha256: '', bytes: buf.length, tris: 2 * N * N, prims: 0, h: [G, G], flags: 0 };
    }
    const roads = d.roads ? Buffer.from(JSON.stringify(d.roads.json)) : null;
    if (roads) files.set(`/coarse/${id}/roads.json`, roads);
    fragments.set(id, { format: 'turku3d-coarse-tile', version: 1, coarse: true, tile: id,
      visual: { [id]: { url: 'vis.glb', sha256: '', bytes: glb.length, level: 0, tris: 2, h: [G, G], coarse: true } }, collision,
      roads: roads && { url: 'roads.json', sha256: '', bytes: roads.length, nodes: d.roads!.nodes, edges: d.roads!.edges } });
  }
  await page.route(/\/coarse\//, route => {
    const url = new URL(route.request().url());
    const json = (status: number, body: unknown) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/coarse/status') return json(200, { state: 'idle' });
    if (url.pathname === '/coarse/want') return json(200, {});
    const m = /^\/coarse\/(t0_-?\d+_-?\d+)\/manifest\.json$/.exec(url.pathname);
    if (m) return fragments.has(m[1]) ? json(200, fragments.get(m[1])) : json(404, { state: 'failed', error: 'no coverage' });
    const file = files.get(url.pathname);
    return file ? route.fulfill({ status: 200, contentType: 'application/octet-stream', body: file }) : json(404, { error: 'not found' });
  });
}
