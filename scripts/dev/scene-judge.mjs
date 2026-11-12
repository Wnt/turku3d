#!/usr/bin/env node
// Scores one iteration of the visual work and keeps the score per iteration. It captures the corridor's gameplay views
// and its reference-photo views with the scene alone (scene-capture.mjs --mode render), builds a blind packet (renders, photos and the rubric, nothing
// that names the build), lets the Fable 5.2 visual judge (.claude/agents/visual-judge.md) score every view in six
// categories, and appends the result to a history whose report shows the scores and their change per iteration.
//
//   scripts/dev/scene-judge.mjs run --label TEXT [--base URL] [--views FILE]... [--capture DIR]... [--repeats 2]
//       [--history FILE] [--out DIR]
//   scripts/dev/scene-judge.mjs prepare --capture DIR... --out DIR        packet only (an in-session subagent judges it)
//   scripts/dev/scene-judge.mjs record --run DIR --label TEXT [--history FILE]     after DIR/scores/judge-*.json exist
//   scripts/dev/scene-judge.mjs report [--history FILE]
//
// The default views are scripts/dev/views/riverside-office.json and riverside-office-refs.json; the default history is
// ~/turku3d/loops/judge/<first view set>.jsonl, shared by everyone working on that corridor, with an HTML report beside it.
// Photos, renders and packets stay outside Git. A judge run is evidence, never a merge gate.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
export const CATEGORIES = ['identity', 'buildings', 'materials', 'lighting', 'street', 'defects'];
/** Recognisability first (docs/design/visual-brief.md); the overall score is 0–100. */
export const WEIGHTS = { identity: 0.3, buildings: 0.2, materials: 0.15, lighting: 0.1, street: 0.15, defects: 0.1 };
const AGENT = path.join(repo, '.claude/agents/visual-judge.md');
const RUBRIC = path.join(repo, 'scripts/dev/judge/JUDGE.md');
const SCHEMA = path.join(repo, 'scripts/dev/judge/scores.schema.json');
const DEFAULT_VIEWS = ['scripts/dev/views/riverside-office.json', 'scripts/dev/views/riverside-office-refs.json'];
const sha = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const round = (x, n = 2) => Math.round(x * 10 ** n) / 10 ** n;

/** Packet items from capture manifests: posed views (photo + render) first, then gameplay renders, neutral ids. */
export function buildItems(captures) {
  const posed = [], gameplay = [];
  for (const { dir, manifest } of captures) {
    if (manifest.failure) throw new Error(`${dir}: the capture failed (${manifest.failure.split('\n')[0]})`);
    if (manifest.mode !== 'render') throw new Error(`${dir}: judged captures show the scene alone (scene-capture.mjs --mode render), not the game`);
    for (const view of manifest.views) (view.photo ? posed : gameplay).push({ dir, view });
  }
  const items = [], key = [];
  posed.forEach(({ dir, view }, i) => {
    const id = `p${String(i + 1).padStart(2, '0')}`;
    items.push({ id, kind: 'posed', photo: `${id}-photo.jpg`, render: `${id}-render.jpg`, photoDate: view.photo.date, poseConfidence: view.photo.poseConfidence });
    key.push({ id, capture: dir, view: view.id, render: path.join(dir, view.file), photo: view.photo });
  });
  gameplay.forEach(({ dir, view }, i) => {
    const id = `g${String(i + 1).padStart(2, '0')}`;
    items.push({ id, kind: 'gameplay', render: `${id}-render.jpg` });
    key.push({ id, capture: dir, view: view.id, render: path.join(dir, view.file), photo: null });
  });
  return { items, key };
}

/** Throws unless the judge's answer covers exactly the packet's items with valid scores. */
export function validateScores(scores, items) {
  const ids = items.map(i => i.id), got = (scores?.views ?? []).map(v => v.id);
  if (JSON.stringify(ids) !== JSON.stringify(got)) throw new Error(`judge views ${got.join(',')} do not match the packet ${ids.join(',')}`);
  for (const v of scores.views) for (const c of CATEGORIES) {
    const s = v.scores?.[c];
    if (!Number.isInteger(s) || s < 0 || s > 10) throw new Error(`${v.id}: ${c} score ${s} is not an integer 0–10`);
  }
  if (typeof scores.summary !== 'string') throw new Error('judge summary missing');
  return scores;
}

const overallOf = means => round(CATEGORIES.reduce((sum, c) => sum + WEIGHTS[c] * means[c], 0) * 10, 1);

/** Per view: mean over repeats; per category: mean over views; overall 0–100; spread: the repeats' overall range. */
export function aggregate(runs, items) {
  const views = items.map((item, i) => ({ id: item.id, kind: item.kind,
    scores: Object.fromEntries(CATEGORIES.map(c => [c, round(runs.reduce((s, r) => s + r.views[i].scores[c], 0) / runs.length)])),
    issues: runs[0].views[i].issues, notes: runs.map(r => r.views[i].note) }));
  const meanOver = (list, c) => list.reduce((s, v) => s + v.scores[c], 0) / list.length;
  const means = Object.fromEntries(CATEGORIES.map(c => [c, round(meanOver(views, c))]));
  const each = runs.map(r => overallOf(Object.fromEntries(CATEGORIES.map(c => [c, meanOver(r.views, c)]))));
  const kinds = Object.fromEntries(['posed', 'gameplay'].map(k => [k, views.filter(v => v.kind === k)]).filter(([, l]) => l.length)
    .map(([k, l]) => [k, overallOf(Object.fromEntries(CATEGORIES.map(c => [c, meanOver(l, c)])))]));
  return { views, means, overall: overallOf(means), byKind: kinds, repeats: each, spread: round(Math.max(...each) - Math.min(...each), 1),
    summaries: runs.map(r => r.summary) };
}

export function reportText(rows) {
  const head = ['#', 'date', 'label', 'overall', '±', 'Δ', ...CATEGORIES.map(c => c.slice(0, 5)), 'posed', 'gameplay'];
  const lines = rows.map((r, i) => [r.iteration, r.at.slice(0, 16).replace('T', ' '), r.label.slice(0, 32), r.overall.toFixed(1), r.spread.toFixed(1),
    i ? (r.overall - rows[i - 1].overall >= 0 ? '+' : '') + (r.overall - rows[i - 1].overall).toFixed(1) : '',
    ...CATEGORIES.map(c => r.means[c].toFixed(1)), r.byKind.posed?.toFixed(1) ?? '', r.byKind.gameplay?.toFixed(1) ?? ''].map(String));
  const width = head.map((h, k) => Math.max(h.length, ...lines.map(l => l[k].length)));
  return [head, ...lines].map(l => l.map((x, k) => k === 2 ? x.padEnd(width[k]) : x.padStart(width[k])).join('  ')).join('\n');
}

export function reportHtml(rows, title) {
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const W = 760, H = 300, pad = 36, n = Math.max(rows.length - 1, 1);
  const x = i => pad + (W - 2 * pad) * i / n, y = v => H - pad - (H - 2 * pad) * v / 100;
  const colours = { overall: '#111', identity: '#c0392b', buildings: '#8e44ad', materials: '#d68910', lighting: '#2e86c1', street: '#229954', defects: '#7f8c8d' };
  const series = [['overall', r => r.overall], ...CATEGORIES.map(c => [c, r => r.means[c] * 10])];
  const lines = series.map(([name, f]) => `<polyline fill="none" stroke="${colours[name]}" stroke-width="${name === 'overall' ? 3 : 1.5}" points="${rows.map((r, i) => `${x(i).toFixed(1)},${y(f(r)).toFixed(1)}`).join(' ')}"/>`
    + rows.map((r, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(f(r)).toFixed(1)}" r="${name === 'overall' ? 4 : 2.5}" fill="${colours[name]}"><title>${esc(`${name} ${f(r).toFixed(1)} · #${r.iteration} ${r.label}`)}</title></circle>`).join('')).join('\n');
  const grid = [0, 25, 50, 75, 100].map(v => `<line x1="${pad}" x2="${W - pad}" y1="${y(v)}" y2="${y(v)}" stroke="#ddd"/><text x="4" y="${y(v) + 4}" font-size="11">${v}</text>`).join('');
  const last = rows.at(-1);
  const issues = last ? last.views.flatMap(v => v.issues.map(i => ({ ...i, view: v.id }))).sort((a, b) => b.severity - a.severity).slice(0, 12) : [];
  return `<!doctype html><meta charset="utf-8"><title>${esc(title)}</title>
<style>body{font:14px system-ui;margin:24px;max-width:980px}table{border-collapse:collapse}td,th{padding:3px 8px;text-align:right;border-bottom:1px solid #eee}td.l,th.l{text-align:left}</style>
<h1>${esc(title)}</h1><p>Visual judge scores per iteration (overall 0–100; categories 0–10 shown ×10 in the chart). ± is the spread between the judge's repeats.</p>
<svg width="${W}" height="${H}" role="img" aria-label="scores per iteration">${grid}${lines}${rows.map((r, i) => `<text x="${x(i)}" y="${H - 12}" font-size="11" text-anchor="middle">#${r.iteration}</text>`).join('')}</svg>
<p>${series.map(([name]) => `<span style="color:${colours[name]}">■ ${name}</span>`).join(' &nbsp; ')}</p>
<table><tr><th>#</th><th class="l">date</th><th class="l">label</th><th>overall</th><th>±</th>${CATEGORIES.map(c => `<th>${c}</th>`).join('')}<th class="l">build</th></tr>
${rows.map(r => `<tr><td>${r.iteration}</td><td class="l">${esc(r.at.slice(0, 16).replace('T', ' '))}</td><td class="l">${esc(r.label)}</td><td><b>${r.overall.toFixed(1)}</b></td><td>${r.spread.toFixed(1)}</td>${CATEGORIES.map(c => `<td>${r.means[c].toFixed(1)}</td>`).join('')}<td class="l">${esc(`${r.git.sha.slice(0, 8)}${r.git.dirty ? '+' : ''} ${r.captures.map(c => c.release ?? '').filter(Boolean)[0]?.slice(0, 8) ?? ''}`)}</td></tr>`).join('\n')}</table>
${last ? `<h2>Latest: #${last.iteration} ${esc(last.label)}</h2><p>${last.summaries.map(esc).join('</p><p>')}</p>
<table><tr><th class="l">view</th><th class="l">category</th><th>sev</th><th class="l">what</th><th class="l">fix</th></tr>
${issues.map(i => `<tr><td class="l">${esc(i.view)}</td><td class="l">${esc(i.category)}</td><td>${i.severity}</td><td class="l">${esc(i.what)} (${esc(i.where)})</td><td class="l">${esc(i.fix)}</td></tr>`).join('\n')}</table>` : ''}\n`;
}

const readHistory = file => fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)) : [];

function writeReport(history) {
  const rows = readHistory(history);
  if (!rows.length) return console.log(`${history}: no iterations yet`);
  const html = history.replace(/\.jsonl$/, '') + '.html';
  fs.writeFileSync(html, reportHtml(rows, `Visual judge: ${path.basename(history, '.jsonl')}`));
  console.log(reportText(rows));
  console.log(`report: ${html}`);
}

function photoFile(image) {
  // Registry paths start with data/qa/ (qa/README.md): QA_DATA, the sandbox, then the shared data tree.
  const rest = image.replace(/^data\/qa\//, '');
  const roots = [process.env.QA_DATA, process.env.TURKU3D_WORK && path.join(process.env.TURKU3D_WORK, 'qa'),
    process.env.TURKU3D_DATA && path.join(process.env.TURKU3D_DATA, 'qa'), path.join(os.homedir(), 'turku3d/data/qa')].filter(Boolean);
  const found = roots.map(r => path.join(r, rest)).find(f => fs.existsSync(f));
  if (!found) throw new Error(`reference photo ${image} not found under ${roots.join(', ')} (make it with qa/views/prepare.py, see qa/README.md)`);
  return found;
}

const jpeg = (from, to) => execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', from, '-vf', "scale='min(1280,iw)':-2", '-q:v', '2', to]);

export function prepare(captureDirs, out) {
  const captures = captureDirs.map(dir => ({ dir: path.resolve(dir), manifest: JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8')) }));
  const { items, key } = buildItems(captures);
  const packet = path.join(out, 'packet');
  fs.rmSync(packet, { recursive: true, force: true });
  fs.mkdirSync(packet, { recursive: true });
  for (const k of key) {
    jpeg(k.render, path.join(packet, `${k.id}-render.jpg`));
    if (k.photo) jpeg(photoFile(k.photo.image), path.join(packet, `${k.id}-photo.jpg`));
  }
  fs.copyFileSync(RUBRIC, path.join(packet, 'JUDGE.md'));
  fs.writeFileSync(path.join(packet, 'items.json'), JSON.stringify(items, null, 1) + '\n');
  // The key stays outside the packet: it names the captures (and so the build) the judge must not know.
  fs.writeFileSync(path.join(out, 'key.json'), JSON.stringify({ items, key, captures: captures.map(c => ({ dir: c.dir,
    viewsFile: c.manifest.viewsFile, label: c.manifest.label, base: c.manifest.base, release: c.manifest.source?.scene?.release ?? null,
    version: c.manifest.source?.version ?? null, readiness: c.manifest.readiness,
    failedChecks: Object.entries(c.manifest.checks ?? {}).filter(([, v]) => !v.ok).map(([k]) => k) })) }, null, 1) + '\n');
  return { packet, items };
}

function agentDefinition() {
  const text = fs.readFileSync(AGENT, 'utf8');
  const [, front, body] = text.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  return { model: front.match(/^model:\s*(\S+)/m)[1], prompt: body.trim() };
}

/** One headless judge run: the agent's model and instructions, Read only, no MCP servers, settings or session. */
function judgeOnce(out, n) {
  const packet = path.join(out, 'packet'), agent = agentDefinition();
  const args = ['-p', '--model', agent.model, '--system-prompt', agent.prompt, '--tools', 'Read', '--allowedTools', 'Read',
    '--add-dir', packet, '--strict-mcp-config', '--disable-slash-commands', '--no-session-persistence', '--setting-sources', '',
    '--json-schema', fs.readFileSync(SCHEMA, 'utf8'), '--output-format', 'json',
    `The packet directory is ${packet}. Read ${path.join(packet, 'JUDGE.md')} and follow it.`];
  const started = performance.now();
  return new Promise((resolve, reject) => {
    const child = spawn('claude', args, { cwd: packet, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', d => { stdout += d; });
    child.stderr.on('data', d => { stderr += d; });
    const timer = setTimeout(() => child.kill('SIGTERM'), 20 * 60 * 1000);
    child.on('close', code => {
      clearTimeout(timer);
      try {
        const d = JSON.parse(stdout);
        if (code || d.is_error) throw new Error(`judge ${n}: ${d.result ?? stderr.slice(0, 400)}`);
        const scores = d.structured_output ?? JSON.parse(String(d.result).replace(/^```(?:json)?\s*|\s*```$/g, ''));
        const meta = { model: Object.keys(d.modelUsage ?? {})[0] ?? agent.model, durationMs: Math.round(performance.now() - started),
          costUsd: d.total_cost_usd ?? null, turns: d.num_turns ?? null };
        fs.mkdirSync(path.join(out, 'scores'), { recursive: true });
        fs.writeFileSync(path.join(out, 'scores', `judge-${n}.json`), JSON.stringify({ ...scores, meta }, null, 1) + '\n');
        resolve(meta);
      } catch (error) { reject(new Error(`${error.message}${stdout ? '' : `\n${stderr.slice(0, 400)}`}`)); }
    });
  });
}

function gitInfo() {
  const git = (...a) => execFileSync('git', a, { cwd: repo, encoding: 'utf8' }).trim();
  return { sha: git('rev-parse', 'HEAD'), branch: git('rev-parse', '--abbrev-ref', 'HEAD'), dirty: git('status', '--porcelain').length > 0 };
}

export function record(out, label, history) {
  const keyFile = JSON.parse(fs.readFileSync(path.join(out, 'key.json'), 'utf8'));
  const files = fs.readdirSync(path.join(out, 'scores')).filter(f => /^judge-\d+\.json$/.test(f)).sort();
  if (!files.length) throw new Error(`${out}/scores has no judge-N.json`);
  const runs = files.map(f => validateScores(JSON.parse(fs.readFileSync(path.join(out, 'scores', f), 'utf8')), keyFile.items));
  const result = aggregate(runs, keyFile.items);
  const rows = readHistory(history);
  const row = { schema: 'turku-judge/1', iteration: rows.length + 1, at: new Date().toISOString(), label, run: path.resolve(out),
    git: gitInfo(), captures: keyFile.captures,
    judge: { agent: path.relative(repo, AGENT), rubricSha: sha(RUBRIC).slice(0, 16), schemaSha: sha(SCHEMA).slice(0, 16), weights: WEIGHTS,
      runs: runs.map(r => r.meta ?? { model: 'in-session subagent' }) },
    ...result, views: result.views.map((v, i) => ({ ...v, view: keyFile.key[i].view, photo: keyFile.key[i].photo?.id ?? null })) };
  const changed = rows.length && rows.at(-1).judge.rubricSha !== row.judge.rubricSha;
  if (changed) console.warn('The rubric changed since the previous iteration: scores before and after are not comparable.');
  fs.mkdirSync(path.dirname(history), { recursive: true });
  fs.appendFileSync(history, JSON.stringify(row) + '\n');
  return row;
}

async function main() {
  const [command, ...argv] = process.argv.slice(2);
  const opt = (name, fallback) => argv.includes(name) ? argv[argv.indexOf(name) + 1] : fallback;
  const many = name => argv.flatMap((a, i) => a === name ? [argv[i + 1]] : []);
  const viewsFiles = many('--views').length ? many('--views') : DEFAULT_VIEWS;
  const corridor = path.basename(viewsFiles[0], '.json');
  const history = path.resolve(opt('--history', path.join(os.homedir(), 'turku3d/loops/judge', `${corridor}.jsonl`)));
  if (command === 'report') return writeReport(history);
  const label = opt('--label');
  if (command === 'record') {
    if (!label) throw new Error('record needs --label');
    record(path.resolve(opt('--run')), label, history);
    return writeReport(history);
  }
  if (command === 'prepare') {
    const { packet, items } = prepare(many('--capture'), path.resolve(opt('--out')));
    return console.log(JSON.stringify({ packet, items: items.length }));
  }
  if (command !== 'run') throw new Error('usage: scene-judge.mjs run|prepare|record|report (see the header)');
  if (!label) throw new Error('run needs --label: what this iteration is');
  const out = path.resolve(opt('--out', path.join(process.env.TURKU3D_WORK ?? os.tmpdir(), 'judge', new Date().toISOString().replace(/[:.]/g, '-'))));
  const timings = {}, t0 = performance.now();
  let captures = many('--capture');
  if (!captures.length) {
    captures = viewsFiles.map(f => path.join(out, 'captures', path.basename(f, '.json')));
    for (const [i, f] of viewsFiles.entries()) {
      // The scene alone (QA camera mode): no player, physics, audio, traffic or multiplayer in a judged image.
      const args = ['scripts/dev/scene-capture.mjs', '--mode', 'render', '--views', f, '--out', captures[i], '--label', label];
      if (opt('--base')) args.push('--base', opt('--base'));
      if (opt('--cdp')) args.push('--cdp', opt('--cdp'));
      try { execFileSync('node', args, { cwd: repo, stdio: 'inherit' }); }
      catch { /* A failed check is recorded with the iteration; only a failed capture stops (prepare checks). */ }
    }
    timings.captureMs = Math.round(performance.now() - t0);
  }
  let t = performance.now();
  prepare(captures, out);
  timings.prepareMs = Math.round(performance.now() - t);
  t = performance.now();
  const repeats = Number(opt('--repeats', '2'));
  await Promise.all(Array.from({ length: repeats }, (_, i) => judgeOnce(out, i + 1)));
  timings.judgeMs = Math.round(performance.now() - t);
  const row = record(out, label, history);
  writeReport(history);
  console.log(JSON.stringify({ iteration: row.iteration, overall: row.overall, spread: row.spread, means: row.means, run: out, history, timings,
    costUsd: round(row.judge.runs.reduce((s, r) => s + (r.costUsd ?? 0), 0)) }));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
