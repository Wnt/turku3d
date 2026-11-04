// Developer object editor (localhost): point the camera at a tree, sign, bench or light post, pick it, move, turn,
// lift or delete it with the gamepad, and the edit goes into an edit session (session.ts) for an agent to turn into
// generator rules (~/turku3d/loops/editor/EDIT-FORMAT.md). Edits show as local previews only (preview.ts): no tile,
// collision, physics or multiplayer state changes. Mapping: state.ts; README.md in this folder.
import * as THREE from 'three/webgpu';
import { isShareHost, localFeature } from '../../core/localOnly';
import { buildStatus } from '../../core/build';
import type { Action, Input } from '../../input';
import type { VisualTiles } from '../../world/tiles';
import type { VegetationLod } from '../../world/vegetationLod';
import type { StreetLights } from '../../render/lights';
import type { TrafficSignals } from '../../traffic/signals';
import type { CoarseTiles } from '../../world/coarseTiles';
import type { RayHit as PhysicsHit, RayFilter } from '../../physics/protocol';
import { EditableCatalog, type Editable, type V3 } from './catalog';
import { CandidateCycle, pickCandidates } from './pick';
import { EDITOR, EditorMachine, REASONS, idleFrame, type Command, type EditorFrame } from './state';
import { Previews, previewMatrix, type PreviewState } from './preview';
import { EditSession, EDIT_FORMAT, EDIT_VERSION, defaultStore, httpTransport, localTransport, sessionExport, type EditRecord, type SessionTransport, type KeyValue, type Transform } from './session';
import { framesOf } from './geodesy';
import { STANDING, groundUnder, measureContext, type Raycast } from './context';
import { provenanceOf } from './provenance';
import { EditorHud } from './hud';
import { SessionDialog } from './closeDialog';
import { summarizeSession } from './summary';
import { FrameShots } from './shots';
import { COLOR_MODES, lidarVisibility, pointCloudOverlay } from './pointCloudHook';
import { MATERIALS } from '../../shared/col/materials';

export interface EditorHosts {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  renderer: { domElement: HTMLCanvasElement };
  tiles: VisualTiles;
  vegetation: () => VegetationLod | null;
  lights: StreetLights | null;
  /** Drawn traffic-signal heads (TrafficClient.signals). */
  signals?: () => TrafficSignals | null;
  physics: { raycast(origin: V3, dir: V3, far: number, filter?: RayFilter): Promise<PhysicsHit | null> };
  coarse: CoarseTiles | null;
  /** Ambient facts for session.json (backend, viewport…). */
  device?: () => Record<string, unknown>;
  /** The multiplayer player while in a multiplayer session (session.json `player`, the summary header). */
  player?: () => { id: string; nick: string } | null;
  store?: KeyValue; transport?: SessionTransport;
}

/** On localhost by default; elsewhere with ?editor=1, never in a public release except on the share host (city.lab),
 *  where it stores nothing (editsLocalOnly). */
export const editorAllowed = (loc: { hostname: string; search: string } = location) =>
  localFeature(loc, 'editor') && (import.meta.env?.VITE_PUBLIC_RELEASE !== '1' || isShareHost(loc.hostname));
/** The public share accepts no uploads: edits stay in this browser and leave it only through the close dialog. */
export const editsLocalOnly = (loc: { hostname: string } = location) => isShareHost(loc.hostname);
const LAST_KEY = 'turku3d.editor.lastClosed';

const DEG = 180 / Math.PI;
const LIDAR_KEY = 'turku3d.editor.lidar';
const r3 = (x: number) => Math.round(x * 1000) / 1000;
const norm180 = (deg: number) => ((deg % 360) + 540) % 360 - 180;

interface Held {
  editable: Editable; prior: PreviewState | null;
  start: V3; startTurn: number; x: number; z: number; turnDeg: number; height: number;
  /** start.y over the ground at the start: the generator's embedding, kept while moving (0 after □). */
  relative: number | null; ground: number | null; startGround: number | null;
  snapping: boolean; snapAt: { x: number; z: number }; dirty: boolean;
  shot: Promise<Blob | null>;
}

/** The camera's ground-plane basis: forward and right (unit, engine x/z). */
function cameraBasis(camera: THREE.Camera) {
  const d = camera.getWorldDirection(new THREE.Vector3());
  let fx = d.x, fz = d.z, l = Math.hypot(fx, fz);
  if (l < 1e-3) { const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion); fx = up.x; fz = up.z; l = Math.hypot(fx, fz) || 1; }
  fx /= l; fz /= l;
  return { fx, fz, rx: -fz, rz: fx };
}

/** A transform record of a ground point and yaw (deg) in every frame. */
function transform(p: V3, yawDeg: number | null): Transform {
  const yaw = yawDeg === null ? null : Math.round(norm180(yawDeg) * 100) / 100;
  // Compass heading of the object's local +X (three.js yaw 0 points east): clockwise from grid north.
  const heading = yaw === null ? null : Math.round(((90 - yaw) % 360 + 360) % 360 * 100) / 100;
  return { ...framesOf(p.x, p.y, p.z), yaw_deg: yaw, heading_deg: heading };
}

export class ObjectEditor {
  readonly enabled: boolean;
  readonly localOnly = editsLocalOnly();
  readonly machine = new EditorMachine();
  readonly catalog: EditableCatalog;
  readonly previews: Previews;
  readonly cycle = new CandidateCycle<Editable>();
  session: EditSession | null = null;
  private hud: EditorHud | null = null;
  private shots: FrameShots;
  private highlight: THREE.LineSegments | null = null;
  private held: Held | null = null;
  private last = performance.now();
  private occluder: number | null = null;
  private occluding = false;
  private lastCommitted: string | null = null;
  private pins = new Map<string, { at: number; pin: Record<string, unknown> | null }>();
  private flushAt = 0;
  private store: KeyValue;
  private transport: SessionTransport;
  private raycast: Raycast;
  private input: Input | null = null;
  private unsavedBadge: HTMLElement | null = null;
  /** The close dialog (id + summary), and a closed session whose session.json or summary.txt is still unsent. */
  private dialog: SessionDialog | null = null;
  private tail: EditSession | null = null;
  /** The point cloud's visibility in the mode (kept across exits and reloads); outside the mode it is hidden unless
   *  the page asked for it (`?pointcloud=1` or a colour mode). Opacity and filters stay with the overlay. */
  private readonly lidarPinned = (() => { const v = new URLSearchParams(location.search).get('pointcloud'); return v !== null && v !== '0'; })();
  private lidarWanted = this.lidarPinned || (() => { try { return localStorage.getItem(LIDAR_KEY) === 'on'; } catch { return false; } })();
  private unsentSince: number | null = null;
  private toast: { text: string; until: number } | null = null;
  /** Props are indexed when the editor needs them; a model loading under open prop previews is indexed at once. */
  private onLoad = (e: { scene: THREE.Object3D }) => {
    const props = [...this.previews.states.values()].some(s => s.category === 'prop');
    this.previews.attach(this.catalog.addModel(e.scene, props));
  };
  private onDispose = (e: { scene: THREE.Object3D }) => { this.previews.detach(e.scene); this.catalog.removeModel(e.scene); };

  constructor(private hosts: EditorHosts, enabled = editorAllowed()) {
    this.enabled = enabled;
    this.store = hosts.store ?? defaultStore();
    this.transport = hosts.transport ?? (this.localOnly ? localTransport() : httpTransport());
    this.catalog = new EditableCatalog(() => hosts.vegetation(), () => hosts.lights, 150, () => hosts.signals?.() ?? null);
    this.previews = new Previews({ vegetation: () => hosts.vegetation(), lights: hosts.lights, signals: () => hosts.signals?.() ?? null });
    this.shots = new FrameShots(() => hosts.renderer.domElement);
    this.raycast = async (origin, dir, far) => {
      const hit = await hosts.physics.raycast(origin, dir, far, 'solid');
      return hit ? { toi: hit.toi, point: hit.point, material: hit.material } : null;
    };
    if (!enabled) return;
    hosts.tiles.modelEvents.addEventListener('load-model', this.onLoad);
    hosts.tiles.modelEvents.addEventListener('dispose-model', this.onDispose);
    for (const model of hosts.tiles.models) this.catalog.addModel(model);
    // A reload continues the open session: its previews come back, and so does the mode if it was on.
    this.session = EditSession.restore(this.store, this.transport);
    if (this.session) {
      this.restorePreviews();
      // A close that had not finished saving (or an ended session with its session.json unsent) carries on.
      if (!this.session.closing && this.session.active) this.enter(performance.now());
    }
  }

  get active() { return this.machine.active; }
  get holding() { return this.held !== null; }

  /** Per frame right after Input.update: the editor's layer, its actions and the gameplay actions it takes. */
  handle(input: Input, playing: boolean, now: number) {
    if (!this.enabled) return;
    this.input = input;
    if (this.dialog?.handle(input)) {
      // The dialog is modal: its buttons take ✕ ○ △ (Enter, Esc, F); the editor toggle waits until it closes.
      input.consume('editToggle');
      input.layer = 'game';
      return;
    }
    const dt = Math.min(0.1, Math.max(0, (now - this.last) / 1000));
    this.last = now;
    input.layer = this.machine.active && playing ? 'editor' : 'game';
    const a = input.actions;
    const frame: EditorFrame = playing ? {
      dt, toggle: input.consume('editToggle') || a.editToggle > 0.5, pick: input.consume('editPick'), back: input.consume('editBack'),
      prev: input.consume('editPrev'), next: input.consume('editNext'), delete: a.editDelete > 0.5, ground: input.consume('editGround'),
      turnLeft: a.editTurnLeft, turnRight: a.editTurnRight, up: input.consume('editUp'), down: input.consume('editDown'),
      left: input.consume('editLeft'), right: input.consume('editRight'), lidar: a.editLidar > 0.5, moveX: a.moveX, moveY: a.moveY,
    } : idleFrame(dt);
    const fast = a.sprint > 0.5;
    const commands = this.machine.step(frame);
    const height = playing ? this.machine.height(a.editUp > 0.5, a.editDown > 0.5, dt) : null;
    if (height) commands.push(height);
    for (const command of commands) this.execute(command, now, fast);
    input.layer = this.machine.active && playing ? 'editor' : 'game';
    if (this.held && playing) {
      // The held object takes the left stick, the triggers and □: the camera only looks around.
      a.moveX = a.moveY = a.flyUp = a.flyDown = a.throttle = a.brake = a.steer = a.sprint = 0;
      input.consume('jump');
    }
  }

  private execute(c: Command, now: number, fast: boolean) {
    switch (c.type) {
      case 'enter': this.enter(now); break;
      case 'exit': this.exit(); break;
      case 'closeSession': this.closeSession(now); break;
      case 'cycle': this.cycle.cycle(c.step); break;
      case 'pick': this.pick(now); break;
      case 'drop': this.drop(now); break;
      case 'cancel': this.cancel(); break;
      case 'undo': this.undo(now); break;
      case 'delete': this.delete(now); break;
      case 'reason': this.tag(c.index, now); break;
      case 'lidar': this.lidar(c, now); break;
      case 'move': if (this.held) this.move(c.right, c.forward, fast); break;
      case 'turn': if (this.held) { this.held.turnDeg += c.radians * DEG; this.held.dirty = true; } break;
      case 'height': if (this.held) { this.held.height += c.metres; this.held.dirty = true; } break;
      case 'ground': if (this.held) { this.held.relative = 0; this.held.height = 0; this.held.dirty = true; this.snap(true); } break;
    }
  }

  private enter(now: number) {
    if (!this.machine.active) this.machine.active = true;
    if (this.session?.closing && !this.session.meta.ended) { this.session.closing = false; this.notify('Close cancelled: the session goes on', now, 2500); }
    this.hud ??= new EditorHud();
    this.hud.show(true);
    if (!this.session) this.session = EditSession.start(this.store, this.transport, {
      build: { ...buildStatus(), url: location.href }, tiles: { manifest: this.hosts.coarse?.manifestUrl('TILE') ?? null, generations: {} },
      device: { userAgent: navigator.userAgent, ...this.hosts.device?.() }, player: this.hosts.player?.() ?? null,
    });
    this.session.setActive(true);
    this.hud.notify(this.localOnly ? `Editor on · session ${this.session.id} · local only: edits stay in this browser, nothing is uploaded`
      : `Editor on · session ${this.session.id}`, now, this.localOnly ? 4500 : 2200);
    this.catalog.refresh(this.hosts.camera.position.x, this.hosts.camera.position.z, now, true);
  }

  private exit() {
    if (this.held) this.cancel();
    this.machine.reset();
    this.hud?.show(false);
    if (this.highlight) this.highlight.visible = false;
    this.session?.setActive(false);
    void this.session?.flush();
  }

  /** Close only once every row is saved: until then the previews stay and the badge shows what is retrying. */
  private closeSession(now: number) {
    if (this.held) this.cancel();
    const session = this.session;
    if (!session) return;
    session.setActive(false);
    session.closing = true;
    void session.flush();
    this.finishClose(now);
  }

  private finishClose(now: number) {
    const session = this.session;
    if (!session || !session.closing || !session.closable) return;
    const edits = session.meta.counts.effective, fresh = !session.meta.ended;
    if (fresh) { session.meta.player = this.hosts.player?.() ?? session.meta.player ?? null; session.end(); }
    // The summary (summary.txt) goes out after session.json; a reload of a close still saving keeps the first one.
    if (session.summary === null)
      session.setSummary(summarizeSession(session.meta, session.rows, { host: location.host, player: session.meta.player?.nick, localOnly: this.localOnly }));
    void session.flush();
    this.tail = session;
    this.previews.clear();
    this.session = null;
    this.notify(this.localOnly ? `Session closed: ${edits} edit${edits === 1 ? '' : 's'}, local only · copy or download the JSON to keep them`
      : `Session saved: ${edits} edit${edits === 1 ? '' : 's'} · previews off until the generator incorporates them`, now, 7000);
    if (!fresh) return;
    // Local only: the dialog's JSON is the only copy, so the last closed session also stays in this browser.
    const json = this.localOnly ? sessionExport(session.meta, session.rows, session.summary) : null;
    if (json) try { localStorage.setItem(LAST_KEY, json); } catch { /* full: the dialog still has it */ }
    (this.dialog ??= new SessionDialog()).show(session.id, session.summary!, session.latestByKey().size, json);
  }

  /** The last session closed on a local-only site, as its export JSON (console: __game.editor.lastClosed()). */
  lastClosed(): string | null { try { return localStorage.getItem(LAST_KEY); } catch { return null; } }

  /** Whether the close dialog is up (the game treats it like a menu: no gameplay buttons, no look). */
  get modal() { return !!this.dialog?.open; }

  /** A message that shows with or without the mode's HUD (the badge). */
  private notify(text: string, now: number, ms: number) { this.toast = { text, until: now + ms }; }

  /** The effective editable: the catalog's original placement moved to its open preview. Deleted ones are null. */
  private effective(e: Editable): Editable | null {
    const state = this.previews.states.get(e.key);
    if (!state) return e;
    if (state.deleted) return null;
    const m = e.anchors ? previewMatrix(state) : null, v = new THREE.Vector3();
    return { ...e, base: { ...state.after }, yawDeg: e.yawDeg === null ? null : e.yawDeg + state.turnDeg,
      anchors: e.anchors?.map(a => { v.set(a.x, a.y, a.z).applyMatrix4(m!); return { x: v.x, y: v.y, z: v.z }; }) };
  }

  private pick(now: number) {
    const target = this.cycle.current?.item;
    if (!target) { this.hud?.notify('Nothing under the crosshair', now, 900); return; }
    const original = this.catalog.find(target.key) ?? target;
    const prior = this.previews.states.get(target.key) ?? null;
    this.held = { editable: original, prior, start: { ...target.base }, startTurn: prior?.turnDeg ?? 0, x: target.base.x, z: target.base.z,
      turnDeg: prior?.turnDeg ?? 0, height: 0, relative: null, ground: null, startGround: null, snapping: false,
      snapAt: { x: target.base.x, z: target.base.z }, dirty: true, shot: this.shots.capture() };
    this.machine.setHolding(true);
    void groundUnder(this.raycast, target.base.x, target.base.y, target.base.z).then(hit => {
      const held = this.held;
      if (!held || held.editable.key !== original.key || !hit) return;
      held.startGround = hit.point.y;
      held.relative ??= held.start.y - hit.point.y;
      if (held.ground === null) held.ground = hit.point.y;
    });
  }

  private move(right: number, forward: number, fast: boolean) {
    const held = this.held!, b = cameraBasis(this.hosts.camera), cam = this.hosts.camera.position;
    const distance = Math.hypot(held.x - cam.x, held.z - cam.z);
    const speed = 1.5 * Math.min(6, Math.max(0.5, distance / 8)) * (fast ? 3 : 1);
    held.x += (b.fx * forward + b.rx * right) * speed;
    held.z += (b.fz * forward + b.rz * right) * speed;
    held.dirty = true;
    this.snap(false);
  }

  /** Ground under the held object's new position (one ray in flight; the latest position wins). */
  private snap(force: boolean) {
    const held = this.held!;
    if (held.snapping || (!force && Math.hypot(held.x - held.snapAt.x, held.z - held.snapAt.z) < 0.01)) return;
    held.snapping = true;
    const at = { x: held.x, z: held.z };
    const y = (held.ground ?? held.start.y) + 0.5;
    void groundUnder(this.raycast, at.x, y, at.z, 2.5, 15).then(hit => {
      if (this.held !== held) return;
      held.snapping = false; held.snapAt = at;
      if (hit) { held.ground = hit.point.y; held.dirty = true; }
      if (Math.hypot(held.x - at.x, held.z - at.z) >= 0.01) this.snap(false);
    });
  }

  private heldState(held: Held): PreviewState {
    const e = held.editable;
    const moved = Math.hypot(held.x - held.start.x, held.z - held.start.z) >= 0.01;
    const base = moved && held.ground !== null ? held.ground + (held.relative ?? 0)
      : held.relative === 0 && held.ground !== null ? held.ground : held.start.y;
    return { key: e.key, category: e.category, objectId: e.objectId, tile: e.tile, original: { ...e.base },
      after: { x: held.x, y: base + held.height, z: held.z }, turnDeg: held.turnDeg, deleted: false, originalYawDeg: e.yawDeg };
  }

  private cancel() {
    const held = this.held;
    if (!held) return;
    this.held = null; this.machine.setHolding(false);
    this.previews.set(held.editable.key, held.prior, this.catalog.find(held.editable.key) ?? undefined);
  }

  private drop(now: number) {
    const held = this.held;
    if (!held) return;
    const state = this.heldState(held);
    const before = held.prior ?? null;
    const was = before?.after ?? held.editable.base, wasTurn = before?.turnDeg ?? 0;
    const unchanged = Math.hypot(state.after.x - was.x, state.after.z - was.z) < 0.01 && Math.abs(state.after.y - was.y) < 0.005
      && Math.abs(state.turnDeg - wasTurn) < 0.05;
    this.held = null; this.machine.setHolding(false);
    if (unchanged) { this.previews.set(state.key, held.prior, this.catalog.find(state.key) ?? undefined); this.hud?.notify('No change', now, 800); return; }
    this.previews.set(state.key, state, this.catalog.find(state.key) ?? undefined);
    this.commit(held.editable, before, state, held.shot, now);
  }

  private delete(now: number) {
    const held = this.held;
    const target = held ? held.editable : this.cycle.current ? this.catalog.find(this.cycle.current.item.key) ?? this.cycle.current.item : null;
    if (!target) { this.hud?.notify('Nothing to delete', now, 900); return; }
    const prior = held ? held.prior : this.previews.states.get(target.key) ?? null;
    const shot = held ? held.shot : this.shots.capture();
    if (held) { this.held = null; this.machine.setHolding(false); }
    const at = prior?.after ?? target.base;
    const state: PreviewState = { key: target.key, category: target.category, objectId: target.objectId, tile: target.tile,
      original: { ...target.base }, after: { ...at }, turnDeg: prior?.turnDeg ?? 0, deleted: true, originalYawDeg: target.yawDeg };
    // The before shot needs the object in its frame: hide it after this frame.
    setTimeout(() => this.previews.set(target.key, state, this.catalog.find(target.key) ?? undefined), 0);
    this.commit(target, prior, state, shot, now);
  }

  private commit(e: Editable, prior: PreviewState | null, state: PreviewState, beforeShot: Promise<Blob | null>, now: number) {
    const session = this.session!;
    const seq = session.nextSeq, id = session.rowId(seq);
    const yaw0 = e.yawDeg;
    const original = transform(e.base, yaw0);
    const before = prior ? transform(prior.after, yaw0 === null ? null : yaw0 + prior.turnDeg) : original;
    const after = state.deleted ? null : transform(state.after, yaw0 === null ? null : yaw0 + state.turnDeg);
    const dx = state.after.x - e.base.x, dz = state.after.z - e.base.z, dy = state.after.y - e.base.y;
    const prevAfter = prior?.after ?? e.base;
    const translated = Math.hypot(state.after.x - prevAfter.x, state.after.z - prevAfter.z) >= 0.01;
    const lifted = Math.abs(state.after.y - prevAfter.y) >= 0.005;
    const rotated = Math.abs(state.turnDeg - (prior?.turnDeg ?? 0)) >= 0.05;
    const cam = this.hosts.camera, dir = cam.getWorldDirection(new THREE.Vector3());
    const record: EditRecord = {
      format: EDIT_FORMAT, version: EDIT_VERSION, id, seq, session: session.id, at: new Date().toISOString(),
      action: state.deleted ? 'delete' : translated || lifted ? 'move' : 'rotate',
      changes: { translated, rotated, height: lifted },
      object: { key: e.key, category: e.category, type: e.type, tile: e.tile, object_id: e.objectId,
        provenance: { ...provenanceOf(e, this.hosts.tiles.models), yaw_source: e.yawSource } },
      original, before, after,
      delta: state.deleted ? null : { dx: r3(dx), dz: r3(dz), dy: r3(dy), horizontal_m: r3(Math.hypot(dx, dz)), yaw_deg: Math.round(norm180(state.turnDeg) * 100) / 100 },
      preview: state.deleted ? new THREE.Matrix4().toArray() : previewMatrix(state).toArray().map(r3),
      camera: { ...framesOf(cam.position.x, cam.position.y, cam.position.z),
        heading_deg: Math.round(((Math.atan2(dir.x, -dir.z) * DEG) + 360) % 360 * 10) / 10,
        pitch_deg: Math.round(Math.atan2(dir.y, Math.hypot(dir.x, dir.z)) * DEG * 10) / 10, fov_deg: cam.fov },
      tile_pin: null, context: null, reason: null, shots: { before: null, after: null },
    };
    session.commit(record);
    this.lastCommitted = id;
    this.machine.committed();
    this.hud?.notify(`${record.action} ${e.type} · edit ${seq}`, now, 1200);
    // Context, generation pin and screenshots complete the row asynchronously; the row is resent whole.
    const afterShot = new Promise<Blob | null>(resolve => setTimeout(() => void this.shots.capture().then(resolve), 30));
    void (async () => {
      const [pin, contextBefore, contextAfter] = await Promise.all([
        this.tilePin(e.tile, now),
        this.context(e.base, e.key),
        state.deleted ? Promise.resolve(null) : this.context(state.after, e.key),
      ]);
      const [b, a] = await Promise.all([beforeShot, afterShot]);
      const shots = { before: b ? `shots/${id}-before.jpg` : null, after: a ? `shots/${id}-after.jpg` : null };
      if (b) void session.putShot(shots.before!, b);
      if (a) void session.putShot(shots.after!, a);
      session.amend(id, { tile_pin: pin, context: { before: contextBefore, after: contextAfter }, shots });
      void session.flush();
    })();
  }

  private context(at: V3, self: string) {
    return measureContext({ raycast: this.raycast, roads: () => this.hosts.coarse?.roadGraphs() ?? [],
      props: (x, z, radius) => this.catalog.near(x, z, radius).flatMap(e => { const f = this.effective(e); return f ? [f] : []; }),
      pointCloud: pointCloudOverlay }, at, self).catch(error => ({ error: String(error) }));
  }

  /** The generation of the tile as served now (its manifest: revision, generation, migrations, visual hash). */
  private async tilePin(tile: string, now: number): Promise<Record<string, unknown> | null> {
    const cached = this.pins.get(tile);
    if (cached && now - cached.at < 60_000) return cached.pin;
    let pin: Record<string, unknown> | null = null;
    try {
      const url = this.hosts.coarse?.manifestUrl(tile);
      if (url) {
        const m = await (await fetch(url, { cache: 'no-store' })).json() as Record<string, unknown>;
        const visual = (m.visual as Record<string, { sha256?: string; url?: string }> | undefined)?.[tile];
        pin = { tile, revision: m.revision ?? null, generation: m.generation ?? null, requested_revision: m.requested_revision ?? null,
          migrations: m.migrations ?? [], migration_base: m.migration_base ?? null, pass_kind: m.pass_kind ?? null,
          source_kind: m.source_kind ?? null, visual_sha256: visual?.sha256 ?? null, visual_url: visual?.url ?? null };
        const tiles = this.session?.meta.tiles as { generations?: Record<string, string[]> } | undefined;
        if (tiles && typeof pin.generation === 'string') {
          const list = (tiles.generations ??= {})[pin.generation] ??= [];
          if (!list.includes(tile)) list.push(tile);
        }
      }
    } catch { pin = null; }
    this.pins.set(tile, { at: now, pin });
    return pin;
  }

  private undo(now: number) {
    const session = this.session;
    const undone = session?.undo();
    if (!session || !undone) { this.hud?.notify('Nothing to undo', now, 900); return; }
    const latest = session.latestByKey().get(undone.object.key) ?? null;
    this.previews.set(undone.object.key, latest ? stateOf(latest) : null, this.catalog.find(undone.object.key) ?? undefined);
    this.machine.reasonLeft = 0;
    this.hud?.notify(`Undo ${undone.action} ${undone.object.type}`, now, 1200);
    void session.flush();
  }

  private tag(index: number, now: number) {
    if (!this.session || !this.lastCommitted) return;
    this.session.tag(this.lastCommitted, REASONS[index]);
    this.hud?.notify(`Reason: ${REASONS[index].replace(/_/g, ' ')}`, now, 1000);
    void this.session.flush();
  }

  private lidar(c: Extract<Command, { type: 'lidar' }>, now: number) {
    const cloud = pointCloudOverlay();
    if (!cloud) { this.hud?.notify('No point cloud loaded', now, 1000); return; }
    if (c.toggle) this.setLidarWanted(!cloud.isVisible());
    if (c.opacity) { this.setLidarWanted(true); cloud.setOpacity(Math.min(1, Math.max(0.05, cloud.getOpacity() + c.opacity))); }
    if (c.size) cloud.setPointSize(Math.min(12, Math.max(1, cloud.getPointSize() + c.size)));
    if (c.colour && cloud.setColorMode) {
      const i = COLOR_MODES.indexOf((cloud.getColorMode?.() ?? 'rgb') as typeof COLOR_MODES[number]);
      cloud.setColorMode(COLOR_MODES[((Math.max(0, i) + c.colour) % COLOR_MODES.length + COLOR_MODES.length) % COLOR_MODES.length]);
    }
    const mode = cloud.getColorMode?.();
    this.hud?.notify(`LiDAR ${cloud.isVisible() ? 'on' : 'off'} · ${Math.round(cloud.getOpacity() * 100)} % · ${cloud.getPointSize()} px${mode ? ` · ${mode}` : ''}`, now, 900);
  }

  private setLidarWanted(on: boolean) {
    this.lidarWanted = on;
    try { localStorage.setItem(LIDAR_KEY, on ? 'on' : 'off'); } catch { /* session only */ }
    this.syncLidar();
  }

  /** In the mode the overlay shows what was last chosen there; outside it, it hides (unless the URL pinned it). */
  private syncLidar() {
    const cloud = pointCloudOverlay(), want = lidarVisibility(this.machine.active, this.lidarWanted, this.lidarPinned);
    if (cloud && want !== null && cloud.isVisible() !== want) cloud.setVisible(want);
  }

  private restorePreviews() {
    for (const [key, edit] of this.session!.latestByKey()) this.previews.set(key, stateOf(edit), this.catalog.find(key) ?? undefined);
  }

  /** Per frame after the camera moved, before rendering: picking, the held preview, highlight and HUD. */
  update(now: number) {
    if (!this.enabled) return;
    this.previews.update();
    this.syncLidar();
    if (now >= this.flushAt) {
      this.flushAt = now + 2000;
      if (this.session?.pending) void this.session.flush();
      if (this.tail?.pending) void this.tail.flush(); else this.tail = null;
    }
    this.finishClose(now);
    this.showUnsaved(now);
    if (!this.machine.active) return;
    const camera = this.hosts.camera;
    camera.updateMatrixWorld();
    this.catalog.refresh(camera.position.x, camera.position.z, now);
    const origin = camera.position, dir = camera.getWorldDirection(new THREE.Vector3());
    if (this.held) {
      if (this.held.dirty) { this.held.dirty = false; const state = this.heldState(this.held); this.previews.set(state.key, state, this.catalog.find(state.key) ?? undefined); }
    } else {
      const items: Editable[] = [];
      for (const e of this.catalog.near(origin.x, origin.z)) { const f = this.effective(e); if (f) items.push(f); }
      this.cycle.update(pickCandidates(origin, dir, items, this.occluder));
      this.probeOccluder(origin, dir);
    }
    this.drawHighlight();
    this.renderHud(now);
  }

  /** The badge (outside the mode's HUD): a close still saving ("N edits not saved, retrying"), rows waiting more
   *  than 3 s, or a notice such as the close result. */
  private showUnsaved(now: number) {
    const s = this.session, unsent = s?.unsent ?? 0;
    if (!unsent) this.unsentSince = null; else this.unsentSince ??= now;
    const error = s?.error ? ` · ${s.error}` : '';
    let text = '', warn = false;
    if (s?.closing && unsent) { text = `Closing: ${unsent} edit row${unsent === 1 ? '' : 's'} not saved, retrying${error} · previews kept`; warn = true; }
    else if (unsent && this.unsentSince !== null && now - this.unsentSince > 3000) { text = `${unsent} edit row${unsent === 1 ? '' : 's'} unsaved, retrying${error}`; warn = true; }
    else if (this.toast && now < this.toast.until) text = this.toast.text;
    if (!text && !this.unsavedBadge) return;
    if (!this.unsavedBadge) {
      this.unsavedBadge = Object.assign(document.createElement('div'), { id: 'object-editor-unsaved' });
      Object.assign(this.unsavedBadge.style, { position: 'fixed', left: '50%', top: '14%', transform: 'translateX(-50%)', zIndex: '41',
        pointerEvents: 'none', color: '#fff', font: '600 13px/1.35 system-ui,sans-serif', padding: '6px 12px', borderRadius: '5px', maxWidth: '80vw' });
      document.body.append(this.unsavedBadge);
    }
    if (this.unsavedBadge.textContent !== text) this.unsavedBadge.textContent = text;
    this.unsavedBadge.style.background = warn ? 'rgba(200,90,20,.92)' : 'rgba(0,0,0,.72)';
    this.unsavedBadge.hidden = !text;
  }

  private probeOccluder(origin: THREE.Vector3, dir: THREE.Vector3) {
    if (this.occluding) return;
    this.occluding = true;
    void this.hosts.physics.raycast({ x: origin.x, y: origin.y, z: origin.z }, { x: dir.x, y: dir.y, z: dir.z }, 150, 'solid').then(hit => {
      // Thin standing things are editables themselves, not walls: look past them.
      this.occluder = !hit ? null : STANDING.has(MATERIALS[hit.material]?.name ?? '') ? hit.toi + 3 : hit.toi;
    }, () => { this.occluder = null; }).finally(() => { this.occluding = false; });
  }

  private drawHighlight() {
    const target = this.held ? (() => { const s = this.heldState(this.held!); return { ...this.held!.editable, base: s.after,
      yawDeg: this.held!.editable.yawDeg === null ? null : this.held!.editable.yawDeg + s.turnDeg }; })() : this.cycle.current?.item ?? null;
    if (!this.highlight) {
      const geometry = new THREE.BufferGeometry();
      const corners = [[-1, 0, -1], [1, 0, -1], [1, 0, 1], [-1, 0, 1], [-1, 1, -1], [1, 1, -1], [1, 1, 1], [-1, 1, 1]];
      const edges = [0, 1, 1, 2, 2, 3, 3, 0, 4, 5, 5, 6, 6, 7, 7, 4, 0, 4, 1, 5, 2, 6, 3, 7];
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(edges.flatMap(i => corners[i]), 3));
      const material = new THREE.LineBasicNodeMaterial({ color: 0x5ef0ff, depthTest: false, transparent: true });
      this.highlight = new THREE.LineSegments(geometry, material);
      this.highlight.name = 'object-editor-highlight';
      this.highlight.renderOrder = 10_000; this.highlight.frustumCulled = false;
      this.hosts.scene.add(this.highlight);
    }
    const h = this.highlight;
    h.visible = !!target;
    if (!target) return;
    (h.material as THREE.LineBasicNodeMaterial).color.setHex(this.held ? 0xffb347 : 0x5ef0ff);
    h.position.set(target.base.x, target.base.y, target.base.z);
    h.rotation.set(0, (target.yawDeg ?? 0) / DEG, 0);
    h.scale.set(target.halfLength ?? target.radius, target.height, target.radius);
    h.updateMatrix(); h.updateMatrixWorld(true);
  }

  private renderHud(now: number) {
    const hud = this.hud!, input = this.input;
    const current = this.cycle.current, list = this.cycle.list;
    let label = '';
    if (this.held) {
      const s = this.heldState(this.held), e = this.held.editable;
      const moved = Math.hypot(s.after.x - e.base.x, s.after.z - e.base.z);
      label = `${e.type} · moved ${moved.toFixed(2)} m · turned ${norm180(s.turnDeg).toFixed(1)}° · ${s.after.y - e.base.y >= 0 ? '+' : ''}${(s.after.y - e.base.y).toFixed(2)} m`;
    } else if (current) {
      const e = current.item;
      label = `${e.type} · ${e.objectId.length > 42 ? e.objectId.slice(0, 40) + '…' : e.objectId} · ${current.t.toFixed(1)} m${list.length > 1 ? ` · ${this.cycle.index + 1}/${list.length}` : ''}`;
    }
    const g = (action: Action) => input?.prompt(action) ?? action;
    const cloud = pointCloudOverlay();
    const lidarState = !cloud ? '' : cloud.isVisible()
      ? `on ${Math.round(cloud.getOpacity() * 100)} %${cloud.getColorMode ? ` ${cloud.getColorMode()}` : ''}` : 'off';
    // While Create is held the D-pad and L1/R1 adjust the cloud: say so where the label is.
    if (this.machine.lidarHeld) label = !cloud ? 'No point cloud loaded'
      : `LiDAR ${lidarState} · ${g('editUp')}${g('editDown')} opacity · ${g('editLeft')}${g('editRight')} size ${cloud.getPointSize()} px`
        + `${cloud.setColorMode ? ` · ${g('editPrev')}/${g('editNext')} colour` : ''} · just tap: ${cloud.isVisible() ? 'hide' : 'show'}`;
    const hints: [string, string][] = this.held ? [
      ['Ⓛ', 'move'], [`${g('editTurnLeft')}/${g('editTurnRight')}`, 'turn'], [`${g('editPrev')}/${g('editNext')}`, '15°'],
      [`${g('editUp')}${g('editDown')}`, 'height'], [`${g('editLeft')}${g('editRight')}`, '1°'], [g('editGround'), 'ground'],
      [g('editPick'), 'drop'], [g('editBack'), 'cancel'], [`hold ${g('editDelete')}`, 'delete'],
    ] : [
      [g('editPick'), 'pick'], [`${g('editPrev')}/${g('editNext')}`, 'cycle'], [`hold ${g('editDelete')}`, 'delete'], [g('editBack'), 'undo'],
      [g('editLidar'), lidarState ? `LiDAR ${lidarState} (hold for settings)` : 'LiDAR: none loaded'], [g('editToggle'), 'exit · hold: close session'],
    ];
    const s = this.session;
    const status = [s ? s.id : '', s?.error ?? '', cloud ? `LiDAR ${lidarState}` : 'no LiDAR overlay'].filter(Boolean).join(' · ');
    hud.render({ mode: this.held ? 'hold' : 'aim', label, hover: !!current, deleting: this.machine.deleteHeld > 0 ? this.machine.deleteHeld / EDITOR.deleteHold : null,
      reasonLeft: this.held ? 0 : this.machine.reasonLeft, reasonGlyphs: [g('editUp'), g('editRight'), g('editDown'), g('editLeft')], hints, status,
      counts: s ? { saved: s.saved, pending: s.unsent } : null, localOnly: this.localOnly }, now);
  }


  dispose() {
    if (!this.enabled) return;
    this.hosts.tiles.modelEvents.removeEventListener('load-model', this.onLoad);
    this.hosts.tiles.modelEvents.removeEventListener('dispose-model', this.onDispose);
    if (this.held) this.cancel();
    void this.session?.flush();
    this.shots.dispose();
    this.hud?.dispose();
    this.unsavedBadge?.remove();
    this.dialog?.dispose();
    if (this.highlight) { this.highlight.removeFromParent(); this.highlight.geometry.dispose(); (this.highlight.material as THREE.Material).dispose(); }
    // Previews stay with the world until the session closes; a hot swap restores them from the store.
    this.previews.clear();
  }
}

/** The preview state of a committed edit. */
export function stateOf(edit: EditRecord): PreviewState {
  const o = edit.original.engine, a = (edit.after ?? edit.before).engine;
  return { key: edit.object.key, category: edit.object.category as PreviewState['category'], objectId: edit.object.object_id,
    tile: edit.object.tile, original: { ...o }, after: { ...a }, turnDeg: edit.delta?.yaw_deg ?? 0,
    deleted: edit.action === 'delete', originalYawDeg: edit.original.yaw_deg };
}
