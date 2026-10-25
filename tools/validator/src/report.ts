import { readFileSync } from 'node:fs';
import { extname } from 'node:path';

const escape = (v: unknown) => String(v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

/** Self-contained, offline failure map. Ortho bounds are engine [x0,z0,x1,z1], north at the top. */
export function htmlReport(report: any, ortho?: string, orthoBounds?: number[]) {
  const bounds = report.aoi.bbox as number[];
  const [x0, z0, x1, z1] = bounds;
  const w = x1 - x0, h = z1 - z0;
  const markers: string[] = [];
  const mark = (point: number[], label: string) => {
    if (!point.every(Number.isFinite)) return;
    const [x, z] = point.length === 3 ? [point[0], point[2]] : point;
    markers.push(`<circle cx="${x}" cy="${z}" r="${Math.max(w, h) / 180}" fill="#ff4d64" stroke="white" stroke-width="${w / 1200}"><title>${escape(label)}</title></circle>`);
  };
  function walk(c: any, label: string) {
    if (c.perTile) { for (const t of c.perTile) walk(t, `${label} / ${t.tile}`); return; }
    if (c.status !== 'fail') return;
    for (const p of [...(c.missSamples ?? []), ...(c.voidSamples ?? [])]) mark(p, label);
    for (const f of [...(c.failures ?? []), ...(c.samples ?? []), ...(c.untestedFootprints ?? []), ...(c.flights ?? []).filter((f: any) => !f.ok)]) {
      const point = f.at ?? f.bottom ?? (f.x !== undefined ? [f.x, f.z] : f.start);
      if (point) mark(point, `${label}: ${JSON.stringify(f)}`);
      for (const p of [...(f.onlyA ?? []), ...(f.onlyB ?? [])]) mark(p, label);
    }
  }
  for (const [name, c] of Object.entries(report.checks)) walk(c, name);
  let backdrop = '';
  if (ortho) {
    if (!orthoBounds || orthoBounds.length !== 4 || !orthoBounds.every(Number.isFinite)) throw new Error('--ortho requires --ortho-bounds x0,z0,x1,z1');
    const ext = extname(ortho).toLowerCase(), mime = ext === '.png' ? 'image/png' : ext === '.webp' ? 'image/webp' : ext === '.jpg' || ext === '.jpeg' ? 'image/jpeg' : undefined;
    if (!mime) throw new Error('ortho must be a PNG, JPEG or WebP image');
    const [a, b, c, d] = orthoBounds;
    backdrop = `<image x="${a}" y="${b}" width="${c - a}" height="${d - b}" preserveAspectRatio="none" href="data:${mime};base64,${readFileSync(ortho).toString('base64')}"/>`;
  }
  const grid = report.tiles.filter((t: any) => /^c_-?\d+_-?\d+$/.test(t.id)).map((t: any) => {
    const [, i, j] = t.id.split('_').map(Number);
    // Tile bounds recorded by the validator avoid a second grid configuration here.
    const b = t.bounds;
    return b ? `<rect x="${b[0]}" y="${b[1]}" width="${b[2]-b[0]}" height="${b[3]-b[1]}" fill="none" stroke="#89a" stroke-width="${w / 1500}"><title>${escape(t.id)} (${i}, ${j})</title></rect>` : '';
  }).join('');
  return `<!doctype html><html lang="en"><meta charset="utf-8"><title>Tile physics validation</title>
<style>body{background:#14202b;color:#eaf1f6;font:16px system-ui;margin:24px}svg{width:100%;max-height:75vh;background:#243b4b}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}a{color:#9df}.fail{color:#ff8495}</style>
<h1>Physics acceptance: ${report.pass ? 'PASS' : 'FAIL'}</h1><p>${report.tiles.length} collision tiles · ${report.elapsedMs} ms · north ↑ · engine metres</p>
<p>${escape(JSON.stringify(report.summary))}</p>${report.requiredMissing?.length ? `<p class="fail">Missing required checks: ${escape(report.requiredMissing.join(', '))}</p>` : ''}
<svg role="img" aria-label="Top-down failure map" viewBox="${x0} ${z0} ${w} ${h}">${backdrop}${grid}${markers.join('')}</svg>
<p>${markers.length} failure markers. Hover for coordinates and check details. ${ortho ? 'Orthophoto embedded below the results.' : 'No orthophoto supplied.'}</p>
<details><summary>Complete check results</summary><pre>${escape(JSON.stringify(report, null, 2))}</pre></details></html>`;
}
