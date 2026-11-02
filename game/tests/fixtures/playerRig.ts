import fs from 'node:fs';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { decodeGlb } from '../../src/world/decodeGlb';
import { parseDecodedGlb } from '../../src/world/loadGlb';

/** Actual shipped geometry/skin/clips; omit textures for CPU-only contact checks. */
export async function playerRig() {
  const bytes = fs.readFileSync(new URL('../../public/assets/character/player.glb', import.meta.url));
  const decoded = decodeGlb(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  const json = decoded.json as { materials: { name: string }[]; textures: unknown[]; images: unknown[]; extensionsRequired: string[]; extensionsUsed: string[] };
  json.materials = json.materials.map(m => ({ name: m.name })); json.textures = []; json.images = [];
  json.extensionsRequired = json.extensionsRequired.filter(e => e !== 'KHR_texture_basisu');
  json.extensionsUsed = json.extensionsUsed.filter(e => e !== 'KHR_texture_basisu');
  return parseDecodedGlb(new GLTFLoader().setMeshoptDecoder(MeshoptDecoder), decoded, '');
}
