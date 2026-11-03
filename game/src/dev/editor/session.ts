// Edit sessions: the durable output of the object editor, for an agent that turns the edits into generator rules
// (~/turku3d/loops/editor/EDIT-FORMAT.md). The dev server stores them under ~/turku3d/edits/<session>/ (session.json,
// edits.jsonl, shots/); the browser keeps the session, its effective edits and an outbox of unsent rows in
// localStorage, so a reload restores the previews and resends what the server has not acknowledged.

export const EDIT_FORMAT = 'turku3d-edit';
export const EDIT_VERSION = 1;
const STORE = 'turku3d.editor.v1';

export interface Frames { engine: { x: number; y: number; z: number }; epsg3877: { E: number; N: number; H: number }; epsg3067: { E: number; N: number; H: number } }
/** A placement transform: ground contact point and yaw (deg, three.js rotation.y; null when unknown). */
export interface Transform extends Frames { yaw_deg: number | null; heading_deg: number | null }

export interface EditRecord {
  format: typeof EDIT_FORMAT; version: typeof EDIT_VERSION;
  id: string; seq: number; session: string; at: string;
  action: 'move' | 'rotate' | 'delete';
  changes: { translated: boolean; rotated: boolean; height: boolean };
  object: { key: string; category: string; type: string; tile: string; object_id: string; provenance: Record<string, unknown> };
  /** The generator's own placement, before any edit of this session. */
  original: Transform;
  /** The placement before this edit (the previous effective edit, or `original`). */
  before: Transform;
  /** The placement after it; null for a delete. */
  after: Transform | null;
  /** after - original: horizontal move (m), height change (m) and turn (deg, counter-clockwise from above). */
  delta: { dx: number; dz: number; dy: number; horizontal_m: number; yaw_deg: number } | null;
  /** Column-major 4×4 world matrix taking the original placement to `after` (previews; identity for a delete). */
  preview: number[];
  camera: Frames & { heading_deg: number; pitch_deg: number; fov_deg: number };
  tile_pin: Record<string, unknown> | null;
  context: { before: unknown; after: unknown } | null;
  reason: string | null;
  shots: { before: string | null; after: string | null };
}
export interface UndoRecord { format: typeof EDIT_FORMAT; version: typeof EDIT_VERSION; id: string; seq: number; session: string; at: string; action: 'undo'; undoes: string }
export type SessionRow = EditRecord | UndoRecord;

export interface SessionMeta {
  format: 'turku3d-edit-session'; version: 1; id: string;
  started: string; ended: string | null; updated: string;
  build: Record<string, unknown>;
  tiles: Record<string, unknown>;
  device: Record<string, unknown>;
  counts: { edits: number; undone: number; effective: number };
}

/** The dev server endpoint (tools/dev/editSessions.mjs). */
export interface SessionTransport {
  putJson(session: string, file: string, value: unknown): Promise<boolean>;
  appendRows(session: string, rows: SessionRow[]): Promise<boolean>;
  putBlob(session: string, file: string, blob: Blob): Promise<boolean>;
}

export const httpTransport = (base = '/__edits/'): SessionTransport => {
  const send = async (url: string, init: RequestInit) => {
    try { return (await fetch(url, { ...init, keepalive: init.body instanceof Blob ? false : true, cache: 'no-store' })).ok; } catch { return false; }
  };
  return {
    putJson: (session, file, value) => send(`${base}${session}/${file}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value, null, 1) }),
    appendRows: (session, rows) => send(`${base}${session}/edits.jsonl`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(rows) }),
    putBlob: (session, file, blob) => send(`${base}${session}/${file}`, { method: 'PUT', headers: { 'Content-Type': blob.type || 'image/jpeg' }, body: blob }),
  };
};

export interface KeyValue { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }
const memory = (): KeyValue => { const m = new Map<string, string>(); return { getItem: k => m.get(k) ?? null, setItem: (k, v) => { m.set(k, v); }, removeItem: k => { m.delete(k); } }; };
export function defaultStore(): KeyValue {
  try { localStorage.setItem(`${STORE}.probe`, '1'); localStorage.removeItem(`${STORE}.probe`); return localStorage; } catch { return memory(); }
}

export function newSessionId(now = new Date()) {
  const stamp = now.toISOString().slice(0, 19).replace(/[-:]/g, '').replace('T', '-');
  return `${stamp}-${Math.random().toString(36).slice(2, 8)}`;
}

interface Stored { meta: SessionMeta; active: boolean; rows: SessionRow[]; outbox: string[]; metaDirty: boolean }

/** One open session: its rows in order, the undo state and the unsent outbox. */
export class EditSession {
  readonly rows: SessionRow[];
  active: boolean;
  private outbox: Set<string>;
  private metaDirty: boolean;
  private sending = false;
  error: string | null = null;
  /** Rows acknowledged by the dev server (status line). */
  sent = 0;

  private constructor(readonly meta: SessionMeta, stored: Stored | null, private store: KeyValue, private transport: SessionTransport) {
    this.rows = stored?.rows ?? [];
    this.active = stored?.active ?? false;
    this.outbox = new Set(stored?.outbox ?? []);
    this.metaDirty = stored?.metaDirty ?? true;
  }

  /** The stored open session, if any. */
  static restore(store: KeyValue, transport: SessionTransport): EditSession | null {
    try {
      const stored = JSON.parse(store.getItem(STORE) ?? 'null') as Stored | null;
      if (!stored?.meta?.id || stored.meta.ended) return null;
      return new EditSession(stored.meta, stored, store, transport);
    } catch { return null; }
  }

  static start(store: KeyValue, transport: SessionTransport, meta: Omit<SessionMeta, 'format' | 'version' | 'id' | 'started' | 'ended' | 'updated' | 'counts'>, now = new Date()) {
    const id = newSessionId(now), at = now.toISOString();
    const session = new EditSession({ format: 'turku3d-edit-session', version: 1, id, started: at, ended: null, updated: at,
      counts: { edits: 0, undone: 0, effective: 0 }, ...meta }, null, store, transport);
    session.save();
    return session;
  }

  get id() { return this.meta.id; }
  get nextSeq() { return this.rows.length + 1; }
  rowId(seq = this.nextSeq) { return `${this.id}-${String(seq).padStart(4, '0')}`; }

  /** Edits not undone, in commit order. */
  effective(): EditRecord[] {
    const undone = new Set(this.rows.flatMap(r => r.action === 'undo' ? [r.undoes] : []));
    return this.rows.filter((r): r is EditRecord => r.action !== 'undo' && !undone.has(r.id));
  }
  /** The latest effective edit of each object. */
  latestByKey(): Map<string, EditRecord> {
    const out = new Map<string, EditRecord>();
    for (const edit of this.effective()) out.set(edit.object.key, edit);
    return out;
  }

  commit(edit: EditRecord) { this.rows.push(edit); this.outbox.add(edit.id); this.touch(); }
  /** Amend an unsent or sent edit before its reason window closes: rows are resent whole (the server keeps the last). */
  amend(id: string, change: Partial<Pick<EditRecord, 'reason' | 'context' | 'shots' | 'tile_pin'>>) {
    const edit = this.rows.find(r => r.id === id && r.action !== 'undo') as EditRecord | undefined;
    if (!edit) return;
    Object.assign(edit, change);
    this.outbox.add(id); this.touch();
  }
  /** Undo the most recent effective edit: returns it, and appends the undo row. */
  undo(now = new Date()): EditRecord | null {
    const last = this.effective().at(-1);
    if (!last) return null;
    const seq = this.nextSeq;
    const row: UndoRecord = { format: EDIT_FORMAT, version: EDIT_VERSION, id: this.rowId(seq), seq, session: this.id, at: now.toISOString(), action: 'undo', undoes: last.id };
    this.rows.push(row); this.outbox.add(row.id); this.touch(now);
    return last;
  }
  setActive(active: boolean) { if (this.active !== active) { this.active = active; this.save(); } }
  end(now = new Date()) { this.meta.ended = now.toISOString(); this.touch(now); }

  private touch(now = new Date()) {
    const effective = this.effective().length, edits = this.rows.filter(r => r.action !== 'undo').length;
    this.meta.counts = { edits, undone: edits - effective, effective };
    this.meta.updated = now.toISOString();
    this.metaDirty = true;
    this.save();
  }

  save() {
    const stored: Stored = { meta: this.meta, active: this.active, rows: this.rows, outbox: [...this.outbox], metaDirty: this.metaDirty };
    try {
      if (this.meta.ended && !this.outbox.size && !this.metaDirty) this.store.removeItem(STORE);
      else this.store.setItem(STORE, JSON.stringify(stored));
    } catch { /* A full store keeps the session in memory; the outbox still flushes. */ }
  }

  get pending() { return this.outbox.size + (this.metaDirty ? 1 : 0); }

  /** Send session.json and every unacknowledged row. Rows are idempotent by id (the server keeps one line per id). */
  async flush(): Promise<boolean> {
    if (this.sending) return false;
    this.sending = true;
    try {
      if (this.metaDirty) {
        if (!await this.transport.putJson(this.id, 'session.json', this.meta)) { this.error = 'edit server unreachable'; return false; }
        this.metaDirty = false;
      }
      const rows = this.rows.filter(r => this.outbox.has(r.id));
      if (rows.length) {
        if (!await this.transport.appendRows(this.id, rows)) { this.error = 'edit server unreachable'; return false; }
        for (const r of rows) this.outbox.delete(r.id);
        this.sent += rows.length;
      }
      this.error = null;
      return true;
    } finally { this.sending = false; this.save(); }
  }

  putShot(file: string, blob: Blob) { return this.transport.putBlob(this.id, file, blob); }
}
