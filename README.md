# Turku3D

**Turku, 2027. AI took your software job. The city still needs pizza.**

Turku3D is an experimental open-world browser game set in Turku, Finland. Explore the Aura riverside on foot or by vehicle, deliver pizzas to pay for a courier permit, make dubious deals, and compete with other couriers in a shared city. The world is assembled from real geographic data, while its look is intentionally game-like rather than a 1:1 digital twin.

**[▶ Play Turku3D](https://wnt.github.io/turku-game/)** · [How to play](docs/PLAYING.md) · [Development](docs/DEVELOPING.md) · [Documentation index](docs/README.md)

> **Browser:** Desktop **Google Chrome** is the recommended experience. WebGPU is the primary renderer; WebGL2 is available with `?backend=webgl`. **Firefox is not supported** (the game warns before starting). Touch controls exist, but desktop play is the primary target. The world is streamed as you explore, so initial loads and new areas may take time.

## The game

- **A recognizable Turku.** Walk and drive around the city centre and the Aura river, using streets, terrain, buildings and landmarks derived from open city data and OpenStreetMap. Geometry and presentation are still evolving.
- **A questionable career change.** Take pizza delivery jobs, rent permits, earn time-based tips, and visit the pizzeria, black market and vehicle dealer. Progress from walking to a scooter, car or trophy truck.
- **Shared streets.** See other players and their vehicles, join the live multiplayer room, and compete on the **hourly pizza-delivery scoreboard** (Europe/Helsinki hours).
- **A physical world.** Third-person movement, drivable vehicles, curb and stair collision, changing daylight, ambient audio and a minimap with route guidance.
- **Built in the open.** A three.js/TypeScript browser client, Rapier WASM physics, a Python geospatial pipeline, and a Node.js multiplayer server.

The **public GitHub Pages release currently starts in multiplayer mode only**. Local development builds also expose solo play, save/load slots and developer tools. This is an actively developed playable prototype, not a finished or geographically exact simulation.

## Quick controls

| Action | Keyboard / mouse | Xbox-style controller |
| --- | --- | --- |
| Move / steer | WASD or arrows | Left stick |
| Look | Click the game canvas, then move mouse | Right stick |
| Sprint / jump | Shift / Space (on foot) | A / X |
| Interact with mission or shop | E | A |
| Enter / exit vehicle | F | Y |
| Accelerate / brake / reverse | W / S | RT / LT |
| Handbrake | Space (driving) | RB |
| Map / pause | M / Esc | View / Menu |

See [the full player guide](docs/PLAYING.md) for getting started, more controls, the mission economy, saves, multiplayer and troubleshooting.

## Local development

For the **browser client and synthetic test scene**, start at the repository root:

```sh
npm ci --prefix game
make game-dev
# Open http://localhost:5170/?scene=dev
```

`make game-dev` starts Vite and the local coarse-tile service; it does **not** download the complete Turku source dataset. The production Turku world needs separately provisioned geographic inputs and the generator. To run focused checks:

```sh
make game-test
make game-build
npm ci --prefix server --ignore-scripts
npm test --prefix server
```

See [development setup](docs/DEVELOPING.md), [game details](game/README.md), [server and protocol](server/README.md), and [the geospatial architecture](docs/ARCHITECTURE.md). For the source-to-production workflow see [developer tooling](scripts/dev/README.md).

## How it works

The browser client is published to GitHub Pages in the **separate, deploy-only** [Wnt/turku-game](https://github.com/Wnt/turku-game) repository. World content is generated and delivered on demand by a separate host; the multiplayer WebSocket service handles player presence, shared vehicles and scores. The data pipeline and all source code live **here**, not in the deploy repository.

The project uses City of Turku open building, terrain/lidar, street and aerial datasets, plus OpenStreetMap. Some facade, material and UI art is AI-generated. [Attribution and third-party licences](NOTICE.md) · [MIT licence for repository code](LICENSE).

**Interested in helping?** See [CONTRIBUTING.md](CONTRIBUTING.md) or [open an issue](https://github.com/Wnt/turku3d/issues). For dated technical measurements and known gaps, start at the [documentation index](docs/README.md); older reports are historical evidence, not promises about the live build.
