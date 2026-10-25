// Contract tests against the stub page: URL building, pose echo, viewport size, ID pass, projection.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { port } from '../../../tools/dev/environment.mjs';
import { contractUrl, parseArgs, main } from '../render.mjs';

let browser;
before(async () => {
  browser = await chromium.launch({ channel: 'chrome' });
