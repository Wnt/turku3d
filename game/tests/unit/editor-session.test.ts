import { describe, expect, test } from 'vitest';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { EditSession, type EditRecord, type KeyValue, type SessionRow, type SessionTransport } from '../../src/dev/editor/session';
import { stateOf } from '../../src/dev/editor';
import { editSessionsMiddleware } from '../../../tools/dev/editSessions.mjs';

const store = (): KeyValue & { map: Map<string, string> } => {
  const map = new Map<string, string>();
  return { map, getItem: k => map.get(k) ?? null, setItem: (k, v) => { map.set(k, v); }, removeItem: k => { map.delete(k); } };
};
const transport = (ok = true) => {
  const sent = { meta: [] as unknown[], rows: [] as SessionRow[][], blobs: [] as string[] };
  const t: SessionTransport & { sent: typeof sent; ok: boolean } = {
    sent, ok,
    putJson: async (_s, _f, v) => { if (!t.ok) return false; sent.meta.push(structuredClone(v)); return true; },
    appendRows: async (_s, rows) => { if (!t.ok) return false; sent.rows.push(structuredClone(rows)); return true; },
    putBlob: async (_s, f) => { sent.blobs.push(f); return t.ok; },
  };
  return t;
};
const frames = (x: number, z: number) => ({ engine: { x, y: 4, z }, epsg3877: { E: 0, N: 0, H: 4 }, epsg3067: { E: 0, N: 0, H: 4 } });
function edit(session: EditSession, key: string, x: number, action: EditRecord['action'] = 'move'): EditRecord {
  const seq = session.nextSeq;
  return { format: 'turku3d-edit', version: 1, id: session.rowId(seq), seq, session: session.id, at: new Date().toISOString(), action,
    changes: { translated: true, rotated: false, height: false },
    object: { key, category: 'prop', type: 'bench', tile: 't0_0_0', object_id: key.split(':').slice(2).join(':'), provenance: {} },
    original: { ...frames(0, 0), yaw_deg: 10, heading_deg: 80 }, before: { ...frames(0, 0), yaw_deg: 10, heading_deg: 80 },
    after: action === 'delete' ? null : { ...frames(x, 0), yaw_deg: 25, heading_deg: 65 },
    delta: action === 'delete' ? null : { dx: x, dz: 0, dy: 0, horizontal_m: x, yaw_deg: 15 }, preview: [], camera: { ...frames(0, 5), heading_deg: 0, pitch_deg: -10, fov_deg: 60 },
    tile_pin: null, context: null, reason: null, shots: { before: null, after: null } };
}

describe('edit sessions', () => {
  test('commit, amend, undo and the effective edits per object; a reload restores the open session and its outbox', async () => {
    const s = store(), t = transport(false);
    const session = EditSession.start(s, t, { build: { loaded: 'abc' }, tiles: {}, device: {} });
    session.setActive(true);
    session.commit(edit(session, 'prop:t0_0_0:GIS_Penkit:1', 2));
    session.commit(edit(session, 'prop:t0_0_0:GIS_Penkit:1', 3));
    session.commit(edit(session, 'prop:t0_0_0:GIS_Penkit:2', 0, 'delete'));
    session.tag(session.rowId(2), 'wrong_facing');
    expect(session.undo()?.id).toBe(session.rowId(3));
    expect(session.effective().map(e => e.seq)).toEqual([1, 2]);
    expect(session.latestByKey().get('prop:t0_0_0:GIS_Penkit:1')?.reason).toBe('wrong_facing');
    expect(session.meta.counts).toEqual({ edits: 3, undone: 1, effective: 2 });
    // Unreachable server: nothing acknowledged, everything stays in the outbox across a reload.
    expect(await session.flush()).toBe(false);
    expect(session.unsent).toBe(5); expect(session.pending).toBe(6);
    const reloaded = EditSession.restore(s, t)!;
    expect(reloaded.id).toBe(session.id); expect(reloaded.active).toBe(true); expect(reloaded.unsent).toBe(5);
    t.ok = true;
    expect(await reloaded.flush()).toBe(true);
    expect(reloaded.pending).toBe(0);
    expect(t.sent.rows[0].map(r => r.action)).toEqual(['move', 'move', 'delete', 'tag', 'undo']);
    expect(t.sent.rows[0][3]).toMatchObject({ action: 'tag', edit: reloaded.rowId(2), reason: 'wrong_facing' });
    expect(t.sent.meta.length).toBe(1); // session.json after the rows
    // A second undo removes the second move; the first remains the object's state.
    reloaded.undo();
    const state = stateOf(reloaded.latestByKey().get('prop:t0_0_0:GIS_Penkit:1')!);
    expect(state.after.x).toBe(2); expect(state.turnDeg).toBe(15); expect(state.deleted).toBe(false);
    // Closing the session ends it; once flushed the store forgets it.
    reloaded.end(); await reloaded.flush();
    expect(EditSession.restore(s, t)).toBeNull();
    expect(s.map.size).toBe(0);
  });

  test('a send that never settles does not stall the queue; an amendment made in flight is sent again; batches stay small', async () => {
    const s = store();
    const hanging: SessionTransport & { calls: SessionRow[][]; hang: boolean; resolve?: (ok: boolean) => void } = {
      calls: [], hang: true,
      putJson: async () => true,
      appendRows: (_s, rows) => { hanging.calls.push(structuredClone(rows)); return hanging.hang ? new Promise(r => { hanging.resolve = r; }) : Promise.resolve(true); },
      putBlob: async () => true,
    };
    const session = EditSession.start(s, hanging, { build: {}, tiles: {}, device: {} });
    session.commit(edit(session, 'prop:t0_0_0:GIS_Penkit:1', 2));
    const first = session.flush(0);
    expect(await session.flush(1000)).toBe(false); // in flight
    // Context arrives while the commit's row is in flight: acknowledging the first send must not drop it.
    session.amend(session.rowId(1), { context: { before: { surface: 'road' }, after: null } });
    hanging.resolve!(true); await first;
    expect(session.unsent).toBe(1);
    hanging.hang = false;
    expect(await session.flush(2000)).toBe(true);
    expect(hanging.calls.at(-1)![0]).toMatchObject({ rev: 2, context: { before: { surface: 'road' } } });
    // A request that never answers (the 22:17 stall) blocks for at most a minute; the transport itself times out.
    hanging.hang = true;
    for (let i = 0; i < 20; i++) session.commit(edit(session, `prop:t0_0_0:GIS_Penkit:${10 + i}`, 1));
    void session.flush(10_000);
    expect(await session.flush(20_000)).toBe(false);
    hanging.hang = false; hanging.calls.length = 0;
    expect(await session.flush(80_000)).toBe(true);
    expect(session.unsent).toBe(0);
    expect(hanging.calls.map(c => c.length)).toEqual([8, 8, 4]);
    hanging.resolve!(true); // the abandoned request finally answering acknowledges nothing twice
    expect(session.unsent).toBe(0);
  });

  test('the dev server writes session.json, one line per row id in seq order, and shots; rejects bad paths', async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), 'edits-'));
    const handle = editSessionsMiddleware(root);
    const call = async (method: string, url: string, body?: unknown, type = 'application/json') => {
      const data = body === undefined ? Buffer.alloc(0) : Buffer.isBuffer(body) ? body : Buffer.from(JSON.stringify(body));
      const req = Object.assign(Readable.from([data]), { method, url, headers: { 'content-type': type } });
      let status = 0, text = '';
      await new Promise<void>(resolve => {
        const res = { writeHead: (s: number) => { status = s; }, end: (b?: Buffer | string) => { text = String(b ?? ''); resolve(); } };
        void handle(req, res, () => { status = 404; resolve(); });
      });
      return { status, text };
    };
    const id = '20261103-221500-abc123';
    const row = (seq: number, extra: Record<string, unknown> = {}) => ({ id: `${id}-${String(seq).padStart(4, '0')}`, seq, session: id, action: 'move', ...extra });
    expect((await call('PUT', `/__edits/${id}/session.json`, { id, started: 'now' })).status).toBe(200);
    expect((await call('POST', `/__edits/${id}/edits.jsonl`, [row(2), row(1)])).status).toBe(200);
    expect((await call('POST', `/__edits/${id}/edits.jsonl`, [row(2, { reason: 'wrong_facing' }), row(3, { action: 'undo' })])).status).toBe(200);
    const lines = (await fs.readFile(path.join(root, id, 'edits.jsonl'), 'utf8')).trim().split('\n').map(l => JSON.parse(l));
    expect(lines.map(l => l.seq)).toEqual([1, 2, 3]); expect(lines[1].reason).toBe('wrong_facing');
    // Idempotent by id and revision: a queued older copy neither duplicates nor clobbers the stored one.
    await call('POST', `/__edits/${id}/edits.jsonl`, [row(4, { rev: 2, context: { full: true } })]);
    const stale = await call('POST', `/__edits/${id}/edits.jsonl`, [row(4, { rev: 1, context: null }), row(4, { rev: 1 })]);
    expect(JSON.parse(stale.text)).toMatchObject({ rows: 4, stale: 2 });
    const after = (await fs.readFile(path.join(root, id, 'edits.jsonl'), 'utf8')).trim().split('\n').map(l => JSON.parse(l));
    expect(after.filter(l => l.seq === 4)).toEqual([expect.objectContaining({ rev: 2, context: { full: true } })]);
    expect((await call('PUT', `/__edits/${id}/shots/${id}-0001-before.jpg`, Buffer.from([0xff, 0xd8, 0xff]), 'image/jpeg')).status).toBe(200);
    expect((await fs.stat(path.join(root, id, 'shots', `${id}-0001-before.jpg`))).size).toBe(3);
    expect((await call('GET', `/__edits/${id}/session.json`)).text).toContain('"started"');
    // Dot segments normalise away before routing: neither request reaches a file outside the session.
    expect((await call('PUT', `/__edits/../x/session.json`, {})).status).toBe(404);
    expect((await call('PUT', `/__edits/${id}/shots/../../evil.jpg`, Buffer.from([1]))).status).toBe(400);
    expect((await call('PUT', `/__edits/${id}/shots/a/b.jpg`, Buffer.from([1]))).status).toBe(405);
    expect((await fs.readdir(root)).sort()).toEqual([id]);
    expect((await call('POST', `/__edits/${id}/edits.jsonl`, [{ id: 'x', seq: 1, session: 'other' }])).status).toBe(400);
    expect((await call('PUT', `/__edits/${id}/session.json`, { id: 'other' })).status).toBe(400);
    expect((await call('GET', '/elsewhere')).status).toBe(404);
    await fs.rm(root, { recursive: true });
  });
});
