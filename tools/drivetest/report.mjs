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

// Rows from before the not-covered rule: a stop before the window is an approach problem, not the structure's.
const covered = r => !r.outcome.startsWith('not covered') && !(r.outcome !== 'arrived'
  && r.events.some(e => (e.kind === 'stuck' || e.kind === 'incomplete') && e.s < r.window[0]));
const byStructure = new Map();
for (const r of rows) { const list = byStructure.get(r.structure) ?? []; list.push(r); byStructure.set(r.structure, list); }
const counts = { structures: structures.length, driven: byStructure.size, lanes: rows.length,
  byKind: Object.fromEntries(['bridge', 'tunnel', 'underpass'].map(k => [k, { structures: structures.filter(s => s.kind === k).length,
    driven: [...byStructure.keys()].filter(id => id.startsWith(k === 'underpass' ? 'under' : k)).length,
    lanes: rows.filter(r => r.kind === k).length, notCovered: rows.filter(r => r.kind === k && !covered(r)).length,
    fail: rows.filter(r => r.kind === k && covered(r) && r.severity === 'fail').length,
    warn: rows.filter(r => r.kind === k && covered(r) && r.severity === 'warn').length }])) };
const failures = rows.filter(r => r.severity === 'fail' && covered(r)).sort((a, b) => weight(b.worst) - weight(a.worst));
const kinds = {};
for (const r of failures) kinds[r.worst?.kind ?? '?'] = (kinds[r.worst?.kind ?? '?'] ?? 0) + 1;

let md = `# Drive sweep: bridges, tunnels and underpasses\n\n`;
md += `Tile version \`${JSON.parse(fs.readFileSync(path.join(dir, 'tile-service.json'), 'utf8')).version}\`, ${new Date().toISOString().slice(0, 16)} UTC. `;
md += `Each lane driven in its own direction at its posted limit (capped at 80 km/h) by the autopilot on the real vehicle physics over the tile's collision. `;
md += `Limits: tools/drivetest/README.md.\n\n`;
md += `| Kind | Structures found | Driven | Lanes | Not covered | Fail | Warn | Pass |\n|---|---:|---:|---:|---:|---:|---:|---:|\n`;
for (const [k, c] of Object.entries(counts.byKind)) md += `| ${k} | ${c.structures} | ${c.driven} | ${c.lanes} | ${c.notCovered} | ${c.fail} | ${c.warn} | ${c.lanes - c.notCovered - c.fail - c.warn} |\n`;
md += `\nNot covered: the car stopped before reaching the structure (an approach junction or kerb), so the lane was not measured.\n`;
md += `\nFailures by worst cause: ${Object.entries(kinds).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${k} ${n}`).join(', ') || 'none'}.\n\n`;
md += `## Failures, worst first\n\n| # | Structure | Lane | Worst | Value | At (E, N) | Outcome | Shot |\n|---:|---|---|---|---:|---|---|---|\n`;
failures.slice(0, 300).forEach((r, i) => {
  const w = r.worst ?? {}, e = r.events.find(e => e.kind === w.kind && Math.abs(e.s - w.s) < 0.01) ?? {};
  const shot = shotOf(r.key);
  md += `| ${i + 1} | ${r.name || '(unnamed)'} (${r.kind}${r.over ? ` under ${r.over.name || r.over.mode}` : ''}) | ${r.k + 1}/${r.laneCount} → ${r.heading}° | ${w.kind}${w.detail ? ` (${w.detail})` : ''} | ${fmt(w.value, 3)} | ${fmt(e.E, 1)}, ${fmt(e.N, 1)} | ${r.outcome} | ${shot ? `![](${shot})` : ''} |\n`;
});
// Geometry bugs, one per structure, ranked by class then by how many of its lanes show it. The class is what a player
// would hit: a hole or fall-through, an obstacle on the lane (stuck, collision), a wheel leaving the deck or a hard
// jolt, a step, then roughness.
const CLASSES = [
  ['hole or fall-through', e => e.kind === 'fall' || (e.kind === 'step' && (e.value > 0.3 || /no ground/.test(e.detail ?? '')))],
  ['obstacle on the lane', e => e.kind === 'stuck' || e.kind === 'impact' || e.kind === 'curb'],
  ['wheel off the ground / hard jolt', e => e.kind === 'contact' || e.kind === 'jolt' || (e.kind === 'vertical' && e.value >= 5)],
  ['step 4-30 cm', e => e.kind === 'step'],
  ['rough (vertical, jerk, pitch, roll)', e => ['vertical', 'jerk', 'pitch', 'roll'].includes(e.kind)],
];
const bugs = [];
for (const [id, list] of byStructure) {
  const s = structures.find(x => x.id === id), lanes = list.filter(covered);
  for (const [ci, [name, test]] of CLASSES.entries()) {
    const hit = lanes.filter(r => r.events.some(e => e.severity === 'fail' && test(e)));
    if (!hit.length) continue;
    const worst = hit.flatMap(r => r.events.filter(e => e.severity === 'fail' && test(e))).sort((a, b) => weight(b) - weight(a))[0];
    bugs.push({ id, s, ci, name, lanes: hit.length, of: lanes.length, worst, offRoad: Math.max(...hit.map(r => r.offRoad ?? 0)) });
    break;
  }
}
bugs.sort((a, b) => a.ci - b.ci || b.lanes / b.of - a.lanes / a.of || b.lanes - a.lanes);
md += `\n## Geometry bugs, ranked\n\nOne row per structure: its most severe class, how many of its measured lanes show it, the worst point. ` +
  `"Off carriageway" is the largest share of a lane's wheel-track samples on kerb, sidewalk or verge materials: a high share means the lane ` +
  `line itself misses the built road (a lane-geometry bug) rather than a rough surface.\n\n` +
  `| # | Class | Structure | Lanes | Worst | At (E, N) | Off carriageway |\n|---:|---|---|---:|---|---|---:|\n`;
bugs.forEach((b, i) => { md += `| ${i + 1} | ${b.name} | ${label(b.s)} | ${b.lanes}/${b.of} | ${b.worst.kind} ${fmt(b.worst.value, 3)}${b.worst.detail ? ` (${b.worst.detail})` : ''} | ${fmt(b.worst.E, 1)}, ${fmt(b.worst.N, 1)} | ${fmt(b.offRoad * 100, 0)} % |\n`; });
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
fs.writeFileSync(path.join(dir, 'report.json'), JSON.stringify({ counts, kinds, bugs: bugs.map(b => ({ structure: b.id, name: b.s.name, kind: b.s.kind, class: b.name, lanes: b.lanes, of: b.lanes && b.of, worst: b.worst, offRoad: b.offRoad })), failures: failures.map(r => ({ key: r.key, name: r.name, kind: r.kind, over: r.over, lane: `${r.k + 1}/${r.laneCount}`, heading: r.heading, worst: r.worst, view: r.view, outcome: r.outcome })) }, null, 1));
console.log(JSON.stringify({ ...counts, kinds, bugClasses: Object.fromEntries(CLASSES.map(([n], i) => [n, bugs.filter(b => b.ci === i).length])) }));
