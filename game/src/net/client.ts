import type { VehicleKind } from '../physics/constants';
import { multiplayerUrl, scoresUrl } from './config';

export interface NetPose {
  t: number; E: number; N: number; H: number; yaw: number;
  vE: number; vN: number; vH: number; mode: 'foot' | VehicleKind; vehicle: string | null; carrying: boolean;
}
export interface NetPlayer { id: string; nick: string; spawn: { E: number; N: number; yaw: number }; pose: NetPose | null }
export interface Score { nick: string; money: number; deliveries: number; rank: number; online: boolean }
export interface Hour { id: string; start: string; end: string; tz: string }
interface Delivery { orderId: string; addressId: string; payout: number; tip: number; routeMeters: number }
interface MultiplayerState {
  connected: boolean; you: { id: string; nick: string; spawn: NetPlayer['spawn'] } | null;
  hour: Hour | null; players: NetPlayer[]; scores: Score[]; brokenPoles: string[]; countdown: number | null; error: string | null;
}

const SESSION_KEY = 'turku3d.mp.session.v1';
const RATE_MS = 55; // under the server's 20 total messages/s token rate, including deliveries
const STATIONARY_MS = 1000;

/** True when another pose is useful and the combined message budget permits it. */
export function shouldSendPose(now: number, previous: NetPose | null, lastSent: number, pose: NetPose): boolean {
  if (now - lastSent < RATE_MS) return false;
  if (!previous) return true;
  const moved = Math.hypot(pose.E - previous.E, pose.N - previous.N, pose.H - previous.H) > 0.005;
  const turned = Math.abs(pose.yaw - previous.yaw) > 0.005;
  const changed = moved || turned || pose.mode !== previous.mode || pose.vehicle !== previous.vehicle || pose.carrying !== previous.carrying
    || Math.hypot(pose.vE - previous.vE, pose.vN - previous.vN, pose.vH - previous.vH) > 0.05;
  return changed || now - lastSent >= STATIONARY_MS;
}

export class MultiplayerClient extends EventTarget {
  readonly url: string;
  readonly httpUrl: string;
  readonly state: MultiplayerState = { connected: false, you: null, hour: null, players: [], scores: [], brokenPoles: [], countdown: null, error: null };
  private ws: WebSocket | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private attempts = 0;
  private closed = false;
  private lastPose: NetPose | null = null;
  private lastSent = -Infinity;
  private token: string | null = null;
  private pendingDeliveries: Delivery[] = [];
  private deliveryTimer: ReturnType<typeof setTimeout> | null = null;
  private claimed = new Set<string>();

  constructor(url = multiplayerUrl(), private readonly storage: Storage | null = (() => {
    try { return window.sessionStorage; } catch { return null; }
  })()) {
    super();
    this.url = url;
    this.httpUrl = scoresUrl(url);
    try { this.token = storage?.getItem(SESSION_KEY) ?? null; } catch { /* storage may be disabled */ }
  }

  connect() {
    if (this.closed || this.ws || this.retry) return;
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.addEventListener('open', () => {
      ws.send(JSON.stringify({ v: 1, type: 'hello', ...(this.token ? { session: this.token } : {}) }));
    });
    ws.addEventListener('message', event => this.receive(event.data));
    ws.addEventListener('close', () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.state.connected = false;
      this.changed();
      if (!this.closed) {
        const delay = Math.min(15000, 500 * 2 ** Math.min(this.attempts++, 5));
        this.retry = setTimeout(() => { this.retry = null; this.connect(); }, delay);
      }
    });
    ws.addEventListener('error', () => { this.state.error = 'Connection unavailable'; this.changed(); });
  }

  disconnect() {
    this.closed = true;
    if (this.retry) clearTimeout(this.retry);
    if (this.deliveryTimer) clearTimeout(this.deliveryTimer);
    this.retry = null;
    this.deliveryTimer = null;
    this.ws?.close();
    this.ws = null;
    this.state.connected = false;
  }

  sendPose(pose: NetPose) {
    const now = performance.now();
    if (!this.state.connected || !shouldSendPose(now, this.lastPose, this.lastSent, pose)) return;
    this.send({ type: 'pose', ...pose });
    this.lastPose = pose;
    this.lastSent = now;
  }

  sendDelivery(delivery: Delivery) {
    if (this.claimed.has(delivery.orderId)) return;
    this.claimed.add(delivery.orderId);
    this.pendingDeliveries.push(delivery);
    this.flushDelivery();
  }

  breakPole(poleId: string) {
    if (!this.state.connected || !this.state.you) return;
    this.send({ type: 'world', action: 'break', poleId, by: this.state.you.id, t: performance.now() });
    this.send({ type: 'fine', reason: 'pole', poleId, amount: 50 });
  }

  private flushDelivery() {
    if (!this.state.connected || !this.pendingDeliveries.length || this.deliveryTimer) return;
    const delay = Math.max(0, RATE_MS - (performance.now() - this.lastSent));
    this.deliveryTimer = setTimeout(() => {
      this.deliveryTimer = null;
      if (!this.state.connected) return;
      const delivery = this.pendingDeliveries.shift()!;
      this.send({ type: 'delivery', ...delivery });
      this.lastSent = performance.now();
      this.flushDelivery();
    }, delay);
  }

  private send(data: object) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ v: 1, ...data }));
  }

  private receive(raw: unknown) {
    if (typeof raw !== 'string') return;
    let msg: Record<string, unknown>;
    try { msg = JSON.parse(raw); } catch { return; }
    if (msg.v !== 1 || typeof msg.type !== 'string') return;
    switch (msg.type) {
      case 'welcome': {
        if (typeof msg.session !== 'string' || !msg.you || !msg.hour) return;
        this.token = msg.session;
        try { this.storage?.setItem(SESSION_KEY, msg.session); } catch { /* storage may be disabled */ }
        this.state.connected = true;
        this.state.you = msg.you as MultiplayerState['you'];
        this.state.hour = msg.hour as Hour;
        this.state.players = msg.players as NetPlayer[];
        this.state.scores = msg.scores as Score[];
        this.state.brokenPoles = (msg.brokenPoles as { poleId: string }[] ?? []).map(p => p.poleId);
        this.state.error = null;
        this.attempts = 0;
        this.lastPose = null; // timestamp epoch may have changed after rejoin
        this.flushDelivery();
        this.changed();
        this.dispatchEvent(new Event('welcome'));
        break;
      }
      case 'players': this.state.players = msg.players as NetPlayer[]; this.changed(); break;
      case 'poses': {
        const byId = new Map(this.state.players.map(p => [p.id, p]));
        for (const pose of msg.poses as (NetPose & { id: string })[]) {
          const player = byId.get(pose.id);
          if (player) player.pose = pose;
        }
        this.dispatchEvent(new CustomEvent('poses', { detail: msg.poses }));
        this.changed();
        break;
      }
      case 'scores': this.state.scores = msg.scores as Score[]; this.changed(); break;
      case 'world': if (msg.action === 'break' && typeof msg.poleId === 'string') {
        if (!this.state.brokenPoles.includes(msg.poleId)) this.state.brokenPoles.push(msg.poleId);
        this.dispatchEvent(new CustomEvent('poleBreak', { detail: msg.poleId }));
      } break;
      case 'hour_end_in': this.state.countdown = msg.seconds as number; this.changed(); break;
      case 'hour_reset': this.state.hour = msg.hour as Hour; this.state.scores = []; this.state.brokenPoles = []; this.state.countdown = null; this.changed(); break;
      case 'error': this.state.error = String(msg.code); this.changed(); break;
    }
  }

  private changed() { this.dispatchEvent(new Event('change')); }
}
