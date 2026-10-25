// File discovery and structural checks for decoded collision tiles.
import { readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { COL_FLAG, MAT_FLAG, materialHasFlag } from '../../../game/src/shared/col/index.ts';
import type { ColTile } from '../../../game/src/shared/col/index.ts';

const TILE = 62.5;

export function collectColFiles(inputs: string[]): string[] {
  const out: string[] = [];
  const walk = (p: string) => {
    const st = statSync(p);
    if (st.isDirectory()) for (const e of readdirSync(p).sort()) walk(join(p, e));
    else if (p.endsWith('.col')) out.push(resolve(p));
  };
  for (const i of inputs) walk(i);
  return out;
}

export function tileConsistency(tile: ColTile): string[] {
  const h = tile.header;
  const errs: string[] = [];
  const [ox, oy, oz] = h.origin;
  if (ox !== h.tileI * TILE || oy !== 0 || oz !== h.tileJ * TILE) errs.push(`origin (${ox},${oy},${oz}) != (i*62.5, 0, j*62.5)`);
  const p = tile.positions;
  const eps = 1e-3;
  for (let t = 0; t < tile.indices.length; t++) {
    const v = tile.indices[t];
    for (let c = 0; c < 3; c++) {
      const x = p[v * 3 + c];
      if (x < h.bboxMin[c] - eps || x > h.bboxMax[c] + eps) {
        errs.push('bbox does not contain all vertices');
        t = tile.indices.length;
        break;
      }
    }
  }
  let needFix = false;
  for (const m of tile.materials) if (materialHasFlag(m, MAT_FLAG.FIX_EDGES)) { needFix = true; break; }
  if (needFix && !(h.flags & COL_FLAG.FIX_INTERNAL_EDGES)) errs.push('FIX_EDGES material present but FIX_INTERNAL_EDGES flag not set');
  let degenerate = 0;
  for (let t = 0; t < h.triangleCount; t++) {
    const a = tile.indices[t * 3], b = tile.indices[t * 3 + 1], c = tile.indices[t * 3 + 2];
    const ux = p[b * 3] - p[a * 3], uy = p[b * 3 + 1] - p[a * 3 + 1], uz = p[b * 3 + 2] - p[a * 3 + 2];
    const vx = p[c * 3] - p[a * 3], vy = p[c * 3 + 1] - p[a * 3 + 1], vz = p[c * 3 + 2] - p[a * 3 + 2];
    const area = 0.5 * Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx);
    if (area < 1e-6) degenerate++;
  }
  if (degenerate) errs.push(`${degenerate} degenerate triangles`);
  return errs;
}
