// Visible Finnish signal heads at roads.json stop events. The data supplies controllers and direction;
// signalState supplies the same deterministic fallback plan as the drivers (not measured Turku timings).
import * as THREE from 'three/webgpu';
import { CAR, EVENT, parseRoadGraph, type RoadGraphJson } from '../gameplay/roadGraph';
import { pointAt } from './network';
import { signalState } from './controls';
import type { Connector, Lane } from './tileNetwork';

export interface SignalHead { key: string; x: number; y: number; z: number; hx: number; hz: number; controller: string; left: boolean;
  /** Where the roads.json stop event came from (developer object editor provenance). */
  source?: { edge: string; way: number; s: number; direction: 'forward' | 'backward'; lanes: number; highway: string } }
/** A visual-only pose of a head (the editor's preview): pole base and yaw (rad, three.js rotation.y). */
export interface SignalPose { x: number; y: number; z: number; yaw: number }
interface Drawn { h: SignalHead; p: { x: number; y: number; z: number; hx: number; hz: number } }

export function signalHeads(tile: string, json: RoadGraphJson): SignalHead[] {
  const graph = parseRoadGraph(json), heads: SignalHead[] = [];
  for (const edge of graph.edges) {
    if (!(edge.flags & CAR) || !edge.heights) continue;
    for (const event of edge.events) {
      if ((event.code & 15) !== EVENT.SIGNAL) continue;
      const p = pointAt(edge.points, edge.cumulative, event.s);
      let i = 1;
      while (i < edge.cumulative.length - 1 && edge.cumulative[i] < event.s) i++;
      const f = Math.max(0, Math.min(1, (event.s - edge.cumulative[i - 1]) / Math.max(0.01, edge.cumulative[i] - edge.cumulative[i - 1])));
      const y = edge.heights[i - 1] + (edge.heights[i] - edge.heights[i - 1]) * f;
      const controller = graph.controllers[event.controller]?.id ?? `${tile}:${edge.a}:${edge.b}`;
      for (const dir of [1, -1]) {
        if (!(event.code & (dir > 0 ? EVENT.FORWARD : EVENT.BACKWARD))) continue;
        const lanes = dir > 0 ? edge.lanes.fwd : edge.lanes.bwd;
        if (!lanes) continue;
        const hx = p.hx * dir, hz = p.hz * dir, side = edge.lanes.width / 2 + 0.35;
        const x = p.x - hz * side, z = p.z + hx * side;
        const bearing = Math.round(Math.atan2(hz, hx) * 8 / Math.PI);
        heads.push({ key: `${controller}:${Math.round(x / 2)}:${Math.round(z / 2)}:${bearing}`,
          x, y, z, hx, hz, controller, left: lanes > 1,
          source: { edge: edge.id, way: edge.way, s: event.s, direction: dir > 0 ? 'forward' : 'backward', lanes, highway: edge.highway } });
      }
    }
  }
  return heads;
}

export class TrafficSignals {
  readonly root = new THREE.Group();
  private tiles = new Map<string, SignalHead[]>();
  /** Drawn heads: the tile's head (phases follow its approach) and the pose it is drawn at. */
  private heads: Drawn[] = [];
  /** Developer editor previews by head key: a pose, or null to leave the head out. Visual only. */
  private edits = new Map<string, SignalPose | null>();
  private matrix = new THREE.Matrix4();
  private position = new THREE.Vector3();
  private rotation = new THREE.Quaternion();
  private scale = new THREE.Vector3(1, 1, 1);
  private clockTick = -Infinity;
  private pole: THREE.InstancedMesh;
  private body: THREE.InstancedMesh;
  private unlit: THREE.InstancedMesh;
  private lenses: THREE.InstancedMesh[];
  private arrow: THREE.InstancedMesh;

  constructor(scene: THREE.Object3D) {
    this.root.name = 'traffic-signals'; scene.add(this.root);
    const mesh = (geometry: THREE.BufferGeometry, color: number, capacity = 512) => {
      const material = new THREE.MeshBasicMaterial({ color, toneMapped: false });
      const m = new THREE.InstancedMesh(geometry, material, capacity);
      m.count = 0; m.frustumCulled = false; this.root.add(m); return m;
    };
    this.pole = mesh(new THREE.CylinderGeometry(0.045, 0.055, 3.45, 6), 0x6d7476);
    this.body = mesh(new THREE.BoxGeometry(0.34, 0.98, 0.28), 0x111417, 1024);
    const circle = new THREE.CircleGeometry(0.098, 12);
    this.unlit = mesh(circle, 0x1b2020, 1536);
    this.lenses = [0xff3026, 0xffbf21, 0x37ee80].map(color => mesh(circle, color));
    const shape = new THREE.Shape();
    shape.moveTo(-0.105, 0); shape.lineTo(-0.005, 0.09); shape.lineTo(-0.005, 0.035);
    shape.lineTo(0.105, 0.035); shape.lineTo(0.105, -0.035); shape.lineTo(-0.005, -0.035);
    shape.lineTo(-0.005, -0.09); shape.closePath();
    this.arrow = mesh(new THREE.ShapeGeometry(shape), 0x37ee80);
  }

  setTile(tile: string, roads: RoadGraphJson) { this.tiles.set(tile, signalHeads(tile, roads)); this.rebuild(); }
  removeTile(tile: string) { if (this.tiles.delete(tile)) this.rebuild(); }

  /** Every head of the resident tiles as the roads describe it (editor picking; previews are not applied). */
  forEachHead(visit: (tile: string, head: SignalHead) => void) {
    for (const [tile, heads] of this.tiles) for (const head of heads) visit(tile, head);
  }

  /** Preview a head somewhere else (pose), hide it (null) or restore it (undefined). Phases, stop lines and drivers
   *  keep the roads' signal: only the drawn head moves. */
  setEdit(key: string, pose: SignalPose | null | undefined) {
    if (pose === undefined) this.edits.delete(key); else this.edits.set(key, pose);
    this.rebuild();
  }

  private place(mesh: THREE.InstancedMesh, index: number, h: Drawn['p'], lift: number, side = 0, front = 0, size = 1) {
    this.position.set(h.x - h.hz * side - h.hx * front, h.y + lift, h.z + h.hx * side - h.hz * front);
    this.rotation.setFromAxisAngle(UP, Math.atan2(-h.hx, -h.hz));
    this.scale.setScalar(size);
    mesh.setMatrixAt(index, this.matrix.compose(this.position, this.rotation, this.scale));
  }

  private rebuild() {
    this.heads = [...new Map([...this.tiles].sort(([a], [b]) => a < b ? -1 : 1).flatMap(([, heads]) => heads).map(h => [h.key, h])).values()]
      .sort((a, b) => a.key < b.key ? -1 : 1).flatMap(h => {
        const edit = this.edits.get(h.key);
        if (edit === null) return [];
        return [{ h, p: edit ? { x: edit.x, y: edit.y, z: edit.z, hx: -Math.sin(edit.yaw), hz: -Math.cos(edit.yaw) } : h }];
      }).slice(0, 512);
    let bodies = 0;
    this.heads.forEach(({ h: { left }, p: h }, i) => {
      this.place(this.pole, i, h, 1.725); this.place(this.body, bodies++, h, 2.97);
      if (left) { this.place(this.body, bodies++, h, 2.97, -0.38, 0, 0.35); }
      [3.27, 2.97, 2.67].forEach((lift, k) => this.place(this.unlit, i * 3 + k, h, lift, 0, 0.151));
    });
    this.pole.count = this.heads.length; this.body.count = bodies; this.unlit.count = this.heads.length * 3;
    for (const mesh of [this.pole, this.body, this.unlit]) mesh.instanceMatrix.needsUpdate = true;
    this.clockTick = -Infinity;
  }

  update(time: number) {
    const tick = Math.floor(time * 5);
    if (tick === this.clockTick) return;
    this.clockTick = tick;
    this.heads.forEach(({ h, p }, i) => {
      // The phase is the roads' head's (its approach direction), wherever a preview draws it.
      const lane = { signal: h.controller, points: Float64Array.of(0, 0, h.hx, h.hz), connector: null } as unknown as Lane;
      const state = signalState(lane, time);
      const active = [state === 'red' || state === 'red-amber', state === 'amber' || state === 'red-amber', state === 'green'];
      this.lenses.forEach((mesh, k) => this.place(mesh, i, p, 3.27 - k * 0.3, 0, 0.155, active[k] ? 1 : 0));
      lane.connector = { turn: -Math.PI / 2 } as Connector;
      const arrow = h.left && state !== 'green' && signalState(lane, time) === 'green';
      this.place(this.arrow, i, p, 2.97, -0.38, 0.155, arrow ? 0.5 : 0);
    });
    for (const mesh of [...this.lenses, this.arrow]) { mesh.count = this.heads.length; mesh.instanceMatrix.needsUpdate = true; }
  }

  get count() { return this.heads.length; }
  dispose() {
    this.root.removeFromParent();
    for (const child of this.root.children) {
      const mesh = child as THREE.InstancedMesh; mesh.dispose(); mesh.geometry.dispose();
      (mesh.material as THREE.Material).dispose();
    }
  }
}
const UP = new THREE.Vector3(0, 1, 0);
