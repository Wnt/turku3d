import { edgeClass, type RoadGraph } from './roadGraph';
import type { WorldPoint } from './contract';

const MAX_PIXELS = 2048;
const ROAD_WIDTH = [1.8, 4, 6, 8];
const ROAD_COLOUR = ['#7c8a91', '#a9b3b8', '#c9d0d4', '#e3e7e9'];

/** A reusable viewport raster, never a bitmap of the whole accumulated city graph. */
export class StreetLayer {
  private canvas: HTMLCanvasElement | null = null;
  private view: { x: number; z: number; span: number; scale: number } | null = null;
  private bounds = new Float64Array();
  private classes = new Uint8Array();
  constructor(private graph: RoadGraph) { this.setGraph(graph); }

  setGraph(graph: RoadGraph) {
    this.graph = graph;
    this.view = null;
    this.bounds = new Float64Array(graph.edges.length * 4);
    this.classes = new Uint8Array(graph.edges.length);
    graph.edges.forEach((edge, index) => {
      let x0 = Infinity, z0 = Infinity, x1 = -Infinity, z1 = -Infinity;
      for (let i = 0; i < edge.points.length; i += 2) {
        x0 = Math.min(x0, edge.points[i]); z0 = Math.min(z0, edge.points[i + 1]);
        x1 = Math.max(x1, edge.points[i]); z1 = Math.max(z1, edge.points[i + 1]);
      }
      this.bounds.set([x0, z0, x1, z1], index * 4);
      this.classes[index] = edgeClass(edge);
    });
  }

  /** Overscan covers every map rotation and lets the player move without repainting streets each frame. */
  draw(target: CanvasRenderingContext2D, centre: WorldPoint, span: number, pixels: number) {
    if (!this.graph.edges.length) return;
    const reach = span / Math.SQRT2;
    const scale = Math.min(pixels / span, MAX_PIXELS / (span * 2));
    const old = this.view;
    if (!old || centre.x - reach < old.x || centre.z - reach < old.z
      || centre.x + reach > old.x + old.span || centre.z + reach > old.z + old.span
      || scale > old.scale * 1.25 || scale < old.scale * 0.5) {
      this.render(centre, span * 2, scale);
    }
    const view = this.view!;
    target.drawImage(this.canvas!, view.x, view.z, view.span, view.span);
  }

  dispose() {
    // Release the native bitmap immediately, including when a play session is replaced.
    if (this.canvas) this.canvas.width = this.canvas.height = 0;
    this.canvas = null; this.view = null;
  }

  private render(centre: WorldPoint, span: number, scale: number) {
    const layer = this.canvas ??= document.createElement('canvas');
    const pixels = Math.min(MAX_PIXELS, Math.ceil(span * scale));
    layer.width = layer.height = pixels;
    scale = pixels / span;
    const x = centre.x - span / 2, z = centre.z - span / 2;
    this.view = { x, z, span, scale };
    const ctx = layer.getContext('2d')!;
    ctx.setTransform(scale, 0, 0, scale, -x * scale, -z * scale);
    ctx.lineCap = ctx.lineJoin = 'round';
    for (let cls = 0; cls < 4; cls++) {
      ctx.beginPath();
      const pad = ROAD_WIDTH[cls] / 2;
      this.graph.edges.forEach((edge, index) => {
        const i = index * 4, b = this.bounds;
        if (this.classes[index] !== cls || b[i] > x + span + pad || b[i + 2] < x - pad
          || b[i + 1] > z + span + pad || b[i + 3] < z - pad) return;
        ctx.moveTo(edge.points[0], edge.points[1]);
        for (let j = 2; j < edge.points.length; j += 2) ctx.lineTo(edge.points[j], edge.points[j + 1]);
      });
      ctx.lineWidth = ROAD_WIDTH[cls]; ctx.strokeStyle = ROAD_COLOUR[cls]; ctx.stroke();
    }
  }
}
