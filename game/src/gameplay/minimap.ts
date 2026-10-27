// GTA-style minimap: streets from the road graph, heading-up rotation, mission markers and the route
// to the active target. A larger north-up view toggles with the map action (M / View).
import type { MissionHudSource, MissionHudState, Router, RouteTarget, TargetKind, WorldPoint } from './contract';
import { EMPTY_HUD_STATE } from './contract';
import { edgeClass, type RoadGraph, type TravelMode } from './roadGraph';

/** Marker colour per target kind, shared with the world zones (zones.ts). */
export const KIND_COLOURS: Record<TargetKind, string> = { pickup: '#f4c430', dropoff: '#4fc3f7', shop: '#66bb6a', market: '#ef6c8c' };
/** Recompute the route after the player moves this far from where it started. */
export const REROUTE_M = 5;
const SIZE = 240;
const SPAN_M = { foot: 200, car: 320, expanded: 650 };
const STATIC_PX_PER_M = 3;
const STATIC_PAD_M = 60;
// Road widths in metres on the static layer, by class path, service, street, major.
const ROAD_WIDTH = [1.8, 4, 6, 8];
const ROAD_COLOUR = ['#7c8a91', '#a9b3b8', '#c9d0d4', '#e3e7e9'];

interface MinimapView {
  position: WorldPoint;
  /** Camera compass heading (deg): the minimap turns so this points up. */
  headingDeg: number;
  /** Player or car compass heading (deg) for the centre arrow. */
  facingDeg: number;
  mode: TravelMode;
}

/** Keeps the route to the active target current without re-planning every frame. */
export class RouteTracker {
  route: WorldPoint[] | null = null;
  length = 0;
  computeMs = 0;
  private origin: WorldPoint | null = null;
  private key = '';
  constructor(private readonly router: Router) {}

  /** True when the route was recomputed. */
  update(position: WorldPoint, target: RouteTarget | null, mode: TravelMode): boolean {
    const key = target ? `${target.id}|${target.point.x}|${target.point.z}|${mode}` : '';
    const moved = this.origin ? Math.hypot(position.x - this.origin.x, position.z - this.origin.z) : Infinity;
    if (key === this.key && (!target || moved <= REROUTE_M)) return false;
    this.key = key;
    this.origin = { ...position };
    const t0 = performance.now();
    this.route = target ? this.router.route(position, target.point) : null;
    this.computeMs = performance.now() - t0;
    this.length = this.route ? this.router.distance(this.route) : 0;
    return true;
  }
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
  private readonly streets: HTMLCanvasElement | null;
  private state: MissionHudState = EMPTY_HUD_STATE;
  private unsubscribe = () => {};
  private dirty = true;
  private last: MinimapView | null = null;
  private cssSize = 0;

  constructor(parent: HTMLElement, private readonly graph: RoadGraph, router: Router) {
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
    this.streets = this.renderStreets();
    this.setState(EMPTY_HUD_STATE);
  }

  setSource(source: MissionHudSource) {
    this.unsubscribe();
    this.unsubscribe = source.subscribe(state => this.setState(state));
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
    if (!this.dirty && l && l.mode === view.mode && Math.hypot(l.position.x - view.position.x, l.position.z - view.position.z) < 0.02
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

  dispose() { this.unsubscribe(); this.element.remove(); }

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

  /** All streets once, into an offscreen layer that each frame only rotates and scales. */
  private renderStreets(): HTMLCanvasElement | null {
    const [x0, z0, x1, z1] = this.graph.bbox;
    if (!this.graph.edges.length) return null;
    const layer = document.createElement('canvas');
    layer.width = Math.ceil((x1 - x0 + 2 * STATIC_PAD_M) * STATIC_PX_PER_M);
    layer.height = Math.ceil((z1 - z0 + 2 * STATIC_PAD_M) * STATIC_PX_PER_M);
    const ctx = layer.getContext('2d')!;
    ctx.scale(STATIC_PX_PER_M, STATIC_PX_PER_M);
    ctx.translate(STATIC_PAD_M - x0, STATIC_PAD_M - z0);
    ctx.lineCap = ctx.lineJoin = 'round';
    for (let cls = 0; cls < 4; cls++) {
      ctx.beginPath();
      for (const edge of this.graph.edges) {
        if (edgeClass(edge) !== cls) continue;
        ctx.moveTo(edge.points[0], edge.points[1]);
        for (let i = 2; i < edge.points.length; i += 2) ctx.lineTo(edge.points[i], edge.points[i + 1]);
      }
      ctx.lineWidth = ROAD_WIDTH[cls];
      ctx.strokeStyle = ROAD_COLOUR[cls];
      ctx.stroke();
    }
    return layer;
  }

  private draw(view: MinimapView) {
    const { ctx, cssSize: size } = this;
    const span = this.expanded ? SPAN_M.expanded : SPAN_M[view.mode];
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
    ctx.translate(-view.position.x, -view.position.z);
    if (this.streets) {
      const [x0, z0] = this.graph.bbox;
      ctx.drawImage(this.streets, x0 - STATIC_PAD_M, z0 - STATIC_PAD_M,
        this.streets.width / STATIC_PX_PER_M, this.streets.height / STATIC_PX_PER_M);
    }
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
    for (const target of this.state.targets) {
      let [sx, sy] = project(target.point, view.position, rotation, k);
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
    }
    ctx.translate(size / 2, size / 2);
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

const angle = (deg: number) => ((deg + 540) % 360) - 180;
