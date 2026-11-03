# Attribution and licences

## Data

| Source | Used for | Licence |
|---|---|---|
| City of Turku open data (Turun kaupunki): WFS base map layers (building footprints, CityGML LoD2/LoD3 buildings, street areas and centrelines, curbs, stairs, walls, trees, street furniture, traffic signs, water) | Buildings, streets, curbs, stairs, props, vegetation placement | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), © City of Turku, [source](https://www.turku.fi/tietoa-turusta/kartat-ja-paikkatieto); catalogue: [city model](https://avoindata.suomi.fi/data/en_GB/dataset/turun-kaupungin-kaupunkitietomalli), [base map](https://avoindata.suomi.fi/data/en_GB/dataset/turun-kantakartta), [street areas](https://avoindata.suomi.fi/data/en_GB/dataset/turun-ja-kaarinan-yllapitamat-katualueet), [traffic signs](https://avoindata.suomi.fi/data/en_GB/dataset/turun-kaupungin-liikennemerkit) |
| City of Turku 2021 lidar point cloud and ground points (part of the city model) | Terrain and building heights | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), © City of Turku, [catalogue](https://avoindata.suomi.fi/data/en_GB/dataset/turun-kaupungin-kaupunkitietomalli) |
| City of Turku aerial imagery: 2022 true orthophoto and 2025 orthophoto | Ground classification and textures | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), © City of Turku, catalogue: [2022](https://avoindata.suomi.fi/data/en_GB/dataset/turun-ilmakuva-2022), [regional](https://avoindata.suomi.fi/data/en_GB/dataset/turun-seudun-ilmakuva) |
| OpenStreetMap | Street network and road graph, crossings and road markings, kerbs, bridges, tunnels, fences and walls, trees, signs, building levels, landmarks | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/), © OpenStreetMap contributors, [copyright](https://www.openstreetmap.org/copyright) |

The game world is derived from these sources. The data was resampled, classified, clipped, meshed and
textured for the game, and the city data was merged with OpenStreetMap. No street-level photography
(Google Street View, Apple Look Around, Mapillary) is published here.

The game's tiles are Produced Works under the ODbL. The street graphs the game downloads (`roads.json`
in each tile) and other OpenStreetMap-derived data inside the tiles are derivative databases of
OpenStreetMap. They are available under [ODbL 1.0](https://opendatacommons.org/licenses/odbl/), regardless
of the licence of the surrounding code and assets. The public source repository
([Wnt/turku3d](https://github.com/Wnt/turku3d)) contains the pipeline that builds them from a dated Geofabrik Finland extract.

## Software and assets

| Component | Licence |
|---|---|
| [three.js](https://threejs.org/) | MIT |
| [Rapier](https://rapier.rs/) physics | Apache-2.0 |
| [3DTilesRendererJS](https://github.com/NASA-AMMOS/3DTilesRendererJS) | Apache-2.0 |
| Basis Universal transcoder (via three.js) | Apache-2.0 |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) text-to-speech model, used offline to synthesise the voice clips | Apache-2.0 |
| Kenney Car Kit and Nature Kit models (car, parked cars, trees) | CC0 1.0 |
| MakeHuman-based player character, Quaternius Universal Animation Library | CC0 1.0 |
| Sound effects | Original procedural audio, CC0 1.0 |

## Generated content

Facade, ground and building material textures, sign faces and menu art are AI-generated or
procedurally drawn for this project; none is cut from street-level photographs. The game code and
the generated assets are © the author, all rights reserved unless a licence file says otherwise.
