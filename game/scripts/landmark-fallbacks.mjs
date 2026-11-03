#!/usr/bin/env node
// Refreshes the `fallback` of each curated landmark (src/gameplay/content/landmarks.json, used by landmarks.ts) from
// a served /coarse/navigation.json: the navigation build's drop-off and city-wide route metres from the pizzeria.
// Usage: curl -o /tmp/navigation.json https://<coarse host>/coarse/navigation.json
//        node game/scripts/landmark-fallbacks.mjs /tmp/navigation.json
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const [navigation] = process.argv.slice(2);
if (!navigation) throw new Error('Usage: landmark-fallbacks.mjs NAVIGATION.json');
const file = new URL('../src/gameplay/content/landmarks.json', import.meta.url);
const published = new Map(JSON.parse(fs.readFileSync(navigation, 'utf8')).addresses.addresses
  .filter(a => a.landmark).map(a => [a.address, a]));
const round = v => Math.round(v * 100) / 100;
const landmarks = JSON.parse(fs.readFileSync(file, 'utf8')).map(({ fallback, ...landmark }) => {
  const a = published.get(landmark.name);
  if (!a) console.warn(`${landmark.name}: not in the navigation pool; fallback ${fallback ? 'kept' : 'missing'}`);
  return a ? { ...landmark, fallback: { point: a.point, street: a.street.map(round), foot: a.foot, car: a.car } }
    : { ...landmark, ...fallback ? { fallback } : {} };
});
fs.writeFileSync(file, JSON.stringify(landmarks, null, 2) + '\n');
console.log(`${fileURLToPath(file)}: ${landmarks.filter(l => l.fallback).length}/${landmarks.length} fallbacks`);
