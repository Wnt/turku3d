import type * as THREE from 'three/webgpu';

/** Diagnostics for perf work without a profiler UI: which node programs get built (and whether inside a live frame,
 * where they stall the frame, or during streaming preparation), and one frame's draws/triangles per pass and group.
 * Read with `__render.census()` in the console or from a capture; nothing here runs per draw unless a census frame
 * is requested. */
export interface BuildEntry { t: number; inFrame: boolean; pass: string; material: string; object: string }

const LIMIT = 400;
export class BuildLog {
  readonly entries: BuildEntry[] = [];
  inFrameTotal = 0;
  preparedTotal = 0;
  record(renderer: THREE.WebGPURenderer, draw: { object?: THREE.Object3D; material?: THREE.Material; camera?: THREE.Camera }, inFrame: boolean) {
    if (inFrame) this.inFrameTotal++; else this.preparedTotal++;
    this.entries.push({ t: Math.round(performance.now()), inFrame, pass: passLabel(renderer, draw.camera),
      material: label(draw.material), object: objectLabel(draw.object) });
    if (this.entries.length > LIMIT) this.entries.shift();
  }
}

function label(value?: { name?: string; type?: string } | null) { return (value?.name || value?.type || '?').slice(0, 80); }
/** The nearest named ancestors, with tile ids and hashes folded so families aggregate. */
function objectLabel(object?: THREE.Object3D) {
  const names: string[] = [];
  for (let node = object ?? null; node && names.length < 3; node = node.parent) {
    if (node.name && !(node as THREE.Scene).isScene) names.push(node.name.replace(/[0-9a-f]{12,}/g, '#').replace(/t0_-?\d+_-?\d+/g, 'T').slice(0, 48));
  }
  return names.reverse().join(' > ') || object?.type || '?';
}
function passLabel(renderer: THREE.WebGPURenderer, camera?: THREE.Camera) {
  const target = renderer.getRenderTarget();
  const name = target ? (target.texture?.name || `rt${target.width}x${target.height}`) : 'screen';
  return `${name}|${(camera as THREE.OrthographicCamera | undefined)?.isOrthographicCamera ? 'ortho' : 'persp'}`;
}

type Direct = (object: THREE.Object3D, material: THREE.Material, scene: THREE.Scene, camera: THREE.Camera, ...rest: unknown[]) => void;
export interface FrameEnds { install(each: () => void): void; uninstall(): void }
/** One whole frame's draws and triangles by pass and object family, sorted by draws. */
export function drawCensus(renderer: THREE.WebGPURenderer, frameEnds: FrameEnds, builds?: BuildLog) {
  return new Promise<{ draws: number; rows: [string, number, number][]; builds?: { inFrame: number; prepared: number; recent: BuildEntry[] } }>(resolve => {
    const host = renderer as unknown as { _renderObjectDirect: Direct };
    const original = host._renderObjectDirect;
    const rows = new Map<string, { draws: number; tris: number }>();
    host._renderObjectDirect = function (this: unknown, object, material, scene, camera, ...rest) {
      const mesh = object as THREE.Mesh, geometry = mesh.geometry;
      let count = geometry?.index ? geometry.index.count : geometry?.attributes.position?.count ?? 0;
      if (geometry?.drawRange && geometry.drawRange.count !== Infinity) count = Math.min(count, geometry.drawRange.count);
      const instances = (mesh as THREE.InstancedMesh).count > 1 ? (mesh as THREE.InstancedMesh).count : 1;
      const key = `${passLabel(renderer, camera)}\t${objectLabel(object)}`;
      const row = rows.get(key) ?? { draws: 0, tris: 0 }; row.draws++; row.tris += count / 3 * instances; rows.set(key, row);
      return original.call(this, object, material, scene, camera, ...rest);
    };
    let ends = 0;
    frameEnds.install(() => {
      if (++ends === 1) { rows.clear(); return; } // the first end closes a partial frame
      frameEnds.uninstall();
      host._renderObjectDirect = original;
      const sorted = [...rows].map(([key, row]) => [key, row.draws, Math.round(row.tris)] as [string, number, number]).sort((a, b) => b[1] - a[1]);
      resolve({ draws: sorted.reduce((sum, row) => sum + row[1], 0), rows: sorted,
        builds: builds && { inFrame: builds.inFrameTotal, prepared: builds.preparedTotal, recent: builds.entries.slice(-80) } });
    });
  });
}
