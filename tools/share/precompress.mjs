#!/usr/bin/env node
// node tools/share/precompress.mjs DIST: brotli sidecars (<file>.br) for the dist's big compressible files, written at
// deploy (tools/share/deploy.sh) so city-web serves them as they are instead of compressing on a player's request.
import { precompress } from '../dev/http.mjs';

const dir = process.argv[2];
if (!dir) { console.error('usage: precompress.mjs DIST'); process.exit(2); }
const t0 = performance.now();
// Every compressible file of 1 KB or more: the 126 small car LOD GLBs (lod1/lod2, < 256 KB each) went out
// uncompressed on a first visit (13.7 MB, 9.6 MB as brotli; loadsize probe 2026-11-10).
const d = await precompress(dir, { minBytes: 1024 });
console.log(`precompressed ${d.files} files, ${(d.bytesIn / 1e6).toFixed(1)} -> ${(d.bytesOut / 1e6).toFixed(1)} MB in ${((performance.now() - t0) / 1000).toFixed(1)} s`);
