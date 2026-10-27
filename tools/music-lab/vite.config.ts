import { defineConfig } from 'vite';
const port = Number(process.env.TURKU3D_PORT_BASE || 5170) + 3;
export default defineConfig({ server: { port, fs: { allow: ['../..'] } }, build: { emptyOutDir: true, outDir: process.env.TURKU3D_WORK ? `${process.env.TURKU3D_WORK}/dist/music-lab` : 'dist' } });
