// Activation zones in the world: a glowing ground ring with a fading light wall and a floating label at every
// target the mission HUD lists (shops, the pizzeria and the current drop-off), coloured like the minimap markers.
import * as THREE from 'three/webgpu';
import { float, uniform, uv } from 'three/tsl';
import type { MissionHudSource, RouteTarget, WorldPoint } from './contract';
import { KIND_COLOURS } from './minimap';
import { lineText } from './content/dialogue';
import { DEFAULT_ZONE_RAY_TOP, ZONE_LABELS, ZONE_RAY_TOP } from './content/shops';

/** Drawn radius: the 3 m interaction radius plus a visible margin. */
export const ZONE_RADIUS = 3.5;
const RING_WIDTH = 0.28;
const WALL_HEIGHT = 1.5;
const LABEL_HEIGHT = 2.7;
/** Full strength up to FADE_START, gone at FADE_END; labels show up to LABEL_RANGE. */
const FADE_START = 50;
const FADE_END = 75;
export const LABEL_RANGE = 60;
const PULSE_SECONDS = 1.6;
/** HDR gain on the zone colour so the bloom pass picks the ring up. */
const GLOW = 2.4;
const PROBE_RETRY_SECONDS = 1;

/** Ground height under (x, z) from a downward ray starting at `top`, or null while no collider is there. */
type GroundProbe = (x: number, z: number, top: number) => Promise<number | null>;

interface Zone {
  target: RouteTarget;
  group: THREE.Group;
  strength: THREE.UniformNode<"float", number>;
  label: HTMLDivElement;
  y: number | null;
  probing: boolean;
  nextProbe: number;
  opacity: number;
  labelShown: boolean;
}

/** Strength of a zone seen from `metres` away at time `t` (s): a gentle pulse times the distance fade. */
export function zoneStrength(metres: number, t: number): number {
  const fade = Math.min(1, Math.max(0, (FADE_END - metres) / (FADE_END - FADE_START)));
  return fade * (0.78 + 0.22 * Math.sin((2 * Math.PI * t) / PULSE_SECONDS));
}

const ringGeometry = new THREE.RingGeometry(ZONE_RADIUS - RING_WIDTH, ZONE_RADIUS, 72).rotateX(-Math.PI / 2);
const discGeometry = new THREE.CircleGeometry(ZONE_RADIUS - RING_WIDTH, 48).rotateX(-Math.PI / 2);
const wallGeometry = new THREE.CylinderGeometry(ZONE_RADIUS, ZONE_RADIUS, WALL_HEIGHT, 72, 1, true)
  .translate(0, WALL_HEIGHT / 2, 0);

function glowMaterial(colour: THREE.Color, opacity: THREE.Node) {
  const material = new THREE.MeshBasicNodeMaterial({ color: colour, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, side: THREE.DoubleSide, fog: false });
  material.opacityNode = opacity;
  return material;
}

export class ZoneMarkers {
  readonly group = new THREE.Group();
  readonly labels = document.createElement('div');
  private zones = new Map<string, Zone>();
  private unsubscribe = () => {};
  private time = 0;
  private hidden = false;
  private readonly projected = new THREE.Vector3();

  constructor(scene: THREE.Scene, private readonly ground: GroundProbe, parent: HTMLElement) {
    this.group.name = 'mission-zones';
    scene.add(this.group);
    this.labels.id = 'zone-labels';
    parent.append(this.labels);
  }

  /** Follows a mission HUD source: one zone per listed target; a target that leaves the list loses its zone. */
  setSource(source: MissionHudSource) {
    this.unsubscribe();
    this.unsubscribe = source.subscribe(state => this.sync(state.targets));
  }

  /** Hides every zone and label (the shop menu covers the world). */
  setHidden(hidden: boolean) {
    this.hidden = hidden;
    this.labels.style.display = hidden ? 'none' : '';
  }

  /** Pulses and fades each zone for the viewer's distance and places its label above it. */
  update(dt: number, camera: THREE.Camera, viewer: WorldPoint) {
    this.time += dt;
    const width = innerWidth, height = innerHeight;
    for (const zone of this.zones.values()) {
      if (zone.y === null) { this.probe(zone); continue; }
      const metres = Math.hypot(zone.target.point.x - viewer.x, zone.target.point.z - viewer.z);
      zone.opacity = this.hidden ? 0 : zoneStrength(metres, this.time);
      zone.strength.value = zone.opacity;
      zone.group.visible = zone.opacity > 0;
      const p = this.projected.set(zone.target.point.x, zone.y + LABEL_HEIGHT, zone.target.point.z).project(camera);
      zone.labelShown = !this.hidden && metres <= LABEL_RANGE && p.z > -1 && p.z < 1 && Math.abs(p.x) < 1.1 && Math.abs(p.y) < 1.1;
      zone.label.style.display = zone.labelShown ? '' : 'none';
      if (zone.labelShown) {
        zone.label.style.transform = `translate(${((p.x + 1) / 2) * width}px, ${((1 - p.y) / 2) * height}px) translate(-50%, -100%)`;
        zone.label.style.opacity = String(Math.min(1, (LABEL_RANGE - metres) / 8 + 0.3));
      }
    }
  }

  snapshot() {
    return [...this.zones.values()].map(z => ({ id: z.target.id, kind: z.target.kind, label: z.label.textContent,
      y: z.y, visible: z.group.visible, opacity: z.opacity, labelShown: z.labelShown }));
  }

  private sync(targets: RouteTarget[]) {
    const wanted = new Map(targets.map(t => [t.id, t]));
    for (const [id, zone] of this.zones) {
      const next = wanted.get(id);
      if (next && next.point.x === zone.target.point.x && next.point.z === zone.target.point.z) continue;
      this.remove(zone);
    }
    for (const target of targets) if (!this.zones.has(target.id)) this.add(target);
  }

  private add(target: RouteTarget) {
    const colour = new THREE.Color(KIND_COLOURS[target.kind]).multiplyScalar(GLOW);
    const strength = uniform(0);
    const fadeUp = float(1).sub(uv().y).pow(2);
    const group = new THREE.Group();
    group.name = `zone:${target.id}`;
    group.visible = false;
    group.add(new THREE.Mesh(ringGeometry, glowMaterial(colour, strength.mul(0.95))),
      new THREE.Mesh(discGeometry, glowMaterial(colour, strength.mul(0.16))),
      new THREE.Mesh(wallGeometry, glowMaterial(colour, strength.mul(fadeUp).mul(0.5))));
    for (const mesh of group.children) mesh.renderOrder = 10;
    group.position.set(target.point.x, 0, target.point.z);
    this.group.add(group);
    const label = document.createElement('div');
    label.className = 'zone-label';
    label.textContent = lineText(ZONE_LABELS[target.kind]);
    label.style.setProperty('--zone-colour', KIND_COLOURS[target.kind]);
    label.style.display = 'none';
    this.labels.append(label);
    this.zones.set(target.id, { target, group, strength, label, y: null, probing: false, nextProbe: 0, opacity: 0,
      labelShown: false });
  }

  private remove(zone: Zone) {
    this.group.remove(zone.group);
    for (const mesh of zone.group.children as THREE.Mesh[]) (mesh.material as THREE.Material).dispose();
    zone.label.remove();
    this.zones.delete(zone.target.id);
  }

  /** Finds the ground under a zone once its collision tile is resident; retries every second until then. */
  private probe(zone: Zone) {
    if (zone.probing || this.time < zone.nextProbe) return;
    zone.probing = true;
    const { x, z } = zone.target.point;
    void this.ground(x, z, ZONE_RAY_TOP[zone.target.id] ?? DEFAULT_ZONE_RAY_TOP).then(y => {
      zone.probing = false;
      if (this.zones.get(zone.target.id) !== zone) return;
      if (y === null) { zone.nextProbe = this.time + PROBE_RETRY_SECONDS; return; }
      zone.y = y;
      zone.group.position.y = y + 0.05;
    });
  }
}
