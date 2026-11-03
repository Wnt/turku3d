// Level of detail for the point-cloud overlay: a screen-space-error traversal of every tile's octree under a hard
// point budget (as Potree and 3D Tiles viewers do). COPC refinement is additive: a child holds points its parent
// lacks, so a node is drawn together with its ancestors and the parents always come first.

export interface LodNode {
  key: string;
  depth: number;
  pointCount: number;
  /** Points the class filter draws, exactly, once the node is loaded (its instance buffer's capacity). */
  drawable?: number;
  /** Estimated share the class filter draws while not loaded (from the parent; 1 until known). */
  weight?: number;
  /** Engine-frame AABB: min x, y, z, max x, y, z. */
  box: readonly number[];
  /** Mean point spacing (m) of this node's own points. */
  spacing: number;
  children: LodNode[];
  /** Decoded and uploaded: only loaded nodes are drawn and refined further, so loading runs coarse to fine. */
  loaded: boolean;
}

export interface LodView {
  x: number; y: number; z: number;
  /** Projection focal length in drawing-buffer pixels: P[1][1] * height / 2. */
  focal: number;
  /** Frustum test of an engine-frame AABB. */
  visible(box: readonly number[]): boolean;
}

export interface LodOptions {
  /** Hard cap on selected points (drawn plus wanted). */
  budget: number;
  /** A node refines while its own spacing projects to more than this many pixels. */
  refinePx: number;
  /** Below this projected spacing (px) a node draws only a prefix of its shuffled points (decode shuffles them). */
  thinPx: number;
  /** Tiles whose box is farther than this (m) are skipped. */
  maxDistance: number;
}

export interface LodSelection {
  /** Highest priority first: the load queue follows this order. */
  selected: LodNode[];
  /** Points drawn of each selected node (a prefix of its shuffled points) and their sum, which the budget caps. */
  drawn: Map<LodNode, number>;
  points: number;
  /** Projected spacing (px) of each selected node, for the draw size. */
  pixels: Map<LodNode, number>;
}

export function boxDistance(box: readonly number[], x: number, y: number, z: number): number {
  const dx = Math.max(box[0] - x, 0, x - box[3]);
  const dy = Math.max(box[1] - y, 0, y - box[4]);
  const dz = Math.max(box[2] - z, 0, z - box[5]);
  return Math.hypot(dx, dy, dz);
}

/** Projected spacing (px) of a node seen from the view position. */
export function projectedSpacing(node: LodNode, view: LodView): number {
  return node.spacing * view.focal / Math.max(1, boxDistance(node.box, view.x, view.y, view.z));
}

/** Binary max-heap on priority. */
class Heap {
  private items: { node: LodNode; priority: number }[] = [];
  get size() { return this.items.length; }
  push(node: LodNode, priority: number) {
    const a = this.items;
    a.push({ node, priority });
    let i = a.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (a[p].priority >= a[i].priority) break;
      [a[p], a[i]] = [a[i], a[p]]; i = p;
    }
  }
  pop() {
    const a = this.items, top = a[0], last = a.pop()!;
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = i * 2 + 1, r = l + 1;
        let m = i;
        if (l < a.length && a[l].priority > a[m].priority) m = l;
        if (r < a.length && a[r].priority > a[m].priority) m = r;
        if (m === i) break;
        [a[m], a[i]] = [a[i], a[m]]; i = m;
      }
    }
    return top;
  }
}

/** Points of a node worth drawing at a projected spacing: all it draws (after the class filter) up to `thinPx`, then by
 *  screen area. A node whose classes are all filtered away costs nothing but still refines into its children. */
export function drawnPoints(node: LodNode, px: number, thinPx: number): number {
  const fraction = Math.min(1, (px / thinPx) ** 2);
  // Integers only: pointCount * (kept / count) can land one ulp above kept, and ceil then asked for one instance
  // more than the node's buffer holds (WebGPU rejected those draws, the pass stopped updating: points froze on screen).
  const shown = node.drawable ?? Math.round(node.pointCount * (node.weight ?? 1));
  return shown > 0 ? Math.min(shown, Math.max(1, Math.ceil(shown * fraction))) : 0;
}

/** Select the nodes to draw or load: largest projected spacing first, until the budget is spent. A node that does not
 *  fit is skipped with its subtree; smaller nodes behind it may still fit. */
export function selectNodes(roots: readonly LodNode[], view: LodView, options: LodOptions): LodSelection {
  const heap = new Heap();
  for (const root of roots) {
    if (boxDistance(root.box, view.x, view.y, view.z) > options.maxDistance || !view.visible(root.box)) continue;
    heap.push(root, projectedSpacing(root, view));
  }
  const selected: LodNode[] = [], pixels = new Map<LodNode, number>(), drawn = new Map<LodNode, number>();
  let points = 0;
  while (heap.size) {
    const { node, priority: px } = heap.pop();
    const count = drawnPoints(node, px, options.thinPx);
    if (points + count > options.budget) continue;
    selected.push(node); pixels.set(node, px); drawn.set(node, count);
    points += count;
    if (!node.loaded || px <= options.refinePx) continue;
    for (const child of node.children) {
      if (!view.visible(child.box)) continue;
      heap.push(child, projectedSpacing(child, view));
    }
  }
  return { selected, drawn, points, pixels };
}
