// The short plain-text summary of a closed edit session (the close dialog's text and summary.txt next to session.json):
// one header line, one line per prop type and action with counts, typical magnitudes, the chosen reasons, tiles and an
// approximate EPSG:3877 position, and a pointer to the full rows. Pure: no DOM, the same input gives the same text.
import type { EditRecord, SessionMeta, SessionRow } from './session';

/** Net change thresholds: below them a component of an edit counts as unchanged (pick jitter, snap). */
export const SUMMARY = { moveM: 0.3, turnDeg: 3, heightM: 0.1, maxGroups: 8 } as const;

export const REASON_TEXT: Record<string, string> = {
  on_road_or_path: 'on road or path', wrong_facing: 'wrong facing', does_not_exist: "doesn't exist",
  misaligned_with_lidar: 'misaligned with LiDAR',
};

export interface SummaryOptions {
  /** Where the session was played (location.host: city.lab.madekivi.fi, localhost:5220…). */
  host: string;
  /** The multiplayer nick, when the session was played in multiplayer. */
  player?: string | null;
  /** Minutes east of UTC for the times (default: this machine's offset at the session start). */
  utcOffsetMin?: number;
}

/** The net change of each edited object: its last effective edit, the newest reason tag folded in. */
export function finalEdits(rows: readonly SessionRow[]): EditRecord[] {
  const undone = new Set<string>(), reasons = new Map<string, string | null>();
  for (const r of rows) {
    if (r.action === 'undo') undone.add(r.undoes);
    else if (r.action === 'tag') reasons.set(r.edit, r.reason);
  }
  const out = new Map<string, EditRecord>();
  for (const r of rows) {
    if (r.action === 'undo' || r.action === 'tag' || undone.has(r.id)) continue;
    out.set(r.object.key, reasons.has(r.id) ? { ...r, reason: reasons.get(r.id) ?? null } : r);
  }
  return [...out.values()];
}

type Kind = 'deleted' | 'moved + turned' | 'moved' | 'turned' | 'height changed' | 'unchanged';

/** What an object's net change amounts to once jitter below the thresholds is ignored. */
export function kindOf(e: EditRecord): Kind {
  if (e.action === 'delete' || !e.after || !e.delta) return 'deleted';
  const moved = e.delta.horizontal_m >= SUMMARY.moveM, turned = Math.abs(e.delta.yaw_deg) >= SUMMARY.turnDeg;
  if (moved && turned) return 'moved + turned';
  if (moved) return 'moved';
  if (turned) return 'turned';
  return Math.abs(e.delta.dy) >= SUMMARY.heightM ? 'height changed' : 'unchanged';
}

const median = (xs: number[]) => { const s = [...xs].sort((a, b) => a - b), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const metres = (x: number) => x < 10 ? x.toFixed(1) : x.toFixed(0);

/** "2.1 m" or "1.2–5.2 m (med 2.0)". */
function spanM(xs: number[]) {
  const lo = Math.min(...xs), hi = Math.max(...xs);
  if (xs.length === 1 || hi - lo < 0.15) return `${metres(median(xs))} m`;
  return `${metres(lo)}–${metres(hi)} m (med ${metres(median(xs))})`;
}

/** Turns as magnitude plus direction counts (+ is counter-clockwise from above): "~90° ccw", "~91° (3 ccw, 1 cw)", "30–175° (med 124°; 9 ccw, 6 cw)". */
function spanTurn(degs: number[]) {
  const abs = degs.map(Math.abs), lo = Math.min(...abs), hi = Math.max(...abs), m = Math.round(median(abs));
  const ccw = degs.filter(d => d > 0).length, cw = degs.length - ccw;
  const mixed = ccw && cw, dir = mixed ? `${ccw} ccw, ${cw} cw` : ccw ? 'ccw' : 'cw';
  if (hi - lo <= 8) return mixed ? `~${m}° (${dir})` : `~${m}° ${dir}`;
  return `${Math.round(lo)}–${Math.round(hi)}° (med ${m}°; ${dir})`;
}

const signed = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(2)} m`;

/** The median change of the distance to the nearest carriageway edge (context.before/after.road), if both are known. */
function carriageway(group: EditRecord[]) {
  const pairs = group.flatMap(e => {
    const c = e.context as { before?: { road?: { car?: boolean; edge_m?: number } }; after?: { road?: { car?: boolean; edge_m?: number } } } | null;
    const b = c?.before?.road, a = c?.after?.road;
    return b?.car && a?.car && typeof b.edge_m === 'number' && typeof a.edge_m === 'number' ? [[b.edge_m, a.edge_m]] : [];
  });
  if (pairs.length * 2 < group.length) return '';
  const before = median(pairs.map(p => p[0])), after = median(pairs.map(p => p[1]));
  return Math.abs(after - before) < 0.3 ? '' : `, carriageway edge ${metres(before)} → ${metres(after)} m`;
}

function reasons(group: EditRecord[]) {
  const counts = new Map<string, number>();
  for (const e of group) { const r = e.reason ? REASON_TEXT[e.reason] ?? e.reason.replace(/_/g, ' ') : ''; counts.set(r, (counts.get(r) ?? 0) + 1); }
  // Most common first; the given reasons ahead of "none" on a tie.
  const list = [...counts].sort((a, b) => b[1] - a[1] || +!a[0] - +!b[0] || a[0].localeCompare(b[0]));
  if (list.length === 1) return list[0][0] ? `reason: ${list[0][0]}` : 'no reason';
  return `reason: ${list.map(([r, n]) => `${r || 'none'} ×${n}`).join(', ')}`;
}

/** Tiles (most edits first, at most three named) and the group's mean EPSG:3877 position with its spread. */
function where(group: EditRecord[]) {
  const tiles = new Map<string, number>();
  for (const e of group) tiles.set(e.object.tile, (tiles.get(e.object.tile) ?? 0) + 1);
  const named = [...tiles].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(t => t[0]);
  const tileText = named.slice(0, 3).join(', ') + (named.length > 3 ? ` +${named.length - 3} tiles` : '');
  const ps = group.map(e => e.original.epsg3877);
  const E = ps.reduce((s, p) => s + p.E, 0) / ps.length, N = ps.reduce((s, p) => s + p.N, 0) / ps.length;
  const spread = Math.max(...ps.map(p => Math.hypot(p.E - E, p.N - N)));
  return `${tileText} around E ${Math.round(E)} N ${Math.round(N)}${spread >= 15 ? ` (within ${Math.round(spread / 5) * 5} m)` : ''}`;
}

function magnitude(kind: Kind, group: EditRecord[]) {
  const d = group.map(e => e.delta!);
  const move = () => `moved ${spanM(d.map(x => x.horizontal_m))}${carriageway(group)}`;
  const turn = () => `turned ${spanTurn(d.map(x => x.yaw_deg))}`;
  switch (kind) {
    case 'deleted': return 'deleted';
    case 'moved + turned': return `${move()}, ${turn()}`;
    case 'moved': return move();
    case 'turned': return turn();
    case 'height changed': return `height ${signed(median(d.map(x => x.dy)))}${d.length > 1 ? ' (median)' : ''}`;
    case 'unchanged': return 'picked and dropped, no net change';
  }
}

const ORDER: Kind[] = ['deleted', 'moved + turned', 'moved', 'turned', 'height changed', 'unchanged'];
const pad = (n: number) => String(n).padStart(2, '0');

/** Local date and time at a fixed UTC offset: "2026-11-04 22:10". */
function stamp(iso: string, offsetMin: number) {
  const t = new Date(Date.parse(iso) + offsetMin * 60_000);
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())} ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`;
}
const zone = (offsetMin: number) => offsetMin === 0 ? 'UTC' : `UTC${offsetMin > 0 ? '+' : '−'}${Math.floor(Math.abs(offsetMin) / 60)}${Math.abs(offsetMin) % 60 ? `:${pad(Math.abs(offsetMin) % 60)}` : ''}`;

/** The session summary: about ten lines of plain text, ready to paste into a chat. */
export function summarizeSession(meta: SessionMeta, rows: readonly SessionRow[], options: SummaryOptions): string {
  const edits = finalEdits(rows);
  const offset = options.utcOffsetMin ?? -new Date(meta.started).getTimezoneOffset();
  const start = stamp(meta.started, offset), end = meta.ended ? stamp(meta.ended, offset) : null;
  const time = !end ? `${start} ${zone(offset)}, not closed` : end.slice(0, 10) === start.slice(0, 10)
    ? `${start}–${end.slice(11)} ${zone(offset)}` : `${start} – ${end} ${zone(offset)}`;
  const sha = typeof meta.build?.loaded === 'string' ? meta.build.loaded.slice(0, 8) + (meta.build.dirty ? '+dirty' : '') : 'unknown';
  const commits = rows.filter(r => r.action !== 'undo' && r.action !== 'tag').length;
  const undone = rows.filter(r => r.action === 'undo').length;
  const extra = commits > edits.length ? `${commits} edits${undone ? `, ${undone} undone` : ''}` : '';
  const header = [`Edit session ${meta.id}`, options.host, options.player ? `player ${options.player}` : '', `build ${sha}`, time,
    `${edits.length} edited object${edits.length === 1 ? '' : 's'}${extra ? ` (${extra})` : ''}`].filter(Boolean).join(' · ');

  const groups = new Map<string, { type: string; kind: Kind; edits: EditRecord[] }>();
  for (const e of edits) {
    const kind = kindOf(e), key = `${e.object.type}\u0000${kind}`;
    const g = groups.get(key) ?? { type: e.object.type, kind, edits: [] };
    g.edits.push(e); groups.set(key, g);
  }
  const sorted = [...groups.values()].sort((a, b) => b.edits.length - a.edits.length || ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind) || a.type.localeCompare(b.type));
  const lines = sorted.slice(0, SUMMARY.maxGroups).map(g =>
    `- ${g.edits.length} ${g.type} ${magnitude(g.kind, g.edits)} (${reasons(g.edits)}) · ${where(g.edits)}`);
  const rest = sorted.slice(SUMMARY.maxGroups);
  if (rest.length) lines.push(`- … ${rest.reduce((n, g) => n + g.edits.length, 0)} more edits in ${rest.length} smaller groups (${rest.map(g => g.type).filter((t, i, a) => a.indexOf(t) === i).join(', ')})`);
  if (!edits.length) lines.push('- no edits');
  lines.push(`Full details: session ${meta.id} (scripts/dev/edit_sessions.py show ${meta.id})`);
  return [header, ...lines].join('\n') + '\n';
}
