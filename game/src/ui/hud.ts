// Debug HUD: backend, fps / frame ms, resident visual tris, collision MB, player E/N/H (EPSG:3877 + N2000).
interface HudData {
  backend: string; fps: number; frameMs: number; physMs: number;
  visTris: number; visModels: number;
  colTiles: number; colTris: number; colMB: number; colWasmMB: number;
  E: number; N: number; H: number; heading: number;
  mode: string; speed: number; ground: string; tile: string; colReader?: string;
}
export class Hud {
  visible = true;
  constructor(private el: HTMLElement) {}
  set(d: HudData) {
    if (!this.visible) return;
    this.el.textContent =
      `${d.backend}  ${d.fps.toFixed(0)} fps  ${d.frameMs.toFixed(2)} ms/frame  phys ${d.physMs.toFixed(2)} ms\n` +
      `visual   ${(d.visTris / 1000).toFixed(1)}k tris resident in ${d.visModels} tile(s)\n` +
      `collision ${d.colTiles} tiles  ${(d.colTris / 1000).toFixed(1)}k tris  ${d.colMB.toFixed(2)} MB .col  ~${d.colWasmMB.toFixed(1)} MB wasm\n` +
      `EPSG:3877 E ${d.E.toFixed(2)}  N ${d.N.toFixed(2)}  H ${d.H.toFixed(2)} (N2000)  hdg ${d.heading.toFixed(0)}°\n` +
      `${d.mode}  ${d.speed.toFixed(1)} m/s  ground ${d.ground}  ${d.tile}`;
  }
  toggle() { this.visible = !this.visible; this.el.style.display = this.visible ? '' : 'none'; }
}
