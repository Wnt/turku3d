# Turku Music Lab

From the main checkout, first create or use a sandbox and source its `sandbox.env`.
The app imports only the procedural engine source from `game/src/audio/music`; it does
not build or load the game. No runtime service or audio download is required.

```sh
source ~/turku3d/wt/<sandbox>/sandbox.env
cd ~/turku3d/wt/<sandbox>/repo/tools/music-lab
npm ci
npm run dev
```

Open `http://localhost:$((TURKU3D_PORT_BASE + 3))` (for the main checkout's
5170 base, `http://localhost:5173`). Press **Start audio** to unlock the browser
AudioContext. Change track, state, deadline or car/shop muffling. The simulation
runs the deadline over the selected seconds. Sliders persist in `localStorage`;
**Copy as JSON** exports the complete `MusicParameters` object for use with
`setParameters`. Run `npm run build` or `npm run test:e2e` in this folder.
