// node --test tools/dev/serve-dist.test.mjs
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import { port } from './environment.mjs';

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'serve-dist-'));
const tiles = fs.mkdtempSync(path.join(os.tmpdir(), 'serve-tiles-'));
fs.writeFileSync(path.join(dir, 'index.html'), '<!doctype html><title>x</title>');
const big = Buffer.alloc(100000, 7); big.write('TCOL', 0);
fs.mkdirSync(path.join(tiles, 'collision'));
fs.writeFileSync(path.join(tiles, 'collision', 'c_0_0.col'), big);
