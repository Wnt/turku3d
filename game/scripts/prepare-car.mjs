// Embed Kenney's external palette texture; preserve authored geometry and wheel nodes.
// Usage: node scripts/prepare-car.mjs path/to/Models/GLB\ format/sedan.glb public/assets/car/sedan.glb
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
const [source, destination] = process.argv.slice(2);
if (!source || !destination) throw new Error('Usage: prepare-car.mjs <source sedan.glb> <output.glb>');
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
const doc = await io.read(source);
await io.write(destination, doc);
console.log(`${destination}: sha256 ${createHash('sha256').update(await readFile(destination)).digest('hex')}`);
