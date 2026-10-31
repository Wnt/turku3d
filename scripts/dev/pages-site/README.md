# Turku3D — playable build

**[Play Turku3D]({{url}})**

Turku3D is an experimental open-world game set in Turku, Finland. In an AI-disrupted 2027, become a pizza courier, explore the city on foot or by vehicle, and compete with other players for hourly delivery scores.

This is the **generated GitHub Pages deployment repository**. The actual game, server and geographic data pipeline source code live in **[Wnt/turku3d](https://github.com/Wnt/turku3d)**. Changes belong there, not in this deploy-only branch.

## Start and play

Use **desktop Google Chrome** for the recommended WebGPU experience. Add `?backend=webgl` to the game URL for WebGL2. **Firefox is unsupported** and displays a warning. An Xbox-style controller or keyboard and mouse work; touch support also exists.

The **public release starts in Multiplayer mode**; local development builds additionally provide solo save/load menus. The first load and newly visited areas can take time: Pages serves the browser application, while a separate service generates and streams the world on demand. This is a playable prototype, not a perfect digital twin.

| Action | Keyboard / mouse | Xbox-style controller |
| --- | --- | --- |
| Move / steer | WASD | Left stick |
| Look | Click canvas, move mouse | Right stick |
| Sprint / jump | Shift / Space | A / X |
| Mission / shop interaction | E | A |
| Enter / exit vehicle | F | Y |
| Accelerate / brake | W / S | RT / LT |
| Map / pause | M / Escape | View / Menu |

See the source repository's [complete player guide](https://github.com/Wnt/turku3d/blob/main/docs/PLAYING.md) and [development guide](https://github.com/Wnt/turku3d/blob/main/docs/DEVELOPING.md).

## Data and licences

World geography uses City of Turku open data and OpenStreetMap. Facade and other visual assets can be stylized or AI-generated; they are not a precise depiction of every building. See [ATTRIBUTION.md](ATTRIBUTION.md) and the source repository's [NOTICE.md](https://github.com/Wnt/turku3d/blob/main/NOTICE.md).
