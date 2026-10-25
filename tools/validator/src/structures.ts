// Acceptance of generated structures against the actual surrounding ground mesh.
import RAPIER from '@dimforge/rapier3d-compat';
import { GROUP, groups, MAT_FLAG, COL_FLAG, materialHasFlag, buildTileColliders } from '../../../game/src/shared/col/index.ts';
import type { ColTile } from '../../../game/src/shared/col/index.ts';
import { Walker } from './kcc.ts';

interface Traversal {
  start: number[];
  target: number[];
}
export interface StructureCase {
  object_id: string;
  kind: 'stairs' | 'wall' | 'fence' | 'railing' | 'quay';
  triangles: number[][][];
  material: number[];
  routes: Traversal[];
}

function createWorld(scene: StructureCase) {
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  // Preserve shared vertices so the runtime's FIX_INTERNAL_EDGES processing can
  // build the same adjacency as the assembled collision mesh.
  const vertices: number[] = [], indices: number[] = [];
  const remap = new Map<string, number>();
  for (const triangle of scene.triangles) for (const point of triangle) {
    const rounded = point.map(Math.fround), key = rounded.join(',');
    let index = remap.get(key);
    if (index === undefined) { index = vertices.length / 3; remap.set(key, index); vertices.push(...rounded); }
    indices.push(index);
  }
  const vertexCount = vertices.length / 3;
  const bounds = (fn: typeof Math.min): [number, number, number] => [0, 1, 2].map(axis => {
    let value = fn === Math.min ? Infinity : -Infinity;
    for (let i = axis; i < vertices.length; i += 3) value = fn(value, vertices[i]);
    return value;
  }) as [number, number, number];
  const tile: ColTile = {
    id: scene.object_id, positions: new Float32Array(vertices), indices: new Uint32Array(indices),
    materials: new Uint8Array(scene.material), primitives: [], sections: [], meta: null, skippedPrimitives: 0,
    header: {
      versionMajor: 1, versionMinor: 0, headerBytes: 96,
      flags: scene.material.some(id => materialHasFlag(id, MAT_FLAG.FIX_EDGES)) ? COL_FLAG.FIX_INTERNAL_EDGES : 0,
      origin: [0, 0, 0], tileI: 0, tileJ: 0, bboxMin: bounds(Math.min), bboxMax: bounds(Math.max),
      vertexCount, triangleCount: scene.triangles.length, primitiveCount: 0, sectionCount: 0,
      materialTableVersion: 1, fileBytes: 0, crc32: 0,
    },
  };
  buildTileColliders(RAPIER, world, tile);
  world.step();
  return world;
}

function walk(scene: StructureCase, route: Traversal) {
  const world = createWorld(scene);
  try {
    const [x, y, z] = route.start;
    const dx = route.target[0] - x, dz = route.target[2] - z;
    const distance = Math.hypot(dx, dz), ux = dx / distance, uz = dz / distance;
    const walker = new Walker(RAPIER, world);
    walker.place(x, y, z);
    const frames = Math.ceil((4 * distance / 2 + 2) * 60);
    let progress = 0;
    for (let i = 0; i < frames && progress < distance - 0.05; i++) {
      walker.step(2 * ux, 2 * uz);
      progress = (walker.x - x) * ux + (walker.z - z) * uz;
    }
    const heightError = Math.abs(walker.feetY - route.target[1]);
    const passed = scene.kind === 'stairs'
      ? progress >= distance - 0.05 && heightError <= 0.06
      : progress < distance / 2 - 0.25 && progress > 0.1;
    return { passed, progress_m: progress, distance_m: distance, final_feet_y: walker.feetY, height_error_m: heightError };
  } finally { world.free(); }
}

function car(scene: StructureCase, route: Traversal) {
  const world = createWorld(scene);
  try {
    const [x, y, z] = route.start;
    const dx = route.target[0] - x, dz = route.target[2] - z;
    const distance = Math.hypot(dx, dz), ux = dx / distance, uz = dz / distance;
    const yaw = -Math.atan2(uz, ux);
    const body = world.createRigidBody(RAPIER.RigidBodyDesc.dynamic().setTranslation(x, y + 0.8, z)
      .setRotation({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) })
      .setLinvel(10 * ux, 0, 10 * uz).setCcdEnabled(true));
    world.createCollider(RAPIER.ColliderDesc.cuboid(1, 0.5, 0.8).setMass(1500)
      .setCollisionGroups(groups(GROUP.VEHICLE, GROUP.STATIC_SOLID)), body);
    let furthest = 0;
    for (let i = 0; i < 120; i++) {
      world.step();
      const p = body.translation();
      furthest = Math.max(furthest, (p.x - x) * ux + (p.z - z) * uz);
    }
    const v = body.linvel(), forwardSpeed = v.x * ux + v.z * uz;
    return { passed: furthest < distance / 2 - 0.8 && furthest > 0.1 && Math.abs(forwardSpeed) < 0.5,
      max_progress_m: furthest, final_forward_speed_mps: forwardSpeed, initial_speed_mps: 10, mass_kg: 1500 };
  } finally { world.free(); }
}

export async function checkStructures(cases: StructureCase[]) {
  await RAPIER.init();
  const results = cases.map(scene => {
    const walkers = scene.routes.map(route => walk(scene, route));
    const cars = scene.kind === 'quay' ? scene.routes.map(route => car(scene, route)) : [];
    return { object_id: scene.object_id, kind: scene.kind, walkers, cars,
      passed: walkers.length > 0 && walkers.every(r => r.passed) && cars.every(r => r.passed) };
  });
  return { passed: results.length > 0 && results.every(r => r.passed), cases: results.length,
    passed_cases: results.filter(r => r.passed).length, results };
}
