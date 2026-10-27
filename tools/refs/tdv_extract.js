// Extract the 3DVista tour definition (script_general.js) into JSON without a browser.
// Usage: node tdv_extract.js <script_general.js> <locale/fi.txt> <out.json>
const fs = require('fs'), vm = require('vm');
const [src, loc, out] = process.argv.slice(2);
let captured = null;
const anyStub = () => new Proxy(function () {}, { get: (t, k) => (k === Symbol.toPrimitive ? () => '' : anyStub()), apply: () => anyStub() });
const ctx = { TDV: { PlayerAPI: { defineScript: (b) => { captured = b; } }, Quiz: anyStub(), Tour: anyStub() }, Object, Array, String, JSON, Math, console: { log() {} } };
try { vm.runInNewContext(fs.readFileSync(src, "utf8"), ctx, { timeout: 10000 }); } catch (e) { console.error("ERR", e.name, e.message.slice(0, 200)); }
const labels = {};
for (const line of fs.readFileSync(loc, 'utf8').split('\n')) {
  const m = line.match(/^([A-Za-z0-9_]+)\.label\s*=\s*(.*)$/); if (m) labels[m[1]] = m[2].trim();
}
const byId = {}; (function walk(o) {
  if (Array.isArray(o)) o.forEach(walk);
  else if (o && typeof o === 'object') { if (o.id) byId[o.id] = o; Object.values(o).forEach(walk); }
})(captured);
const ref = (s) => typeof s === 'string' && s.startsWith('this.') ? byId[s.slice(5)] : s;
const panos = Object.values(byId).filter(o => o.class === 'Panorama').map(p => {
  const fr = p.frames[0]; const cube = fr.front || fr.image || fr;
  const levels = ((cube && cube.levels) || []).map(l => ({ url: l.url, w: l.width, h: l.height, rows: l.rowCount, cols: l.colCount, tags: l.tags }));
  const overlays = (p.overlays || []).map(ref).filter(Boolean).map(ov => ({
    id: ov.id, class: ov.class,
    items: (ov.items || []).map(it => ({ yaw: it.yaw, pitch: it.pitch, hfov: it.hfov, label: it.data && it.data.label })),
    label: ov.data && ov.data.label,
    click: (ov.areas || []).map(ref).filter(Boolean).map(a => String(a.click || '')).join(' | ').slice(0, 300),
  }));
  if (process.env.DUMP === p.id) console.error(JSON.stringify((p.overlays||[]).slice(0,3).map(ref), null, 1).slice(0, 4000));
  return { id: p.id, label: labels[p.id] || (p.data && p.data.label), hfov: p.hfov, vfov: p.vfov, thumbnail: p.thumbnailUrl,
    levels, adjacent: (p.adjacentPanoramas || []).map(a => ({ to: a.panorama.slice(5), toLabel: labels[a.panorama.slice(5)], yaw: a.yaw, distance: a.distance })),
    overlays };
});
fs.writeFileSync(out, JSON.stringify({ panoramas: panos }, null, 1));
console.error(`panoramas: ${panos.length}`);
