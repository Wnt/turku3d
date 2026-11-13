// Material families of the world pipeline (docs/design/world-pipeline.md, section 3.3).
//
// A compiled mesh names its materials by family and parameters (glTF `material.extras.turku`) and carries a plain
// PBR fallback. A family registered here turns that reference into the material the renderer draws; a family
// nobody registered draws its fallback. Tracks add their families with `registerFamily`.
import * as THREE from 'three/webgpu';

export interface FamilyReference { family: string; params: Record<string, unknown> }
/** What one parameter set of a family is drawn with. A family that draws many parameter sets with one material
 *  (one shader build, and meshes that merge into one draw) tells them apart per vertex: every vertex drawn with this
 *  set carries `vertex.value` in the one-float attribute `vertex.name` (`markVertices`). */
export interface Drawn { material: THREE.Material; vertex?: { name: string; value: number } }
/** Builds the drawn material of one family. `fallback` is the glTF material (base colour, roughness, metalness). */
export type Family = (params: Record<string, unknown>, fallback: THREE.MeshStandardMaterial) => THREE.Material | Drawn;

const families = new Map<string, Family>();

/** Registers a family for every kind, or for the entities of one kind only (which then wins for that kind). */
export function registerFamily(name: string, family: Family, kind?: string) {
  families.set(kind ? `${kind}/${name}` : name, family);
}

let store = '';
const textures = new Map<string, THREE.Texture>();
const pictures = new THREE.TextureLoader();

/** Sets where the object store is served (the location loader does, before it loads a mesh). */
export function storeRoot(href: string) {
  store = href;
}

/** The picture a material names by its store url (`objects/<aa>/<sha256>.png`, from the pipeline's `Output.images`):
 *  one texture per url, sRGB, rows from the top as glTF texture coordinates run (`flipY` false). */
export function storeTexture(url: string): THREE.Texture {
  let texture = textures.get(url);
  if (!texture) {
    texture = pictures.load(new URL(url, store || new URL('world/', location.href)).href);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.flipY = false;
    texture.anisotropy = 16;
    textures.set(url, texture);
  }
  return texture;
}

export function familyOf(material: THREE.Material): FamilyReference | null {
  const reference = (material.userData as { turku?: FamilyReference }).turku;
  return reference && typeof reference.family === 'string' ? reference : null;
}

/** What a loaded glTF material is drawn with; identical references share one entry per `cache`. */
export function familyDrawn(source: THREE.Material, cache: Map<string, Drawn>, kind = ''): Drawn {
  const reference = familyOf(source);
  const standard = source as THREE.MeshStandardMaterial;
  const key = JSON.stringify([kind, reference, standard.color?.getHex(), standard.roughness, standard.metalness, source.side]);
  let drawn = cache.get(key);
  if (!drawn) {
    const family = reference && (families.get(`${kind}/${reference.family}`) ?? families.get(reference.family));
    const made = family ? family(reference!.params ?? {}, standard) : source;
    drawn = (made as THREE.Material).isMaterial ? { material: made as THREE.Material } : made as Drawn;
    // A material of one parameter set is named after it; one that draws many sets keeps its family's name.
    if (!drawn.vertex) {
      const material = drawn.material;
      material.userData = { ...material.userData, turku: reference };
      // A family nobody registered is handed to the game's own material pass under its name: that pass already
      // gives asphalt, granite, setts and cobbles, grass and water their proven surfaces (render/materials.ts).
      if (!family && reference) material.userData.material_key = reference.family;
      material.name = reference?.family ?? source.name;
    }
    cache.set(key, drawn);
  }
  return drawn;
}

/** Writes a parameter set's vertex value on every vertex of a geometry drawn with it (nothing for other sets). */
export function markVertices(geometry: THREE.BufferGeometry, vertex: Drawn['vertex']) {
  if (vertex) geometry.setAttribute(vertex.name, new THREE.BufferAttribute(new Float32Array(geometry.attributes.position.count).fill(vertex.value), 1));
}

/** Gives a loaded mesh what its glTF material is drawn with: the material and its vertex marks. */
export function dress(mesh: THREE.Mesh, cache: Map<string, Drawn>, kind = '') {
  if (Array.isArray(mesh.material)) throw new Error(`Mesh ${mesh.name} has one material per group: the families draw one per mesh`);
  const drawn = familyDrawn(mesh.material, cache, kind);
  mesh.material = drawn.material;
  if (!drawn.vertex) return;
  // A geometry two materials draw (glTF shares a primitive's buffers) is marked once for each.
  const marked = mesh.geometry.getAttribute(drawn.vertex.name);
  if (marked && marked.getX(0) !== drawn.vertex.value) mesh.geometry = mesh.geometry.clone();
  if (!marked || marked.getX(0) !== drawn.vertex.value) markVertices(mesh.geometry, drawn.vertex);
}
