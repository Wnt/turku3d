// Drive sweep report: one table per structure (bridges, tunnels, underpasses), one row per lane and direction, and
// the failures ranked by severity with their ETRS-TM35FIN coordinates and the worst-point screenshot when the
// browser pass (tools/drivetest/shots.mjs) took one.
//
//   node tools/drivetest/report.mjs DIR     → DIR/report.md, DIR/report.json
import fs from 'node:fs';
import path from 'node:path';

const dir = process.argv[2];
const rows = [];
for (const f of fs.readdirSync(path.join(dir, 'results')).filter(f => f.endsWith('.jsonl')).sort())
  for (const line of fs.readFileSync(path.join(dir, 'results', f), 'utf8').split('\n')) if (line) rows.push(JSON.parse(line));
const structures = JSON.parse(fs.readFileSync(path.join(dir, 'structures.json'), 'utf8'));
const shots = fs.existsSync(path.join(dir, 'shots')) ? new Set(fs.readdirSync(path.join(dir, 'shots'))) : new Set();
const shotOf = key => { const f = `${key.replace(/[^\w-]+/g, '_')}.png`; return shots.has(f) ? `shots/${f}` : null; };

// Severity of a failure for ranking: falls and stuck cars first, then how far past its fail limit the worst event is.
const LIMIT = { vertical: 3, jerk: 50, pitch: 0.35, roll: 0.35, step: 0.04, jolt: 1, impact: 0.5, contact: 0.05, fall: 0.5, curb: 0.03, stuck: 0.2, incomplete: 0.2 };
const weight = e => e ? (['fall', 'stuck', 'incomplete'].includes(e.kind) ? 100 : 0) + e.value / (LIMIT[e.kind] ?? 1) : 0;
const fmt = (v, d = 2) => v === undefined || v === null || Number.isNaN(v) ? '-' : Number(v).toFixed(d);
const label = s => `${s.name || '(unnamed)'} — ${s.kind}${s.over ? ` under ${s.over.name || s.over.mode} (way ${s.over.way})` : ''}, ${s.highway || 'road'}, OSM way ${s.way}`;

const byStructure = new Map();
for (const r of rows) { const list = byStructure.get(r.structure) ?? []; list.push(r); byStructure.set(r.structure, list); }
const counts = { structures: structures.length, driven: byStructure.size, lanes: rows.length,
  byKind: Object.fromEntries(['bridge', 'tunnel', 'underpass'].map(k => [k, { structures: structures.filter(s => s.kind === k).length,
    driven: [...byStructure.keys()].filter(id => id.startsWith(k === 'underpass' ? 'under' : k)).length,
    lanes: rows.filter(r => r.kind === k).length, fail: rows.filter(r => r.kind === k && r.severity === 'fail').length,
    warn: rows.filter(r => r.kind === k && r.severity === 'warn').length }])) };
const failures = rows.filter(r => r.severity === 'fail').sort((a, b) => weight(b.worst) - weight(a.worst));
const kinds = {};
for (const r of failures) kinds[r.worst?.kind ?? '?'] = (kinds[r.worst?.kind ?? '?'] ?? 0) + 1;

let md = `# Drive sweep: bridges, tunnels and underpasses\n\n`;
md += `Tile version \`${JSON.parse(fs.readFileSync(path.join(dir, 'tile-service.json'), 'utf8')).version}\`, ${new Date().toISOString().slice(0, 16)} UTC. `;
md += `Each lane driven in its own direction at its posted limit (capped at 80 km/h) by the autopilot on the real vehicle physics over the tile's collision. `;
md += `Limits: tools/drivetest/README.md.\n\n`;
md += `| Kind | Structures found | Driven | Lanes | Fail | Warn |\n|---|---:|---:|---:|---:|---:|\n`;
for (const [k, c] of Object.entries(counts.byKind)) md += `| ${k} | ${c.structures} | ${c.driven} | ${c.lanes} | ${c.fail} | ${c.warn} |\n`;
md += `\nFailures by worst cause: ${Object.entries(kinds).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${k} ${n}`).join(', ') || 'none'}.\n\n`;
md += `## Failures, worst first\n\n| # | Structure | Lane | Worst | Value | At (E, N) | Outcome | Shot |\n|---:|---|---|---|---:|---|---|---|\n`;
failures.slice(0, 300).forEach((r, i) => {
  const w = r.worst ?? {}, e = r.events.find(e => e.kind === w.kind && Math.abs(e.s - w.s) < 0.01) ?? {};
  const shot = shotOf(r.key);
  md += `| ${i + 1} | ${r.name || '(unnamed)'} (${r.kind}${r.over ? ` under ${r.over.name || r.over.mode}` : ''}) | ${r.k + 1}/${r.laneCount} → ${r.heading}° | ${w.kind}${w.detail ? ` (${w.detail})` : ''} | ${fmt(w.value, 3)} | ${fmt(e.E, 1)}, ${fmt(e.N, 1)} | ${r.outcome} | ${shot ? `![](${shot})` : ''} |\n`;
});
md += `\n## Per structure\n\n`;
const ordered = [...byStructure.entries()].sort((a, b) => Math.max(...b[1].map(r => weight(r.worst))) - Math.max(...a[1].map(r => weight(r.worst))));
for (const [id, list] of ordered) {
  const s = structures.find(x => x.id === id);
  md += `### ${label(s)}\n\n${fmt(s.center.E, 1)} E, ${fmt(s.center.N, 1)} N · ${fmt(s.length, 0)} m · \`${id}\`\n\n`;
  md += `| Lane | Dir | km/h | Result | Max vert (m/s²) | Jerk | Pitch/roll (rad/s) | Contact loss (s) | Steps ≥2.5 cm | Worst | Outcome |\n|---|---:|---:|---|---:|---:|---|---:|---:|---|---|\n`;
  for (const r of list.sort((a, b) => a.heading - b.heading || a.k - b.k)) {
    const steps = r.events.filter(e => e.kind === 'step').length, w = r.worst;
    md += `| ${r.k + 1}/${r.laneCount} | ${r.heading}° | ${r.limitKmh} | ${r.severity.toUpperCase()} | ${fmt(r.peaks.vertical)} | ${fmt(r.peaks.jerk, 0)} | ${fmt(r.peaks.pitchRate)}/${fmt(r.peaks.rollRate)} | ${fmt(r.peaks.contactLoss)} | ${steps} | ${w ? `${w.kind} ${fmt(w.value, 3)} at ${fmt(r.view?.E, 1)}, ${fmt(r.view?.N, 1)}` : '-'} | ${r.outcome} |\n`;
  }
  md += '\n';
}
const missing = structures.filter(s => !byStructure.has(s.id));
md += `## Not driven (${missing.length})\n\nStructures with no matching car lane in the lane graph (service roads without lanes, private ways, rows too short): ` +
  missing.slice(0, 200).map(s => `${s.name || s.id}`).join(', ') + (missing.length > 200 ? ', …' : '') + '\n';
fs.writeFileSync(path.join(dir, 'report.md'), md);
fs.writeFileSync(path.join(dir, 'report.json'), JSON.stringify({ counts, kinds, failures: failures.map(r => ({ key: r.key, name: r.name, kind: r.kind, over: r.over, lane: `${r.k + 1}/${r.laneCount}`, heading: r.heading, worst: r.worst, view: r.view, outcome: r.outcome })) }, null, 1));
console.log(JSON.stringify({ ...counts, kinds }));
