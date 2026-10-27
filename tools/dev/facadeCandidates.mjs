// GET /facades/candidates/: every reviewed generated facade candidate, accepted or rejected, rendered as static HTML
// from the JSON that textures/turkutex/facade_candidates.py keeps under $TURKU3D_DATA/facade-approvals/candidates/.
// Served by the localhost game's dev server (localhost/vitePlugin.mjs) and serve-dist.mjs. No build step.
//  /facades/candidates/                      tasks, newest first, with their counts
//  /facades/candidates/<task>/               walls (rejected first), candidates side by side with the reviewer's reasons
//  /facades/candidates/<task>/index.json     the task's index
//  /facades/candidates/<task>/<wall>/<n>.png the reviewed wall (generated pixels only); <n>.json its record
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { serveFile } from './http.mjs';

const PREFIX = '/facades/candidates';
/** Where facade_candidates.py keeps them: $TURKU3D_DATA/facade-approvals/candidates (the shared data root, not the sandbox). */
export const candidatesRoot = path.resolve(process.env.TURKU3D_DATA ?? path.join(os.homedir(), 'turku3d/data'), 'facade-approvals/candidates');
const NAME = /^[A-Za-z0-9_.-]+$/;
const valid = (name) => NAME.test(name) && name !== '.' && name !== '..';
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const enc = encodeURIComponent;

function readIndex(root, task) {
  try { return JSON.parse(fs.readFileSync(path.join(root, task, 'index.json'), 'utf8')); } catch { return null; }
}

function send(res, status, body, type = 'text/html; charset=utf-8', headers = {}) {
  res.writeHead(status, { 'Content-Type': type, 'Cache-Control': 'no-store', 'Cross-Origin-Resource-Policy': 'cross-origin', ...headers });
  res.end(body);
}

const STYLE = `
:root { --bg: #f6f5f2; --fg: #1d1d1b; --muted: #6b6a66; --card: #fff; --line: #dcdad4; --ok: #1f7a3d; --ok-bg: #e2f3e7;
  --bad: #a3261b; --bad-bg: #fbe6e3; --link: #1d5fa8; }
@media (prefers-color-scheme: dark) { :root { --bg: #161615; --fg: #ecebe7; --muted: #a09e98; --card: #22221f; --line: #3a3935;
  --ok: #7fd49a; --ok-bg: #173622; --bad: #f4a197; --bad-bg: #3d1a16; --link: #8ab8ec; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 14px/1.45 system-ui, sans-serif; }
a { color: var(--link); }
h1 { font-size: 20px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 0; }
.muted { color: var(--muted); }
table { border-collapse: collapse; width: 100%; max-width: 960px; background: var(--card); }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); }
td.n { text-align: right; font-variant-numeric: tabular-nums; }
.wall { margin: 20px 0; padding: 12px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; }
.head { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: baseline; margin-bottom: 10px; }
.row { display: flex; gap: 12px; overflow-x: auto; padding-bottom: 4px; }
.cand { flex: 0 0 340px; border: 1px solid var(--line); border-radius: 6px; padding: 8px; }
.cand img { display: block; width: 100%; height: 220px; object-fit: contain; background: repeating-conic-gradient(var(--line) 0 25%, transparent 0 50%) 0 0 / 16px 16px; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; }
.ok { color: var(--ok); background: var(--ok-bg); } .bad { color: var(--bad); background: var(--bad-bg); }
ul { margin: 4px 0; padding-left: 18px; } details { margin-top: 4px; } summary { cursor: pointer; color: var(--muted); }
pre { white-space: pre-wrap; word-break: break-word; font-size: 12px; margin: 4px 0; }
`;

const page = (title, body) => `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(title)}</title><style>${STYLE}</style></head>
<body>${body}</body></html>`;

const badge = (accepted) => accepted ? '<span class="badge ok">accepted</span>' : '<span class="badge bad">rejected</span>';

function taskList(root) {
  let names = [];
  try { names = fs.readdirSync(root).filter(valid); } catch { /* no candidates yet */ }
  const tasks = names.map((name) => ({ name, index: readIndex(root, name) })).filter((t) => t.index)
    .sort((a, b) => String(b.index.updated).localeCompare(String(a.index.updated)));
  const cell = (n) => `<td class="n">${Number(n ?? 0)}</td>`;
  const rows = tasks.map(({ name, index: { counts = {}, updated } }) => `<tr><td><a href="${enc(name)}/">${esc(name)}</a></td>`
    + `<td>${esc(updated)}</td>${cell(counts.walls)}${cell(counts.candidates)}${cell(counts.accepted)}${cell(counts.rejected)}</tr>\n`).join('');
  return page('Facade candidates', `<h1>Generated facade candidates</h1>
<p class="muted">Every wall the reviewer saw, accepted or rejected, per content task. Kept in ${esc(root)}.</p>
${tasks.length ? `<table><thead><tr><th>Task</th><th>Updated</th><th>Walls</th><th>Candidates</th><th>Accepted</th><th>Rejected</th></tr></thead>
<tbody>${rows}</tbody></table>` : '<p>No candidates kept yet.</p>'}`);
}

function candidateCard(wall, c) {
  const review = c.review ?? {};
  const gen = c.generation ?? {};
  const problems = [...(review.problems ?? []), ...(c.audit?.problems ?? []).map((p) => `audit: ${p}`)];
  const answer = review.answer ?? {};
  const counts = answer.storeys !== undefined
    ? `<div class="muted">storeys ${esc(answer.storeys)}/${esc(answer.expected_storeys)}, upper openings ${esc(answer.upper_openings)}/${esc(answer.expected_upper_openings)}</div>` : '';
  const src = `${enc(wall)}/${enc(c.image)}`;
  return `<div class="cand">
<a href="${src}"><img src="${src}" alt="candidate ${esc(c.n)} of ${esc(wall)}" loading="lazy"></a>
<div class="head" style="margin:6px 0 2px"><strong>#${esc(c.n)}</strong> ${badge(c.accepted)}
<span class="muted">${esc(c.width_px)}×${esc(c.height_px)} px · ${esc(gen.model)} ${esc(gen.effort)}</span></div>
${problems.length ? `<ul>${problems.map((p) => `<li>${esc(p)}</li>`).join('')}</ul>` : ''}
${answer.notes ? `<div>Reviewer: ${esc(answer.notes)}</div>` : ''}${counts}
${answer.foreign_objects?.length ? `<div class="muted">foreign objects: ${esc(answer.foreign_objects.join(', '))}</div>` : ''}
${gen.retry_feedback ? `<div class="muted">retry after: ${esc(gen.retry_feedback)}</div>` : ''}
${gen.prompt_summary ? `<details><summary>prompt summary</summary><pre>${esc(gen.prompt_summary)}</pre></details>` : ''}
${gen.image_prompt ? `<details><summary>image prompt</summary><pre>${esc(gen.image_prompt)}</pre></details>` : ''}
<div class="muted">kept ${esc(c.kept_at)} · <a href="${enc(wall)}/${enc(c.n)}.json">record</a></div>
</div>`;
}

function taskPage(task, index, gameUrl) {
  const walls = [...(index.walls ?? [])].sort((a, b) => Number(a.accepted) - Number(b.accepted));
  const sections = walls.map((w) => {
    const pos = w.position;
    const where = pos
      ? `<a href="${esc(`${gameUrl}${gameUrl.includes('?') ? '&' : '?'}spawn=${pos.spawn.join(',')}`)}">open in game</a>
<span class="muted">E ${esc(pos.E)} N ${esc(pos.N)} · tile ${esc(pos.tile)}</span>` : '<span class="muted">no position</span>';
    return `<section class="wall" id="${esc(w.facade_id)}"><div class="head"><h2>${esc(w.facade_id)}</h2>${badge(w.accepted)}
<span class="muted">building ${esc(w.building_id)} · ${w.candidates.length} candidate${w.candidates.length === 1 ? '' : 's'}</span>${where}</div>
<div class="row">${w.candidates.map((c) => candidateCard(w.facade_id, c)).join('')}</div></section>`;
  }).join('');
  const c = index.counts ?? {};
  return page(`${task} facade candidates`, `<p><a href="../">All tasks</a></p><h1>${esc(task)}</h1>
<p class="muted">${c.walls ?? 0} walls · ${c.candidates ?? 0} candidates · ${c.accepted ?? 0} accepted · ${c.rejected ?? 0} rejected ·
updated ${esc(index.updated)} · <a href="index.json">index.json</a></p>${sections}`);
}

/** Request handler for PREFIX; `gameUrl` is the game page the "open in game" links extend with `spawn=E,N,heading`. */
export function facadeCandidates({ root, gameUrl = '/?tiles=/tiles/tileset.json' }) {
  return (req, res) => {
    const p = (req.url ?? '/').split('?')[0];
    let parts;
    try { parts = p.slice(PREFIX.length).split('/').filter(Boolean).map(decodeURIComponent); } catch { parts = null; }
    if (!parts || parts.some((s) => !valid(s))) return send(res, 404, 'not found', 'text/plain');
    if (!p.endsWith('/') && parts.length < 2) return send(res, 301, '', 'text/plain', { Location: `${p}/` });
    if (parts.length === 0) return send(res, 200, taskList(root));
    const [task, ...rest] = parts;
    if (rest.length === 0) {
      const index = readIndex(root, task);
      return index ? send(res, 200, taskPage(task, index, gameUrl)) : send(res, 404, 'no such task', 'text/plain');
    }
    const file = rest.join('/');
    if (!(file === 'index.json' || (rest.length === 2 && /^\d+\.(png|json)$/.test(rest[1])))) return send(res, 404, 'not found', 'text/plain');
    return serveFile(req, res, path.join(root, task, ...rest), { cacheControl: 'no-cache' });
  };
}

/** True when `url` is under PREFIX. */
export const isFacadeCandidates = (url) => { const p = (url ?? '').split('?')[0]; return p === PREFIX || p.startsWith(`${PREFIX}/`); };
