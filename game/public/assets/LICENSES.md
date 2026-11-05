# Third-party assets in game/public/assets

| File | Source | Licence | Notes |
|---|---|---|---|
| `character/player.glb` | MakeHuman assets exported by [Kasper Olesen / CharacterCreator](https://github.com/kblood/CharacterCreator/tree/852e48f316b90a0937234886630e4fb4cdfd1f78/output), plus Quaternius *Universal Animation Library [Standard]* | **CC0 1.0** | Textured autumn player; offline skin retarget to UAL, KTX2 and meshopt. [Licence, provenance and exact rebuild](character/README.md). |
| `walkers/source/hair_short02_swept_m*.glb` | CC0 CharacterCreator `hair_short02`, edited in Blender through Blenderwright MCP; original Turku3D lattice deformation | **CC0 1.0** | Swept short crop, fitted to two male recipes. Photo references are local only; no photo pixels are used. |
| `walkers/walkers.bin`, `walkers/wardrobe.png` | Pinned CharacterCreator MakeHuman body, clothing, hair and textures; Quaternius UAL rig; original Turku3D parametric bags/caps | **CC0 1.0** source assets; original accessory geometry | 96 anatomical/wardrobe recipes, shared texture atlas and two mesh LODs. [Provenance and reproducible build](walkers/README.md). |
| Runtime Finnish fleet (`src/actors/fleet/`) | Original parametric geometry and surface details authored for Turku3D | Project-owned | 56 fictional models, three LODs, shared materials; no external vehicle geometry, logos or reference pixels. [Implementation and provenance](../../src/actors/fleet/README.md). |
| `car/sedan.glb` | Kenney, *Car Kit 3.1*, `Models/GLB format/sedan.glb` + its `Textures/colormap.png` from [Car Kit](https://kenney.nl/assets/car-kit) | **CC0 1.0** ([original license](car/KENNEY-LICENSE.txt)) | Embedded palette with `scripts/prepare-car.mjs`; body/wheel transforms adapted at runtime to the physics dimensions. Separate authored wheel meshes follow suspension, steering and rotation. |
| `vehicles/scooter.glb` | "Scooter" by MiniPoly, [poly.pizza/m/3vLV1QtLFP](https://poly.pizza/m/3vLV1QtLFP) | **CC BY 3.0** ([licence](https://creativecommons.org/licenses/by/3.0/)) | Attribution: "Scooter" by MiniPoly, CC BY 3.0, modified for Turku3D. Baked to the physics frame and matte material with `scripts/prepare-scooter.mjs`; [source, hashes and rebuild](vehicles/README.md). |
| `nature/nature.glb` | Generated for this project by `scripts/build-nature-assets.mjs` (trees, shrubs, hedge, grass, ground cover, impostors) and modelled in Blender through Blender MCP (fence kit). Texture inputs: [ambientCG](https://ambientcg.com) LeafSet002/004/005/010/013/016/019/020/023/024, PineNeedles001, Foliage001/003/006, Bark002/004/009/010, Granite003B (ambientCG, Lennart Demes); [Poly Haven](https://polyhaven.com) pine_bark (Dimitrios Savva), jolcham_oak_bark_01 (Charlotte Baglioni), bark_brown_02 (Rob Tuytel), bark_willow (Dario Barresi, Dimitrios Savva) | **CC0 1.0** inputs; generated geometry and compositions project-owned | Leaf, needle and grass cards composed from cut-out leaf scans; barks recalibrated, birch bark redrawn; impostors baked from our meshes; KTX2 and meshopt. [Sources, hashes, changes and rebuild](nature/README.md). |
| `ui/*.webp` | Generated for this project with the Codex image runner (`gpt-6-astra`), 2026-10-27; `bg_intro.webp` uses the built-in image model with `bg_pizzeria.webp` as a style reference; `icon_scooter.webp` and `icon_truck.webp` are hand-drawn (`textures/ui-art/icon_scooter.svg`, `icon_truck.svg`) | Project-owned | Shop and intro backgrounds and icons; prompts, job ids and the rebuild in [`src/ui/README.md`](../../src/ui/README.md). Not third-party. |

Car source archive: `https://kenney.nl/media/pages/assets/car-kit/1a312ec241-1775131960/kenney_car-kit.zip`, downloaded 2026-10-25,
SHA-256 `fac7dacac5c7874348cf19729af3ef205f3d366493edaf0a827d93f4fdf3d0c4`.
Embedded sedan SHA-256 `f867bdd69399bbcf68cdfbd02d5c98c9b95ed965875029656c359baff90b741f`.
Rebuild from the extracted archive (run in `game/`):

```bash
node scripts/prepare-car.mjs '/path/to/Car Kit/Models/GLB format/sedan.glb' public/assets/car/sedan.glb
```

`../basis/basis_transcoder.{js,wasm}` are copied from `three/examples/jsm/libs/basis` (three 0.186.1, Apache-2.0, Binomial LLC).

`Walk_Formal_Loop` was added from the same pinned archive for the slower stair gait (2026-10-25).
The archive's 46 named clips were inspected: none is a dedicated stair up/down clip. Stair states therefore use
procedural swing clearance and two-bone foot IK against query treads. Publisher CC0 declaration:
https://quaternius.com/packs/universalanimationlibrary.html . No paid animation pack is used.

UAL source archive: https://opengameart.org/sites/default/files/universal_animation_librarystandard.zip
(page: https://opengameart.org/content/universal-animation-library), downloaded 2026-10-25.
Archive SHA-256: `18ff1a7215f4852b320203e8aaf02a1578b5c8eef9027fbaedfcedc7b85a3ac2`.
The pinned trimmed input is recoverable from commit `2d75e2c56c2db4a011f4197fe16f4367c494e0be`;
the old mannequin is no longer shipped.

## KORPI 440 fictional off-road racer

Original parametric geometry and painted artwork in `src/actors/prerunner/` (2026).
Copyright 2026 Jonni Madekivi; MIT, as the repository. No downloaded vehicle mesh,
commercial-game asset, photograph, badge or sponsor artwork is included. Local KOH
photographic references stay outside the repository under `data/research/prerunner/`.

## Vire estate — original Blender fleet

`cars/aava-estate/estate_lod{0,1,2}.glb`: original fictional estate model authored
with Blenderwright in Blender 5.2 (2026). Copyright 2026 Jonni Madekivi; MIT,
as the repository. Fictional diamond badge and TKU-247 plate. No downloaded mesh
or photographic pixels are included. The local reference plane is excluded from exports.
Editable sources: `data/assets-src/cars/estate/estate.blend` and `estate-lods.blend`.
Run `node game/scripts/prepare-fleet.mjs <source-directory> <output-directory>`
to pack the three exported LODs with meshopt.

## Octavia IV Combi study — original Blender fleet prototype

`cars/octavia-combi-4/lod{0,1,2}.glb`: original geometry authored through
Blenderwright in Blender 5.2 (2026). Copyright 2026 Jonni Madekivi; MIT,
as the repository. Fictional diamond badge and VIR-268 plate. No downloaded
mesh, photographic pixels or manufacturer artwork is included. Dimensions and
vehicle specifications cite their factual sources in `spec.json`. Editable local
source: `data/assets-src/cars/octavia-combi/octavia-cage.blend`; build settings
in the adjacent `model.json`. Separate hub-centred wheels and named lamp materials
are preserved in all three meshopt LODs. This model remains a visual prototype.

## Regional horizon terrain

Generated outer terrain uses [Copernicus DEM GLO-30](https://registry.opendata.aws/copernicus-dem/)
under its [free and open licence](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/DEM/resources/license/License-COPDEM-30.pdf),
and [ESA WorldCover 2021 v200](https://esa-worldcover.org/en/data-access) under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Data accessed 2026-11-02.
Only elevation and land-cover classes become geometry/vertex pigments; no satellite imagery is rendered.
Regional resampling, ocean flattening and mesh simplification modify the source products.

© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021)
processed by ESA WorldCover consortium. Copernicus DEM: European Union, produced
by Airbus Defence and Space and DLR. The immutable grid's `sources.json` records
source URLs, transformations and the SHA-256; inputs and generated meshes stay outside Git.

produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

The organisations in charge of the Copernicus programme by law or by delegation do not incur any liability for any use of the Copernicus WorldDEM-30.

## Model Y crossover study — original Blender fleet prototype

`cars/model-y/lod{0,1,2}.glb`: original geometry authored through parameterised
Blenderwright tools in Blender 5.2 (2026). Copyright 2026 Jonni Madekivi; MIT,
as the repository. Fictional Vire badge and VIR-519 plate. No downloaded mesh,
reference photographs or manufacturer artwork is included. Public dimensional
and driving specification sources are recorded in `spec.json`; estimated power
and torque are identified there. Editable local source and build settings live
under `data/assets-src/cars/model-y/`. Four hub-centred wheels and four named
lamp material roles are preserved. This model remains a visual prototype.

## RAV4 XA50 study — original Blender fleet model

`cars/rav4-xa50/lod{0,1,2}.glb`: original geometry authored through parameterised
Blenderwright tools in Blender 5.2 (2026). Copyright 2026 Jonni Madekivi; MIT,
as the repository. Fictional Vire Ranta H4 badge and plate artwork. No downloaded
mesh, reference photographs or manufacturer artwork is included. Public vehicle
specifications and their manufacturer source are recorded in `spec.json`.
Editable local source: `data/assets-src/cars/rav4-xa50/rav4-delivery.blend`;
reproducible MCP build settings: adjacent `model.json`. Four hub-centred wheel
objects and four front/tail lamp material roles survive all three meshopt LODs.
Included under Jonni's explicit approval on 2026-11-02.

## Converted CC BY 4.0 cars (`cars/<id>/`)

Converted on 2026-11-05 from Sketchfab downloads with `game/scripts/import-fleet-gltf.mjs` and
`tools/dev/fleet-import/models.json` (rebuild: `node scripts/import-fleet-gltf.mjs ../tools/dev/fleet-import/models.json <id>` in `game/`).
Sources, with each model page's licence text as read at download time (`LICENSE.txt`, `sketchfab-record.json`), stay outside git in
`~/turku3d/loops/carmodels/<id>/`. Brand names appear only to credit the source models; the game uses fictional names.

| File | Source | Licence | Notes |
|---|---|---|---|
| `cars/corolla-e180/lod{0,1,2}.glb` | "2014 Toyota Corolla E180 EU (with interior)" (https://sketchfab.com/3d-models/2014-toyota-corolla-e180-eu-with-interior-36f95efb0585464cae43a25a3b3392e8) by Armored Wave | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "2014 Toyota Corolla E180 EU (with interior)" by Armored Wave, CC BY 4.0, modified for Turku3D. Changes: rescaled to 4.62 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); paint made tintable; decimated to 30193/9851/3444 triangles. Runtime name "Kaira Säde 160" (fictional). |
| `cars/w212/lod{0,1,2}.glb` | "Mercedes E Class W212" (https://sketchfab.com/3d-models/mercedes-e-class-w212-119c5e10733142b197aa53b86f6aeb04) by Peter_D | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "Mercedes E Class W212" by Peter_D, CC BY 4.0, modified for Turku3D. Changes: rescaled to 4.87 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); paint made tintable, grille emblem/lettering removed by hand-placed box; decimated to 47355/9817/3418 triangles. Runtime name "Nord Säde 220" (fictional). |
| `cars/v60/lod{0,1,2}.glb` | "Volvo V60 Polestar (2013)" (https://sketchfab.com/3d-models/volvo-v60-polestar-2013-504ce39150fe49c7979f702d5f0f8580) by Myedsu | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "Volvo V60 Polestar (2013)" by Myedsu, CC BY 4.0, modified for Turku3D. Changes: rescaled to 4.63 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); paint made tintable; decimated to 35383/9848/3442 triangles. Runtime name "Routa Virta D4" (fictional). |
| `cars/prius-2012/lod{0,1,2}.glb` | "Toyota Prius 2012" (https://sketchfab.com/3d-models/toyota-prius-2012-04eaf6b288cc466785365b7d985e3b29) by Doz3 | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "Toyota Prius 2012" by Doz3, CC BY 4.0, modified for Turku3D. Changes: rescaled to 4.48 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); paint made tintable; decimated to 18870/9843/3443 triangles. Runtime name "Valo Hybrid 3" (fictional). |
| `cars/yaris-2001/lod{0,1,2}.glb` | "2001 Toyota Yaris" (https://sketchfab.com/3d-models/2001-toyota-yaris-443fd49eb0844557a06854cae0b61267) by Dave Love SketchFab | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "2001 Toyota Yaris" by Dave Love SketchFab, CC BY 4.0, modified for Turku3D. Changes: rescaled to 3.62 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); paint made tintable; decimated to 47301/9844/3440 triangles. Runtime name "Salo Kipinä 10" (fictional). |
| `cars/punto-1995/lod{0,1,2}.glb` | "(FREE) 1995 Fiat Punto GT" (https://sketchfab.com/3d-models/free-1995-fiat-punto-gt-48db6facb4b64e99b60f36b8c01185e1) by Lionsharp Studios | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "(FREE) 1995 Fiat Punto GT" by Lionsharp Studios, CC BY 4.0, modified for Turku3D. Changes: rescaled to 3.76 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); paint made tintable; decimated to 33736/9849/3441 triangles. Runtime name "Aava Kipinä GT" (fictional). |
| `cars/e30/lod{0,1,2}.glb` | "[FREE] BMW M3 E30" (https://sketchfab.com/3d-models/free-bmw-m3-e30-ac3c7013434e403e8faff87948caf422) by Martin Trafas | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "[FREE] BMW M3 E30" by Martin Trafas, CC BY 4.0, modified for Turku3D. Changes: rescaled to 4.3464 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); paint made tintable; decimated to 47270/9837/3386 triangles. Runtime name "Muisto Säde 23" (fictional). |
| `cars/s90/lod{0,1,2}.glb` | "Volvo S90 Recharge (Free)" (https://sketchfab.com/3d-models/volvo-s90-recharge-free-9462b07c10244fd4a28d86846dc9e3a9) by lazercar | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "Volvo S90 Recharge (Free)" by lazercar, CC BY 4.0, modified for Turku3D. Changes: rescaled to 4.96 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); paint made tintable; decimated to 47245/9811/3441 triangles. Runtime name "Routa Säde T8" (fictional). |
| `cars/generic-sedan/lod{0,1,2}.glb` | "Generic Sedan Car" (https://sketchfab.com/3d-models/generic-sedan-car-58c33766470d46e7b2aed542650494e5) by MMC Works | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "Generic Sedan Car" by MMC Works, CC BY 4.0, modified for Turku3D. Changes: rescaled to 4.75 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); paint made tintable; decimated to 47204/9810/3411 triangles. Runtime name "Vire Säde 200" (fictional). |
| `cars/crownvic-taxi/lod{0,1,2}.glb` | "2001 Crown Victoria Taxi Game Prop" (https://sketchfab.com/3d-models/2001-crown-victoria-taxi-game-prop-2429a825ff1646568e8e6e1453f9c501) by 8sianDude | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "2001 Crown Victoria Taxi Game Prop" by 8sianDude, CC BY 4.0, modified for Turku3D. Changes: rescaled to 5.39 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); livery colours kept, grille emblem/lettering removed by hand-placed box; decimated to 33834/9851/3443 triangles. Runtime name "Nord Taksi V8" (fictional). |
| `cars/crownvic-police/lod{0,1,2}.glb` | "2001 Crown Victoria Police Interceptor Game Prop" (https://sketchfab.com/3d-models/2001-crown-victoria-police-interceptor-game-prop-9f30d360cee343efb5a441978ddb57bd) by 8sianDude | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "2001 Crown Victoria Police Interceptor Game Prop" by 8sianDude, CC BY 4.0, modified for Turku3D. Changes: rescaled to 5.39 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); livery colours kept, grille emblem/lettering removed by hand-placed box; decimated to 38536/9851/3438 triangles. Runtime name "Nord Partio V8" (fictional). |
| `cars/euro-van/lod{0,1,2}.glb` | "European Delivery Van" (https://sketchfab.com/3d-models/european-delivery-van-0b2f1ad95a79419f9a092420024d329c) by Evan Hiltz | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "European Delivery Van" by Evan Hiltz, CC BY 4.0, modified for Turku3D. Changes: rescaled to 5.2 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); paint made tintable; decimated to 21317/9843/3436 triangles. Runtime name "Kaira Kuorma 308" (fictional). |
| `cars/prado-2010/lod{0,1,2}.glb` | "Tayota Prado 2010 3D Model" (https://sketchfab.com/3d-models/tayota-prado-2010-3d-model-acca60013e8b4adaaa0823389525b5b2) by Betier Models | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "Tayota Prado 2010 3D Model" by Betier Models, CC BY 4.0, modified for Turku3D. Changes: rescaled to 4.78 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); paint made tintable; decimated to 47213/9836/3440 triangles. Runtime name "Salo Vaara D4" (fictional). |
| `cars/sprinter-ambulance/lod{0,1,2}.glb` | "Rettungswagen / Krankenwagen /  Ambulance" (https://sketchfab.com/3d-models/rettungswagen-krankenwagen-ambulance-e6c4858d34f0440394c2aa772ebaca38) by GRIP420 (model by Spaehling) | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "Rettungswagen / Krankenwagen /  Ambulance" by GRIP420 (model by Spaehling), CC BY 4.0, modified for Turku3D. Changes: rescaled to 6.6 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); livery colours kept; decimated to 28040/9841/3437 triangles. Runtime name "Kaira Ensihoito 413" (fictional). |
| `cars/transit-bus/lod{0,1,2}.glb` | "Transit Bus \| Two Service Doors (variant)" (https://sketchfab.com/3d-models/transit-bus-two-service-doors-variant-80ccd0ba61af4c0cbc862db81b85a3e1) by kurtismedia | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "Transit Bus \| Two Service Doors (variant)" by kurtismedia, CC BY 4.0, modified for Turku3D. Changes: rescaled to 11.4655 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); livery colours kept; decimated to 59185/11677/3934 triangles. Runtime name "Valo Linja 12" (fictional). |
| `cars/garbage-truck/lod{0,1,2}.glb` | "GARBAGE TRUCK" (https://sketchfab.com/3d-models/garbage-truck-9aee01b569f043b290cd7dc1abf38b71) by gelber | **CC BY 4.0** ([licence](https://creativecommons.org/licenses/by/4.0/)) | Attribution: "GARBAGE TRUCK" by gelber, CC BY 4.0, modified for Turku3D. Changes: rescaled to 9.5 m and reoriented; wheels split at hubs; badges, logos and plate text removed (blank generic plates); textures sampled into vertex colours (no images shipped); livery colours kept, grille emblem/lettering removed by hand-placed box; decimated to 58945/11799/3934 triangles. Runtime name "Louhi Jäte 26" (fictional). |
