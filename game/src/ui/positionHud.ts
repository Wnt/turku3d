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

  constructor(private camera: THREE.PerspectiveCamera, private tiles: VisualTiles, readonly enabled = devToolsEnabled(location)) {
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
    try { localStorage.setItem(STORAGE_KEY, this.visible ? 'on' : 'off'); } catch { /* Keep session state. */ }
  }

  private lines() {
    this.camera.updateWorldMatrix(true, false);
    this.camera.getWorldDirection(this.direction);
    this.ray.setFromCamera(this.centre, this.camera);
    this.ray.far = 300;
    const meshes: THREE.Object3D[] = [];
    for (const model of this.tiles.models) model.traverseVisible(o => {
      if ((o as THREE.Mesh).isMesh && !o.userData.shadowBatch) meshes.push(o);
    });
    const hit = this.ray.intersectObjects(meshes, false)[0];
    const target = buildingTarget(hit);
    return positionReadout(this.camera.position, this.direction, target);
  }

  update(now: number, playing: boolean) {
    this.element.hidden = !this.enabled || !playing || !this.visible;
    if (this.element.hidden || now - this.lastUpdate < 500) return;
    this.lastUpdate = now;
    this.element.textContent = [...this.lines(), ...(now < this.noticeUntil ? [this.notice] : [])].join('\n');
  }

  /** Called directly from the keyboard gesture so Clipboard API activation is retained. */
  async copy() {
    if (!this.enabled) return;
    const text = this.lines().join(' · ');
    try {
      await navigator.clipboard.writeText(text);
      this.notice = 'Position copied';
    } catch { this.notice = 'Copy failed — allow clipboard access'; }
    this.noticeUntil = performance.now() + 2500;
    this.lastUpdate = -Infinity;
  }
}
