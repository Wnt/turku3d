import { describe, expect, test } from 'vitest';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { finalEdits, kindOf, summarizeSession } from '../../src/dev/editor/summary';
import { EditSession, type EditRecord, type KeyValue, type SessionMeta, type SessionRow, type SessionTransport } from '../../src/dev/editor/session';
import { editSessionsMiddleware } from '../../../tools/dev/editSessions.mjs';

const ID = '20261104-201000-abc123';
const at = (E: number, N: number) => ({ engine: { x: 0, y: 0, z: 0 }, epsg3877: { E, N, H: 5 }, epsg3067: { E: 0, N: 0, H: 5 } });
let seq = 0;
interface Spec { type: string; tile?: string; E?: number; N?: number; move?: number; turn?: number; dy?: number; del?: boolean; reason?: string | null;
  road?: [number, number]; key?: string }
function edit(s: Spec): EditRecord {
  seq++;
  const E = s.E ?? 23459847, N = s.N ?? 6704463, original = { ...at(E, N), yaw_deg: 0, heading_deg: 90 };
  const del = !!s.del;
  return { format: 'turku3d-edit', version: 1, id: `${ID}-${String(seq).padStart(4, '0')}`, seq, session: ID, at: '2026-11-04T20:11:00Z',
    action: del ? 'delete' : s.move ? 'move' : 'rotate', changes: { translated: !!s.move, rotated: !!s.turn, height: !!s.dy },
    object: { key: s.key ?? `prop:${s.tile ?? 't0_1_0'}:${s.type}:${seq}`, category: 'prop', type: s.type, tile: s.tile ?? 't0_1_0', object_id: `x:${seq}`, provenance: {} },
    original, before: original, after: del ? null : { ...at(E + (s.move ?? 0), N), yaw_deg: s.turn ?? 0, heading_deg: 0 },
    delta: del ? null : { dx: s.move ?? 0, dz: 0, dy: s.dy ?? 0, horizontal_m: s.move ?? 0, yaw_deg: s.turn ?? 0 },
    preview: [], camera: { ...at(E, N), heading_deg: 0, pitch_deg: 0, fov_deg: 60 }, tile_pin: null,
    context: s.road ? { before: { road: { car: true, edge_m: s.road[0] } }, after: { road: { car: true, edge_m: s.road[1] } } } : null,
    reason: s.reason ?? null, shots: { before: null, after: null } };
}
const meta = (over: Partial<SessionMeta> = {}): SessionMeta => ({ format: 'turku3d-edit-session', version: 1, id: ID,
  started: '2026-11-04T20:10:00.000Z', ended: '2026-11-04T20:31:00.000Z', updated: '2026-11-04T20:31:00.000Z',
  build: { loaded: '23728096ec577bf0b673eb93735fe599b7aa5890', dirty: false }, tiles: {}, device: {}, counts: { edits: 0, undone: 0, effective: 0 }, ...over });
const summary = (rows: SessionRow[], over: Partial<SessionMeta> = {}, player?: string) =>
  summarizeSession(meta(over), rows, { host: 'city.lab.madekivi.fi', utcOffsetMin: 120, player });

describe('edit session summary', () => {
  test('groups by type and net action, with counts, magnitudes, reasons, tiles and a position', () => {
    seq = 0;
    const rows = [
      ...[91, 89, 90, 92, 88, 90].map(turn => edit({ type: 'bike_rack', turn, reason: 'wrong_facing', E: 23459840, N: 6704460 })),
      edit({ type: 'traffic_signal', move: 2, reason: 'on_road_or_path', road: [0.2, 2.4], tile: 't0_0_0', E: 23459700, N: 6704400 }),
      edit({ type: 'traffic_signal', move: 5, reason: 'on_road_or_path', road: [0.4, 5.1], tile: 't0_1_0', E: 23459720, N: 6704410 }),
      edit({ type: 'traffic_signal', move: 3, reason: 'on_road_or_path', road: [0.3, 3.0], tile: 't0_1_0', E: 23459710, N: 6704420 }),
      edit({ type: 'traffic_sign', del: true, reason: 'does_not_exist' }),
      edit({ type: 'traffic_sign', del: true, reason: null }),
      edit({ type: 'bench', move: 0.8, turn: 0.5 }),
    ];
    const text = summary(rows);
    const lines = text.trimEnd().split('\n');
    expect(lines[0]).toBe(`Edit session ${ID} · city.lab.madekivi.fi · build 23728096 · 2026-11-04 22:10–22:31 UTC+2 · 12 edited objects`);
    expect(lines[1]).toBe('- 6 bike_rack turned ~90° ccw (reason: wrong facing) · t0_1_0 around E 23459840 N 6704460');
    expect(lines[2]).toBe('- 3 traffic_signal moved 2.0–5.0 m (med 3.0), carriageway edge 0.3 → 3.0 m (reason: on road or path) · t0_1_0, t0_0_0 around E 23459710 N 6704410');
    expect(lines[3]).toBe("- 2 traffic_sign deleted (reason: doesn't exist ×1, none ×1) · t0_1_0 around E 23459847 N 6704463");
    // Jitter below the thresholds does not count: a 0.5° turn with a 0.8 m move is a move.
    expect(lines[4]).toBe('- 1 bench moved 0.8 m (no reason) · t0_1_0 around E 23459847 N 6704463');
    expect(lines.at(-1)).toBe(`Full details: session ${ID} (scripts/dev/edit_sessions.py show ${ID})`);
    expect(lines.length).toBeLessThanOrEqual(10);
  });

  test('mixed turn directions and wide spreads stay explicit; height-only and spread groups', () => {
    seq = 0;
    const rows = [
      edit({ type: 'bench', turn: 30, E: 23459000, N: 6704000 }), edit({ type: 'bench', turn: -175, E: 23459100, N: 6704000 }),
      edit({ type: 'bench', turn: 120, E: 23459050, N: 6704050 }),
      edit({ type: 'bike_rack', turn: 91 }), edit({ type: 'bike_rack', turn: -89 }),
      edit({ type: 'bin', dy: -0.4 }), edit({ type: 'bin', dy: -0.2, move: 0.1 }),
    ];
    const lines = summary(rows).split('\n');
    expect(lines[1]).toBe('- 3 bench turned 30–175° (med 120°; 2 ccw, 1 cw) (no reason) · t0_1_0 around E 23459050 N 6704017 (within 55 m)');
    expect(lines[2]).toBe('- 2 bike_rack turned ~90° (1 ccw, 1 cw) (no reason) · t0_1_0 around E 23459847 N 6704463');
    expect(lines[3]).toBe('- 2 bin height −0.30 m (median) (no reason) · t0_1_0 around E 23459847 N 6704463');
    expect(kindOf(edit({ type: 'x', move: 0.1, turn: 1 }))).toBe('unchanged');
    expect(kindOf(edit({ type: 'x', move: 1, turn: 10 }))).toBe('moved + turned');
  });

  test('net change per object: undo, re-edits and the newest reason tag; header counts the commits', () => {
    seq = 0;
    const first = edit({ type: 'bench', move: 1, key: 'prop:t0_1_0:bench:A' });
    const second = edit({ type: 'bench', move: 2.5, turn: 90, key: 'prop:t0_1_0:bench:A' });
    const undone = edit({ type: 'sign', del: true });
    const rows: SessionRow[] = [first, second, undone,
      { format: 'turku3d-edit', version: 1, id: `${ID}-0004`, seq: 4, session: ID, at: '', action: 'undo', undoes: undone.id },
      { format: 'turku3d-edit', version: 1, id: `${ID}-0005`, seq: 5, session: ID, at: '', action: 'tag', edit: second.id, reason: 'on_road_or_path' },
      { format: 'turku3d-edit', version: 1, id: `${ID}-0006`, seq: 6, session: ID, at: '', action: 'tag', edit: second.id, reason: 'wrong_facing' }];
    expect(finalEdits(rows).map(e => [e.id, e.reason])).toEqual([[second.id, 'wrong_facing']]);
    const lines = summary(rows, {}, 'Jonni').split('\n');
    expect(lines[0]).toContain('· city.lab.madekivi.fi · player Jonni · build 23728096 ·');
    expect(lines[0]).toMatch(/· 1 edited object \(3 edits, 1 undone\)$/);
    expect(lines[1]).toBe('- 1 bench moved 2.5 m, turned ~90° ccw (reason: wrong facing) · t0_1_0 around E 23459847 N 6704463');
  });

  test('at most eight group lines, the rest folded into one; an empty session says so', () => {
    seq = 0;
    const rows = Array.from({ length: 11 }, (_, i) => edit({ type: `type${i}`, del: true }));
    const lines = summary(rows).trimEnd().split('\n');
    expect(lines.length).toBe(11);
    expect(lines[9]).toBe('- … 3 more edits in 3 smaller groups (type7, type8, type9)');
    expect(summary([], { ended: null })).toContain('2026-11-04 22:10 UTC+2, not closed · 0 edited objects\n- no edits\n');
  });

  test('the session sends summary.txt after session.json and keeps it across a reload until stored', async () => {
    const map = new Map<string, string>();
    const store: KeyValue = { getItem: k => map.get(k) ?? null, setItem: (k, v) => { map.set(k, v); }, removeItem: k => { map.delete(k); } };
    const sent: string[] = [];
    let ok = false;
    const t: SessionTransport = {
      putJson: async (_s, f) => { if (ok) sent.push(f); return ok; },
      appendRows: async () => ok,
      putBlob: async (_s, f, blob) => { if (ok) sent.push(`${f}:${await blob.text()}`); return ok; },
    };
    const session = EditSession.start(store, t, { build: {}, tiles: {}, device: {} });
    session.end();
    session.setSummary('Edit session x\n');
    expect(await session.flush()).toBe(false);
    const reloaded = EditSession.restore(store, t)!;
    expect(reloaded.summary).toBe('Edit session x\n');
    ok = true;
    expect(await reloaded.flush()).toBe(true);
    expect(sent).toEqual(['session.json', 'summary.txt:Edit session x\n']);
    expect(reloaded.pending).toBe(0);
    expect(EditSession.restore(store, t)).toBeNull();
  });

  test('the server stores summary.txt; a public share is write-only, same-origin and within its quota', async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), 'edits-'));
    const call = async (handle: ReturnType<typeof editSessionsMiddleware>, method: string, url: string, body: string, headers: Record<string, string> = {}) => {
      const req = Object.assign(Readable.from([Buffer.from(body)]), { method, url, headers: { host: '127.0.0.1:7780', ...headers } });
      let status = 0, text = '';
      await new Promise<void>(resolve => {
        const res = { writeHead: (s: number) => { status = s; }, end: (b?: Buffer | string) => { text = String(b ?? ''); resolve(); } };
        void handle(req, res, () => { status = 404; resolve(); });
      });
      return { status, text };
    };
    const local = editSessionsMiddleware(root);
    expect((await call(local, 'PUT', `/__edits/${ID}/summary.txt`, 'Edit session …\n')).status).toBe(200);
    expect(await fs.readFile(path.join(root, ID, 'summary.txt'), 'utf8')).toBe('Edit session …\n');
    expect((await call(local, 'GET', `/__edits/${ID}/summary.txt`, '')).text).toBe('Edit session …\n');
    const share = editSessionsMiddleware(root, { writeOnly: true, quotaBytes: 64 });
    expect((await call(share, 'GET', `/__edits/${ID}/summary.txt`, '')).status).toBe(405);
    // Behind the tunnel Host is the local listener: the browser's own same-origin claim is accepted, a foreign one is not.
    expect((await call(share, 'PUT', `/__edits/${ID}/summary.txt`, 'x', { origin: 'https://city.lab.madekivi.fi', 'sec-fetch-site': 'same-origin' })).status).toBe(200);
    expect((await call(share, 'PUT', `/__edits/${ID}/summary.txt`, 'x', { origin: 'https://evil.example', 'sec-fetch-site': 'cross-site' })).status).toBe(403);
    expect((await call(share, 'PUT', `/__edits/${ID}/summary.txt`, 'y'.repeat(100))).status).toBe(507);
  });
});
