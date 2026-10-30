// GTA-style minimap: streets from the road graph, heading-up rotation, mission markers and the route
// to the active target. A larger north-up view toggles with the map action (M / View).
import type { MissionHudSource, MissionHudState, Router, TargetKind, WorldPoint } from './contract';
import { EMPTY_HUD_STATE } from './contract';
import type { RoadGraph, TravelMode } from './roadGraph';
import { StreetLayer } from './streetLayer';
import type { PlayableArea } from './recovery';
import { RouteTracker, routeView } from './routeTracker';
export { RouteTracker, REROUTE_M } from './routeTracker';
import { COLLISION_TILE } from '../core/origin';

/** Marker colour per target kind, shared with the world zones (zones.ts). */
export const KIND_COLOURS: Record<TargetKind, string> = { pickup: '#f4c430', dropoff: '#4fc3f7', shop: '#66bb6a', market: '#ef6c8c' };
const SIZE = 240;
const SPAN_M = { foot: 200, car: 320, expanded: 650 };

interface MinimapView {
  position: WorldPoint;
  /** Camera compass heading (deg): the minimap turns so this points up. */
  headingDeg: number;
  /** Player or car compass heading (deg) for the centre arrow. */
  facingDeg: number;
  mode: TravelMode;
  /** Within `RECOVERY.edgeWarning` of the edge of the built area: the edge line turns red. */
  nearEdge: boolean;
}

/** World x/z to map pixels relative to the map centre: player at the origin, `rotation` turns the map. */
export function project(p: WorldPoint, centre: WorldPoint, rotation: number, pxPerM: number): [number, number] {
  const dx = (p.x - centre.x) * pxPerM, dz = (p.z - centre.z) * pxPerM;
  const c = Math.cos(rotation), s = Math.sin(rotation);
  return [dx * c - dz * s, dx * s + dz * c];
}

/** Compass heading (deg, clockwise from north) of an engine x/z direction. */
export const compassDeg = (dx: number, dz: number) => (Math.atan2(dx, -dz) * 180 / Math.PI + 360) % 360;

export const formatTimer = (seconds: number) => {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};
export const formatMoney = (money: number) => `€${Math.round(money).toLocaleString('en-US')}`;

export class Minimap {
  readonly element = document.createElement('div');
  readonly routes: RouteTracker;
  expanded = false;
  draws = 0;
  drawMs = 0;
  private readonly canvas = document.createElement('canvas');
  private readonly ctx: CanvasRenderingContext2D;
  private readonly message = document.createElement('div');
  private readonly timer = document.createElement('span');
  private readonly money = document.createElement('span');
  private streets: StreetLayer;
  private state: MissionHudState = EMPTY_HUD_STATE;
  private unsubscribe = () => {};
  private dirty = true;
  private last: MinimapView | null = null;
  private cssSize = 0;
  private area: PlayableArea | null = null;
  private remotes: readonly { id: string; nick: string; x: number; z: number }[] = [];

  constructor(parent: HTMLElement, private graph: RoadGraph, router: Router) {
    this.routes = new RouteTracker(router);
    this.element.id = 'minimap';
    const status = document.createElement('div');
    status.className = 'minimap-status';
    const row = document.createElement('div');
    row.className = 'minimap-row';
    this.message.className = 'minimap-message';
    this.timer.className = 'minimap-timer';
    this.money.className = 'minimap-money';
    row.append(this.timer, this.money);
    status.append(this.message, row);
    this.element.append(status, this.canvas);
    parent.append(this.element);
    this.ctx = this.canvas.getContext('2d')!;
    this.streets = new StreetLayer(graph);
    this.setState(EMPTY_HUD_STATE);
  }

  setSource(source: MissionHudSource) {
    this.unsubscribe();
    this.unsubscribe = source.subscribe(state => this.setState(state));
  }

  /** Draws `graph` from now on (draft tiles' streets joined the map; gameplay/index.ts StreetMap). */
  setGraph(graph: RoadGraph) {
    this.graph = graph;
    this.routes.invalidate();
    this.streets.setGraph(graph);
    this.dirty = true;
  }

  /** Shades everything outside the built area and outlines its edge. */
  setArea(area: PlayableArea) { this.area = area; this.dirty = true; }
  setRemotes(players: readonly { id: string; nick: string; x: number; z: number }[]) {
    this.remotes = players;
    if (players.length) this.dirty = true;
  }

  toggleExpanded() {
    this.expanded = !this.expanded;
    this.element.classList.toggle('expanded', this.expanded);
    this.dirty = true;
  }

  /** Call every frame; redraws only when the view, state or size changed. */
  update(view: MinimapView) {
    const target = this.state.targets.find(t => t.id === this.state.activeTargetId) ?? null;
    if (this.routes.update(view.position, target, view.mode)) this.dirty = true;
    // On a phone in landscape (390 px tall) the corner map shrinks so the HUD above it stays readable.
    const size = this.expanded ? Math.round(Math.min(innerWidth, innerHeight) * 0.8) : Math.min(SIZE, Math.round(innerHeight * 0.45));
    if (size !== this.cssSize || this.canvas.width !== Math.round(size * devicePixelRatio)) {
      this.cssSize = size;
      this.canvas.style.width = this.canvas.style.height = `${size}px`;
      this.canvas.width = this.canvas.height = Math.round(size * devicePixelRatio);
      this.dirty = true;
    }
    const l = this.last;
    if (!this.dirty && l && l.mode === view.mode && l.nearEdge === view.nearEdge && Math.hypot(l.position.x - view.position.x, l.position.z - view.position.z) < 0.02
      && Math.abs(angle(l.headingDeg - view.headingDeg)) < 0.05 && Math.abs(angle(l.facingDeg - view.facingDeg)) < 0.5) return;
    const t0 = performance.now();
    this.draw(view);
    this.drawMs += (performance.now() - t0 - this.drawMs) * (this.draws ? 0.1 : 1);
    this.draws++;
    this.last = { ...view, position: { ...view.position } };
    this.dirty = false;
  }

  snapshot() {
    return { route: this.routes.route, routeLength: this.routes.length, routeComputeMs: this.routes.computeMs,
      drawMs: this.drawMs, draws: this.draws, expanded: this.expanded, cssSize: this.cssSize, state: this.state,
      streets: this.graph.edges.length, view: this.last };
  }

  dispose() { this.unsubscribe(); this.streets.dispose(); this.canvas.width = this.canvas.height = 0; this.element.remove(); }

  private setState(state: MissionHudState) {
    this.state = state;
    this.message.textContent = state.message ?? '';
    this.message.hidden = !state.message;
    this.timer.hidden = state.timerSeconds === null;
    this.timer.textContent = state.timerSeconds === null ? '' : formatTimer(state.timerSeconds);
    this.timer.classList.toggle('urgent', state.timerSeconds !== null && state.timerSeconds <= 10);
    this.money.textContent = formatMoney(state.money);
    this.dirty = true;
  }

  /** Outside cells darkened, the boundary between inside and outside cells stroked (world units). */
  private drawArea(area: PlayableArea, view: MinimapView, centre: WorldPoint, reach: number, k: number) {
    const { ctx } = this;
    const x0 = centre.x - reach, z0 = centre.z - reach, x1 = centre.x + reach, z1 = centre.z + reach;
    const s = COLLISION_TILE;
    ctx.beginPath();
    ctx.rect(x0, z0, x1 - x0, z1 - z0);
    for (const [i, j] of area.cellsIn(x0, z0, x1, z1)) ctx.rect(i * s, j * s, s, s);
    ctx.fillStyle = 'rgba(8, 10, 14, 0.72)';
    ctx.fill('evenodd');
    ctx.beginPath();
    for (const [i, j] of area.cellsIn(x0, z0, x1, z1)) {
      if (!area.hasCell(i, j - 1)) { ctx.moveTo(i * s, j * s); ctx.lineTo((i + 1) * s, j * s); }
      if (!area.hasCell(i, j + 1)) { ctx.moveTo(i * s, (j + 1) * s); ctx.lineTo((i + 1) * s, (j + 1) * s); }
      if (!area.hasCell(i - 1, j)) { ctx.moveTo(i * s, j * s); ctx.lineTo(i * s, (j + 1) * s); }
      if (!area.hasCell(i + 1, j)) { ctx.moveTo((i + 1) * s, j * s); ctx.lineTo((i + 1) * s, (j + 1) * s); }
    }
    ctx.strokeStyle = view.nearEdge ? '#ff6b6b' : 'rgba(255, 255, 255, 0.45)';
    ctx.lineWidth = (view.nearEdge ? 3 : 1.5) / k;
    ctx.stroke();
  }

  private draw(view: MinimapView) {
    const { ctx, cssSize: size } = this;
    const fit = this.expanded ? routeView(view.position, this.routes.route, SPAN_M.expanded) : { centre: view.position, span: SPAN_M[view.mode] };
    const { span, centre } = fit;
    const k = size / span;
    const rotation = this.expanded ? 0 : -view.headingDeg * Math.PI / 180;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, size, size);
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(1, 1, size - 2, size - 2, size * 0.08);
    ctx.fillStyle = 'rgba(38, 52, 60, 0.86)';
    ctx.fill();
    ctx.clip();
    ctx.save();
    ctx.translate(size / 2, size / 2);
    ctx.rotate(rotation);
    ctx.scale(k, k);
    ctx.translate(-centre.x, -centre.z);
    this.streets.draw(ctx, centre, span, size * devicePixelRatio);
    if (this.area) this.drawArea(this.area, view, centre, span / Math.SQRT1_2 / 2, k);
    const route = this.routes.route;
    if (route && route.length > 1) {
      ctx.lineCap = ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(route[0].x, route[0].z);
      for (const p of route.slice(1)) ctx.lineTo(p.x, p.z);
      ctx.strokeStyle = 'rgba(20, 20, 20, 0.7)';
      ctx.lineWidth = 9 / k;
      ctx.stroke();
      const active = this.state.targets.find(t => t.id === this.state.activeTargetId);
      ctx.strokeStyle = active ? KIND_COLOURS[active.kind] : '#f4c430';
      ctx.lineWidth = 5.5 / k;
      ctx.stroke();
    }
    ctx.restore();
    const inner = size / 2 - 12;
    for (const remote of this.remotes) {
      let [sx, sy] = project({ x: remote.x, z: remote.z }, centre, rotation, k);
      const edge = Math.max(Math.abs(sx), Math.abs(sy)) / inner;
      if (edge > 1) { sx /= edge; sy /= edge; }
      ctx.beginPath(); ctx.arc(size / 2 + sx, size / 2 + sy, 5, 0, Math.PI * 2);
      ctx.fillStyle = '#e88cff'; ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = 'bold 10px sans-serif'; ctx.textAlign = 'center';
      const labelWidth = Math.min(ctx.measureText(remote.nick).width, size - 12);
      const labelX = Math.max(6 + labelWidth / 2, Math.min(size - 6 - labelWidth / 2, size / 2 + sx));
      ctx.strokeStyle = '#10252d'; ctx.lineWidth = 3;
      const labelY = Math.max(14, size / 2 + sy - 8);
      ctx.strokeText(remote.nick, labelX, labelY, size - 12);
      ctx.fillText(remote.nick, labelX, labelY, size - 12);
    }
    for (const target of this.state.targets) {
      let [sx, sy] = project(target.point, centre, rotation, k);
      const edge = Math.max(Math.abs(sx), Math.abs(sy)) / inner;
      if (edge > 1) { sx /= edge; sy /= edge; } // clamp off-map markers to the rim, GTA style
      const active = target.id === this.state.activeTargetId;
      ctx.beginPath();
      ctx.arc(size / 2 + sx, size / 2 + sy, active ? 8 : 6, 0, Math.PI * 2);
      ctx.fillStyle = KIND_COLOURS[target.kind];
      ctx.fill();
      ctx.lineWidth = active ? 3 : 1.5;
      ctx.strokeStyle = active ? '#ffffff' : 'rgba(0, 0, 0, 0.7)';
      ctx.stroke();
      const number = this.expanded && target.kind === 'dropoff' ? houseNumber(target.label) : null;
      if (number) {
        ctx.font = 'bold 14px system-ui, sans-serif';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(0, 0, 0, 0.85)';
        ctx.strokeText(number, size / 2 + sx + 11, size / 2 + sy);
        ctx.fillStyle = '#ffffff';
        ctx.fillText(number, size / 2 + sx + 11, size / 2 + sy);
      }
    }
    const [playerX, playerZ] = project(view.position, centre, rotation, k);
    ctx.translate(size / 2 + playerX, size / 2 + playerZ);
    ctx.rotate((view.facingDeg * Math.PI / 180) + rotation);
    ctx.beginPath();
    ctx.moveTo(0, -11);
    ctx.lineTo(8, 9);
    ctx.lineTo(0, 4);
    ctx.lineTo(-8, 9);
    ctx.closePath();
    ctx.fillStyle = '#ffffff';
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = 'rgba(0, 0, 0, 0.75)';
    ctx.stroke();
    ctx.restore();
  }
}

/** The house number of a street address (`Linnankatu 13a` -> `13a`), or null without one. */
export function houseNumber(address: string): string | null {
  const last = address.trim().split(/\s+/).pop() ?? '';
  return /^\d/.test(last) ? last : null;
}

const angle = (deg: number) => ((deg + 540) % 360) - 180;
