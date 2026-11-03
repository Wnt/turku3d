import { Material } from 'three/webgpu';

/** Names and UUIDs label a source; only render inputs select its shared material/program. */
export function materialIdentity(material: Material): string {
  const json = material.toJSON({ textures: {}, images: {}, geometries: {}, materials: {}, shapes: {}, skeletons: {}, animations: {}, nodes: {} }) as unknown as Record<string, unknown>;
  for (const key of ['uuid', 'metadata', 'name']) delete json[key];
  // Material.toJSON quantizes/clamps colours to 8-bit hex; shader uniforms retain working-space floats.
  for (const [key, value] of Object.entries(material)) if (value?.isColor) json[key] = value.toArray();
  // Callbacks are not serialized. Keep sources with private shader/render hooks isolated.
  const privateHooks = material.onBeforeCompile !== Material.prototype.onBeforeCompile
    || material.onBeforeRender !== Material.prototype.onBeforeRender
    || material.customProgramCacheKey !== Material.prototype.customProgramCacheKey;
  return `${privateHooks ? material.uuid + ':' : ''}${JSON.stringify(json)}`;
}
