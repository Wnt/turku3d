// Same acceptance budgets as render-perf, served from the final dev source without a production build.
import { defineConfig } from '@playwright/test';
import base from './playwright.render.config';
import { port } from '../tools/dev/environment.mjs';

const PORT = Number(process.env.E2E_PORT ?? port(1));
export default defineConfig({ ...base,
  webServer: { ...base.webServer, command: `./node_modules/.bin/vite --host 127.0.0.1 --port ${PORT} --strictPort` },
});
