import { Material } from 'three/webgpu';

/** Answers every texture lookup of `toJSON` as already serialised: a material's JSON names its textures by uuid
 *  only, so the identity is unchanged, and three never serialises their images (a PNG data URL per ImageBitmap, and a
 *  "THREE.Texture: Unable to serialize Texture." warning per compressed texture, on every identity). */
const knownTextures = () => new Proxy({} as Record<string, { uuid: string }>, {
  get: (_target, key) => typeof key === 'string' ? { uuid: key } : undefined,
});

/** Names and UUIDs label a source; only render inputs select its shared material/program. */
export function materialIdentity(material: Material): string {
  const json = material.toJSON({ textures: knownTextures() as never, images: {}, geometries: {}, materials: {}, shapes: {}, skeletons: {}, animations: {}, nodes: {} }) as unknown as Record<string, unknown>;
  for (const key of ['uuid', 'metadata', 'name']) delete json[key];
  // Material.toJSON quantizes/clamps colours to 8-bit hex; shader uniforms retain working-space floats.
  for (const [key, value] of Object.entries(material)) if (value?.isColor) json[key] = value.toArray();
  // Callbacks are not serialized. Keep sources with private shader/render hooks isolated.
  const privateHooks = material.onBeforeCompile !== Material.prototype.onBeforeCompile
    || material.onBeforeRender !== Material.prototype.onBeforeRender
    || material.customProgramCacheKey !== Material.prototype.customProgramCacheKey;
  return `${privateHooks ? material.uuid + ':' : ''}${JSON.stringify(json)}`;
}
