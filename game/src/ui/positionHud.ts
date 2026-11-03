import * as THREE from 'three/webgpu';
import { engineToEN, VISUAL_L0, TRUE_NORTH_GRID_BEARING_DEG } from '../core/origin';
import { enToLatLon } from '../core/geographic';
import { devToolsEnabled } from '../core/localOnly';
import type { Input } from '../input';
import type { VisualTiles } from '../world/tiles';

const STORAGE_KEY = 'turku3d.position-hud.v1';
const CARDINALS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

export function buildingTarget(hit?: THREE.Intersection): string {
  for (let o: THREE.Object3D | null = hit?.object ?? null; o; o = o.parent) {
    const data = o.userData;
    const offset = (hit?.faceIndex ?? -1) * 3;
    const range = hit?.instanceId != null ? data.instance_objects?.[hit.instanceId]
      : data.objects?.find((r: { start: number; count: number }) => offset >= r.start && offset < r.start + r.count);
    const d = range ?? data;
    if (d.building_id != null) return [d.building_id, d.address].filter(Boolean).join(' · ');
    // A batch identity describes a draw call, never a building.
    if (d.layer === 'buildings' && !data.objects && !data.instance_objects)
      return [d.object_id ?? o.name, d.address].filter(Boolean).join(' · ');
  }
  return '';
}

export function positionReadout(position: THREE.Vector3, direction: THREE.Vector3, target = '') {
  const { E, N, H } = engineToEN(position.x, position.y, position.z);
  const { lat, lon } = enToLatLon(E, N);
  const heading = (Math.atan2(direction.x, -direction.z) * 180 / Math.PI - TRUE_NORTH_GRID_BEARING_DEG + 360) % 360;
  const degrees = Math.round(heading) % 360;
  const pitch = Math.atan2(direction.y, Math.hypot(direction.x, direction.z)) * 180 / Math.PI;
  return [
    `Camera ${lat.toFixed(6)}, ${lon.toFixed(6)}`,
    `EPSG:3877 E ${E.toFixed(2)} N ${N.toFixed(2)} · H ${H.toFixed(2)} m N2000`,
    `${degrees}° ${CARDINALS[Math.round(heading / 45) % 8]} · pitch ${pitch.toFixed(1)}° · t0_${Math.floor(position.x / VISUAL_L0)}_${Math.floor(position.z / VISUAL_L0)}`,
    ...(target ? [`Centre: ${target}`] : []),
  ];
}

/** Developer-only camera pose and photo controls, independent of the debug HUD. */
export class PositionHud {
  readonly element = Object.assign(document.createElement('div'), { id: 'position-hud' });
  visible = true;
  characterHidden = false;
  private lastUpdate = -Infinity;
  private direction = new THREE.Vector3();
  private ray = new THREE.Raycaster();
  private centre = new THREE.Vector2();
  private noticeUntil = 0;
  private notice = '';
  private bounds = new THREE.Box3();
  private point = new THREE.Vector3();
  private hits: THREE.Intersection[] = [];
  private candidates: { mesh: THREE.Mesh; distance: number }[] = [];
  private queryOrigin = new THREE.Vector3();
  private queryDirection = new THREE.Vector3();
  private queryAt = -Infinity;
  private queryLoads = -1;
  private queryModels = -1;
  private queryVisibility = -1;
  private target = '';
  private instanceBounds = new WeakMap<THREE.InstancedMesh, { matrix: THREE.InstancedBufferAttribute; version: number; count: number; sphere: THREE.Sphere }>();

  constructor(private camera: THREE.PerspectiveCamera, private tiles: VisualTiles, readonly enabled = devToolsEnabled(location),
    private visibilityVersion: () => number = () => 0) {
    try { this.visible = localStorage.getItem(STORAGE_KEY) !== 'off'; } catch { /* Session default. */ }
    this.visible &&= this.enabled;
    document.body.append(this.element);
    this.element.hidden = !this.visible;
  }

  handle(input: Input, playing: boolean) {
    if (!this.enabled || !playing) return;
    if (input.consume('positionHud')) this.toggle();
    if (input.consume('photoCharacter')) this.characterHidden = !this.characterHidden;
  }

  toggle() {
    if (!this.enabled) return;
    this.visible = !this.visible;
    this.element.hidden = !this.visible;
    this.lastUpdate = -Infinity;
    this.queryAt = -Infinity;
    try { localStorage.setItem(STORAGE_KEY, this.visible ? 'on' : 'off'); } catch { /* Keep session state. */ }
  }

  private lines(now: number, force = false) {
    this.camera.updateWorldMatrix(true, false);
    this.camera.getWorldDirection(this.direction);
    this.ray.setFromCamera(this.centre, this.camera);
    const visibility = this.visibilityVersion();
    // The camera readout still refreshes at 2 Hz. An unchanged view can reuse its
    // centre target; bound freshness for in-place geometry edits such as broken props.
    if (!force && now - this.queryAt < 2000 && this.queryLoads === this.tiles.loadedModels
      && this.queryModels === this.tiles.models.size && this.queryVisibility === visibility
      && this.queryOrigin.equals(this.ray.ray.origin) && this.queryDirection.equals(this.ray.ray.direction))
      return positionReadout(this.camera.position, this.direction, this.target);
    this.ray.far = 300;
    // Test bounding boxes first, then stop at the nearest surface. Raycasting all resident
    // triangles just to discard every hit except the first scales with the whole city.
    this.candidates.length = 0;
    for (const model of this.tiles.models) model.traverseVisible(o => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh || o.userData.shadowBatch || !this.ray.layers.test(mesh.layers)) return;
      if ((mesh as THREE.InstancedMesh).isInstancedMesh) {
        const instance = mesh as THREE.InstancedMesh;
        let bounds = this.instanceBounds.get(instance);
        if (!instance.boundingBox || !bounds || bounds.matrix !== instance.instanceMatrix
          || bounds.version !== instance.instanceMatrix.version || bounds.count !== instance.count) {
          instance.computeBoundingBox();
          if (!bounds) {
            bounds = { matrix: instance.instanceMatrix, version: -1, count: -1, sphere: new THREE.Sphere() };
            this.instanceBounds.set(instance, bounds);
          }
          bounds.matrix = instance.instanceMatrix; bounds.version = instance.instanceMatrix.version; bounds.count = instance.count;
          instance.boundingBox!.getBoundingSphere(bounds.sphere);
        }
        this.bounds.copy(instance.boundingBox!);
      } else {
        if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
        this.bounds.copy(mesh.geometry.boundingBox!);
      }
      this.bounds.applyMatrix4(mesh.matrixWorld);
      if (!this.ray.ray.intersectBox(this.bounds, this.point)) return;
      const distance = this.bounds.containsPoint(this.ray.ray.origin) ? 0 : this.point.distanceTo(this.ray.ray.origin);
      if (distance <= this.ray.far) this.candidates.push({ mesh, distance });
    });
    this.candidates.sort((a, b) => a.distance - b.distance);
    let hit: THREE.Intersection | undefined;
    for (const { mesh, distance } of this.candidates) {
      if (distance > this.ray.far) break;
      this.hits.length = 0;
      const instance = mesh as THREE.InstancedMesh;
      const bounds = this.instanceBounds.get(instance);
      if (bounds) {
        // Raycast needs the same fresh bounds. Preserve any conservative sphere
        // installed by the renderer/LOD owner; never tighten its render culling.
        const sphere = instance.boundingSphere;
        try { instance.boundingSphere = bounds.sphere; mesh.raycast(this.ray, this.hits); }
        finally { instance.boundingSphere = sphere; }
      } else mesh.raycast(this.ray, this.hits);
      for (const candidate of this.hits) if (!hit || candidate.distance < hit.distance) {
        hit = candidate;
        this.ray.far = hit.distance;
      }
    }
    this.target = buildingTarget(hit);
    this.queryAt = now;
    this.queryLoads = this.tiles.loadedModels;
    this.queryModels = this.tiles.models.size;
    this.queryVisibility = visibility;
    this.queryOrigin.copy(this.ray.ray.origin);
    this.queryDirection.copy(this.ray.ray.direction);
    // Intersections and candidates hold meshes: a hidden readout must not retain evicted tiles.
    this.hits.length = this.candidates.length = 0;
    return positionReadout(this.camera.position, this.direction, this.target);
  }

  update(now: number, playing: boolean) {
    this.element.hidden = !this.enabled || !playing || !this.visible;
    if (this.element.hidden || now - this.lastUpdate < 500) return;
    this.lastUpdate = now;
    this.element.textContent = [...this.lines(now), ...(now < this.noticeUntil ? [this.notice] : [])].join('\n');
  }

  /** Called directly from the keyboard gesture so Clipboard API activation is retained. */
  async copy() {
    if (!this.enabled) return;
    const text = this.lines(performance.now(), true).join(' · ');
    try {
      await navigator.clipboard.writeText(text);
      this.notice = 'Position copied';
    } catch { this.notice = 'Copy failed — allow clipboard access'; }
    this.noticeUntil = performance.now() + 2500;
    this.lastUpdate = -Infinity;
  }
}
