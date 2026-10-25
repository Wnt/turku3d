import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { port, reportsRoot, isolateBrowser, gameDist, tileDist } from '../tools/dev/environment.mjs';

isolateBrowser();

// Phase 0 exit check (`make tile`): the built game (dist/) served by tools/dev/serve-dist.mjs with the local tile
