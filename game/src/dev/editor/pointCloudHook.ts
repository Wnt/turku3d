// The hook between the object editor (owns the mode, its input and HUD) and the LiDAR point-cloud overlay (the
// lidarpc loop owns data, loading and rendering). The overlay registers itself; the editor binds the controls.
// Agreed in ~/turku3d/loops/editor/STATUS.md; neither side imports the other's code.

export interface PointCloudEvidence {
  /** Points within the query radius, the highest above the ground and optional per-class counts (ASPRS codes). */
  count: number; maxHeightM: number; classes?: Record<string, number>;
  [extra: string]: unknown;
}

export interface PointCloudOverlay {
  setVisible(on: boolean): void;
  isVisible(): boolean;
  /** 0..1 */
  setOpacity(alpha: number): void;
  getOpacity(): number;
  /** Screen pixels. */
  setPointSize(px: number): void;
  getPointSize(): number;
  /** Evidence near a ground position (engine metres: x east, z south) for an edit's context; null when unloaded there. */
  queryNear?(x: number, z: number, radiusM: number): PointCloudEvidence | null | Promise<PointCloudEvidence | null>;
}

declare global { interface Window { __turku3dPointCloud?: PointCloudOverlay } }

let overlay: PointCloudOverlay | null = null;

/** Called by the overlay when it is ready (or with null when it goes away). */
export function registerPointCloudOverlay(next: PointCloudOverlay | null) { overlay = next; }

/** The registered overlay, or one published on `window.__turku3dPointCloud`. */
export function pointCloudOverlay(): PointCloudOverlay | null {
  return overlay ?? (typeof window !== 'undefined' ? window.__turku3dPointCloud ?? null : null);
}
