import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import type { Options } from './validate.ts';

/** Layer manifests advertise validation sidecars; conventional names support pre-assembler layers. */
export function resolveInputs(options: Options): Options {
  if (options.metadata) return options;
  const metadata: any = { footprints: { features: [] }, curbs: { features: [] }, stairs: [], sources: [] };
  const paths: Record<string, Set<string>> = { footprints: new Set(), curbs: new Set(), stairs: new Set() };
  if (options.layers) {
    if (!existsSync(options.layers)) throw new Error(`layer directory does not exist: ${options.layers}`);
    for (const entry of readdirSync(options.layers, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const dir = join(options.layers, entry.name), manifest = join(dir, 'MANIFEST.json');
      const refs = existsSync(manifest) ? JSON.parse(readFileSync(manifest, 'utf8')).validation ?? {} : {};
      for (const key of ['footprints', 'curbs', 'stairs']) {
        const conventional = join(dir, key + (key === 'stairs' ? '.json' : '.geojson'));
        if (refs[key]) paths[key].add(resolve(dirname(manifest), refs[key]));
        else if (existsSync(conventional)) paths[key].add(conventional);
      }
    }
  }
  for (const key of ['footprints', 'curbs', 'stairs'] as const) {
    if (options[key]) paths[key] = new Set([resolve(options[key])]);
    for (const path of paths[key]) {
      const data = JSON.parse(readFileSync(path, 'utf8'));
      if (key === 'stairs') {
        const flights = Array.isArray(data) ? data : data.flights;
        if (!Array.isArray(flights)) throw new Error(`${path}: expected stair flight array`);
        for (const f of flights) {
          if (!f.id || ![f.bottom, f.top].every(p => Array.isArray(p) && p.length === 3 && p.every(Number.isFinite)) || !(f.width > 0))
            throw new Error(`${path}: stair requires id, bottom/top [x,y,z], width > 0`);
        }
        metadata.stairs.push(...flights);
      } else {
        if (data.type !== 'FeatureCollection' || !Array.isArray(data.features)) throw new Error(`${path}: expected GeoJSON FeatureCollection`);
        const mode = data.turku3d_coords ?? 'engine-xz';
        if (metadata[key].turku3d_coords && metadata[key].turku3d_coords !== mode) throw new Error(`mixed coordinate conventions for ${key}`);
        metadata[key].turku3d_coords = mode;
        metadata[key].features.push(...data.features);
      }
      metadata.sources.push({ kind: key, path });
    }
  }
  return { ...options, metadata };
}
