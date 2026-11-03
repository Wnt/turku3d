// The hook between the object editor (owns the mode, its input and HUD) and the LiDAR point-cloud overlay (the
// lidarpc loop owns data, loading and rendering). The overlay registers itself; the editor binds the controls.
// Agreed in ~/turku3d/loops/editor/STATUS.md; neither side imports the other's code.

export interface PointCloudEvidence {
  /** Points within the query radius, the highest above the ground and optional per-class counts (ASPRS codes). */
  count: number; maxHeightM: number; classes?: Record<string, number>;
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
  /** Optional extras the editor binds when present (lidarpc's PointCloudOverlayApi has them). */
  setColorMode?(mode: string): void;
  getColorMode?(): string;
  setOcclusion?(on: boolean): void;
}

/** Colour modes cycled with Create + L1/R1 when the overlay offers them. */
export const COLOR_MODES = ['rgb', 'classification', 'elevation', 'intensity'] as const;

let overlay: PointCloudOverlay | null = null;

/** Called by the overlay when it is ready (or with null when it goes away). */
export function registerPointCloudOverlay(next: PointCloudOverlay | null) { overlay = next; }

/** The registered overlay, or one published on `window.__turku3dPointCloud` (the overlay's module declares that global;
 *  this side only reads it structurally, so the two declarations never clash). */
export function pointCloudOverlay(): PointCloudOverlay | null {
  if (overlay) return overlay;
  if (typeof window === 'undefined') return null;
  return (window as unknown as { __turku3dPointCloud?: PointCloudOverlay }).__turku3dPointCloud ?? null;
}
