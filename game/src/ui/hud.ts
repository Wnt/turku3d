// Debug HUD: backend, fps / frame ms, resident visual tris, collision MB, player E/N/H (EPSG:3877 + N2000).
import type { PerformanceMonitor } from '../render/performance';

interface HudData {
  backend: string; fps: number; frameMs: number; physMs: number;
  visTris: number; visModels: number;
  colTiles: number; colTris: number; colMB: number; colWasmMB: number;
  E: number; N: number; H: number; heading: number;
  mode: string; speed: number; ground: string; tile: string; colReader?: string;
  /** Draft (coarse) tile line from world/coarseTiles.ts; empty or absent on published tiles. */
  draft?: string;
  perf: PerformanceMonitor;
}
export class Hud {
  visible = true;
  private lastUpdate = -Infinity;
  constructor(private el: HTMLElement) {}
  set(d: HudData) {
    const now = performance.now();
    if (!this.visible || now - this.lastUpdate < 250) return;
    this.lastUpdate = now;
    const perf = d.perf.snapshot();
    this.el.textContent =
      `${d.backend}  ${d.fps.toFixed(0)} fps  ${d.frameMs.toFixed(2)} ms/frame  phys ${d.physMs.toFixed(2)} ms\n` +
      `p95 CPU ${perf.cpuMs.p95?.toFixed(2) ?? '—'} ms  GPU ${perf.gpuMs.p95?.toFixed(2) ?? perf.gpuStatus}  ` +
      `frame ${perf.frameMs.p95?.toFixed(2) ?? '—'} ms  draws ${perf.drawCalls}  ${(perf.triangles / 1000).toFixed(1)}k drawn tris\n` +
      `visual   ${(d.visTris / 1000).toFixed(1)}k tris resident in ${d.visModels} tile(s)\n` +
      `collision ${d.colTiles} tiles  ${(d.colTris / 1000).toFixed(1)}k tris  ${d.colMB.toFixed(2)} MB .col  ~${d.colWasmMB.toFixed(1)} MB wasm\n` +
      `EPSG:3877 E ${d.E.toFixed(2)}  N ${d.N.toFixed(2)}  H ${d.H.toFixed(2)} (N2000)  hdg ${d.heading.toFixed(0)}°\n` +
      `${d.mode}  ${d.speed.toFixed(1)} m/s  ground ${d.ground}  ${d.tile}` + (d.draft ? `\n${d.draft}` : '');
  }
  toggle() { this.visible = !this.visible; this.el.style.display = this.visible ? '' : 'none'; }
}
