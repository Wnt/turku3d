# Notice: third-party data and assets

The code in this repository is under the MIT licence in [LICENSE](LICENSE). The repository holds no bulk
data; the pipeline downloads it from the sources below, and the published game
(https://wnt.github.io/turku-game/) is built from them.

## Data

| Source | Used for | Licence |
|---|---|---|
| City of Turku open data (Turun kaupunki): WFS base map layers (building footprints, CityGML LoD2/LoD3 buildings, street areas and centrelines, curbs, stairs, walls, trees, street furniture, traffic signs, water) | Buildings, streets, curbs, stairs, props, vegetation placement | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), © City of Turku, [source](https://www.turku.fi/tietoa-turusta/kartat-ja-paikkatieto) |
| City of Turku 2021 lidar point cloud | Terrain and building heights | CC BY 4.0, © City of Turku |
| City of Turku aerial imagery: 2022 true orthophoto and 2025 orthophoto | Ground textures; crops in `docs/` | CC BY 4.0, © City of Turku |
| OpenStreetMap | Street network, crossings and road markings | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/), © OpenStreetMap contributors |

Street-level imagery (Google Street View, Apple Look Around, Mapillary) is used only locally as a reference
for texture generation and is not part of this repository or the published game.

## Software and assets

| Component | Licence |
|---|---|
| three.js | MIT |
| Rapier physics (`@dimforge/rapier3d`) | Apache-2.0 |
| 3DTilesRendererJS (`3d-tiles-renderer`) | Apache-2.0 |
| Basis Universal transcoder (`game/public/basis/`, from three.js) | Apache-2.0 |
| Kokoro-82M text-to-speech model, used offline to synthesise the voice clips (`audio/voice/`) | Apache-2.0 |
| Kenney Car Kit and Nature Kit (`game/public/assets/car/`, `pipeline/turku3d/parked/assets/`, `pipeline/turku3d/vegetation/assets/`) | CC0 1.0 |
| MakeHuman-based player and Quaternius Universal Animation Library (`game/public/assets/character/`) | CC0 1.0 |
| Sound effects (`game/public/assets/audio/`) | Original procedural audio, CC0 1.0 |
| Wikimedia Commons reference photos in `docs/research/ai-textures/` (`ref_facade_*.jpg` and images derived from them) | CC0 1.0 |

Per-asset details: [`game/public/assets/LICENSES.md`](game/public/assets/LICENSES.md) and the licence files next to
each asset.

## Project-generated assets

The AI-generated facade, ground and material textures produced by the pipeline, the menu art in
`game/public/assets/ui/` and the synthesised voice clips in `game/public/assets/voice/` are part of this project.
