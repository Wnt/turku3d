// Material families of the world pipeline (docs/design/world-pipeline.md, section 3.3).
//
// A compiled mesh names its materials by family and parameters (glTF `material.extras.turku`) and carries a plain
// PBR fallback. A family registered here turns that reference into the material the renderer draws; a family
// nobody registered draws its fallback. Tracks add their families with `registerFamily`.
import * as THREE from 'three/webgpu';

export interface FamilyReference { family: string; params: Record<string, unknown> }
/** Builds the drawn material of one family. `fallback` is the glTF material (base colour, roughness, metalness). */
export type Family = (params: Record<string, unknown>, fallback: THREE.MeshStandardMaterial) => THREE.Material;

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

/** The drawn material for a loaded glTF material; identical references share one material per `cache`. */
export function familyMaterial(source: THREE.Material, cache: Map<string, THREE.Material>, kind = ''): THREE.Material {
  const reference = familyOf(source);
  const standard = source as THREE.MeshStandardMaterial;
  const key = JSON.stringify([kind, reference, standard.color?.getHex(), standard.roughness, standard.metalness, source.side]);
  let drawn = cache.get(key);
  if (!drawn) {
    const family = reference && (families.get(`${kind}/${reference.family}`) ?? families.get(reference.family));
    drawn = family ? family(reference!.params ?? {}, standard) : source;
    drawn.userData = { ...drawn.userData, turku: reference };
    // A family nobody registered is handed to the game's own material pass under its name: that pass already
    // gives asphalt, granite, setts and cobbles, grass and water their proven surfaces (render/materials.ts).
    if (!family && reference) drawn.userData.material_key = reference.family;
    drawn.name = reference?.family ?? source.name;
    cache.set(key, drawn);
  }
  return drawn;
}
