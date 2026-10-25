#!/usr/bin/env node
// Render the game from the poses in a view registry (qa/views/views.jsonl) via the QA URL contract
// (qa/README.md). For each view it writes, into --out:
//   <id>.png            colour pass at the reference's exact width x height
//   <id>.id.png         object-ID pass (?pass=id), unless --pass color
//   <id>.id.json        window.__qaIdTable (id -> {kind, id, tile, material})
//   <id>.meta.json      URL, pose echo, renderer info, console errors, timings
// and <out>/render-summary.json. Exit code 1 if any view failed.
//
// Usage (from qa/render, after `npm install`):
//   node render.mjs --views ../views/views.jsonl --url http://localhost:$TURKU3D_PORT_BASE/ --out "$TURKU3D_WORK/qa/runs/R/renders"
//   node render.mjs --views ... --serve stub --out "$TURKU3D_WORK/qa/stub-renders" # test against the contract stub
// Options: --ids a,b  --pass color|id|both (default both)  --backend webgpu|webgl  --timeout 120000
//          --channel chrome|chromium (default chromium)  --headed  --extra "k=v&k2=v2"
import { chromium } from 'playwright';
import { port, isolateBrowser } from '../../tools/dev/environment.mjs';
isolateBrowser();
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

