import type * as THREE from 'three/webgpu';

/** The previous frame as TRAA keeps it (post.ts publishes it before any river material is built): its opaque depth
 *  and resolved HDR colour. River pixels read these for the bank fade (W3) and screen-space reflection (W1) without
 *  another pass: a mid-pass depth copy for the river would store and reload every beauty attachment (about 200 MB per
 *  frame at 1440p, a few ms on an M1 Air). With TRAA off (fxaa, off, idPass) both stay null and rivers keep the
 *  opaque, depth-writing path. */
export interface WaterHistory {
  depth: THREE.DepthTexture | null;
  color: THREE.Texture | null;
  /** Whether the beauty pass carries TRAA's velocity (no GTAO prepass): the river can then write its own motion. */
  beautyVelocity: boolean;
}

export const WATER_HISTORY: WaterHistory = { depth: null, color: null, beautyVelocity: false };
