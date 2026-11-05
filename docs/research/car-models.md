# Permissively licensed realistic car models

Research date: 2026-11-05. Goal (Jonni): find realistic, not cartoon, vehicle models that we may modify and
redistribute in the public MIT repo and the public web game, so the fleet does not have to be modelled from scratch.

## How candidates were ranked

The ranking uses only these criteria:

1. **Realism**, scored 1–5. Scores come from the model's Sketchfab or source page, its texture sets and the
   author's notes, plus a thumbnail review of the top 20 (local sheet, not in git). Only the BMW E30 and the
   OpenGameArt bus were opened on disk.
2. **Licence**: permissive and redistributable as raw files (CC0, CC BY 4.0), and credibly the uploader's own
   work. Game rips that carry a CC label are excluded; they are listed at the end.
3. **Usefulness for a Finnish/European city**: everyday cars, taxis, vans, buses, trucks and emergency vehicles
   rank above sports and luxury cars. European models get only a slight bonus; US, Asian and Soviet-era
   models are included.
4. **Completeness**: full body, windows (ideally an interior), PBR textures and no missing parts.

Triangle counts and wheel separation are listed for information only. We decimate, retopologise and split
wheels ourselves through the Blenderwright MCP fleet pipeline (`tools/dev/fleet-mcp.py`). Triangle counts are
the totals reported by Sketchfab (`faceCount`, triangles) unless marked otherwise.

**Formats.** Every downloadable Sketchfab model can be downloaded as auto-converted glTF and GLB with PBR
textures, plus the author's original upload (`.blend`, `.fbx`, `.obj` or `.max`) when one was provided. A
Sketchfab download needs a logged-in free account (API token). None is configured on this machine.

**Licence links.** Sketchfab's "CC Attribution" is
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), as the API's licence URL for every model below
confirms. Each licence label was read from the model's own record (`api.sketchfab.com/v3/models/{uid}`, the
same data the model page shows) on 2026-11-05.

**Attribution template (CC BY 4.0).** Each CC BY row in `game/public/assets/LICENSES.md` would read:
`"<Title>" (<model URL>) by <Author>, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/),
modified for Turku3D (decimated, rebadged, re-materialled).` Below, "Attribution: template" means this text
with the row's title, URL and author. CC0 rows need no attribution; we still record the source.

**Badges.** The fleet uses fictional badges. Every branded model below needs its logos, plates and liveries
(DHL, FedEx, police and taxi markings) replaced in the pipeline. That is normal conversion work.

## What the game uses today

- `game/src/actors/fleet/catalog.ts` and `geometry.ts`: 56 procedural, code-generated fictional models with a
  shared untextured node material.
- Four Blender-authored models on `local/loop-integration` (`game/public/assets/cars/`: model-y,
  octavia-combi-4, rav4-xa50, aava-estate). They are untextured, about 430–490 kB per LOD0.
- Kenney Car Kit `sedan.glb` and `truck.glb` (CC0). This is the cartoon look Jonni wants to leave behind.

Every top-10 candidate below has real PBR texture sets, glass and moulded detail. All of them are clearly
above the current fleet in realism; the gap is largest against the procedural and Kenney cars.

## Top 10

### 1. CARLA simulator vehicle set (about 35 vehicles)
- **Link:** <https://carla.readthedocs.io/en/latest/catalogue_vehicles/>, source <https://github.com/carla-simulator/carla>.
  **Author:** CARLA Team (Computer Vision Center, Barcelona).
- **Licence:** the README says "CARLA specific assets are distributed under the CC-BY License" (the version
  is not stated; treat it as CC BY 4.0 and credit "CARLA Team, Computer Vision Center"). Code is MIT.
  **Attribution:** "<Vehicle> from the CARLA Simulator (https://carla.org), CARLA Team, CC BY, modified for Turku3D."
- **Format:** Unreal Engine `.uasset` in the CARLA content package (downloaded by `Update.sh` / the release
  `Content` archive). Exporting to FBX needs the CARLA Unreal Editor, which in practice means a Linux or
  Windows host; the GTA research host could do it. Triangle counts are not published (the vehicles are
  game-ready LOD sets). PBR: yes. Separate wheels: yes (skeletal wheel bones). Interiors: yes on Gen 2.
- **Types:** the broadest set found. Cars: Audi A2, Citroën C3, Seat Leon, Nissan Micra, Mini Cooper, Toyota
  Prius, Tesla Model 3, Lincoln MKZ, BMW Gran Tourer, Mercedes Coupé, Audi e-tron, Nissan Patrol, Jeep
  Wrangler, Ford Crown Victoria taxi, Dodge Charger police. Vans: Mercedes Sprinter, VW T2. Emergency: Ford
  ambulance, CARLA fire truck. Trucks: "European HGV", CarlaCola truck. Bus: Mitsubishi Fuso Rosa.
- **Realism:** 4. **Fit:** best single source for European everyday cars plus the ambulance, fire truck, taxi,
  police and bus roles. The only cost is a one-off export step.

### 2. 2014 Toyota Corolla E180 EU (with interior)
- **Link:** <https://sketchfab.com/3d-models/36f95efb0585464cae43a25a3b3392e8>. **Author:** Armored Wave (`armoredwave`).
- **Licence:** CC BY 4.0. Author: "The model is free to use in any project." **Attribution:** template.
- **Format:** glTF/GLB plus source. About 84k tris (exterior about 30k, interior about 55k). Optimised for game
  engines, 30 textures. Wheels: unknown, likely separate.
- **Type:** EU-spec compact sedan, about 2014–2019. **Realism:** 4.
- **Fit:** excellent. It is a very common Finnish car, game-ready, with an interior.

### 3. Mercedes E-Class W212
- **Link:** <https://sketchfab.com/3d-models/119c5e10733142b197aa53b86f6aeb04>. **Author:** Peter_D (`better_peter`).
- **Licence:** CC BY 4.0. Author: "You can use it freely as you want in your projects or games."
  **Attribution:** template.
- **Format:** glTF/GLB. About 363k tris, 68 textures (PBR), 16 materials. Wheels: unknown.
- **Type:** executive sedan, 2009–2016. **Realism:** 4.
- **Fit:** excellent. The W212 is the archetypal Finnish taxi and a common private car; it needs decimation.

### 4. Volvo V60 Polestar (2013)
- **Link:** <https://sketchfab.com/3d-models/504ce39150fe49c7979f702d5f0f8580>. **Author:** Myedsu (`myedsu`).
- **Licence:** CC BY 4.0. **Attribution:** template.
- **Format:** glTF/GLB. About 53k tris, 7 textures, 24 materials. Wheels: unknown.
- **Type:** estate. **Realism:** 4. **Fit:** a Volvo estate is a Finnish staple and the polycount is already
  game-sized. Swap the Polestar wheels and kit for a plain trim.

### 5. Mercedes Sprinter 413 Rettungswagen (ambulance)
- **Link:** <https://sketchfab.com/3d-models/e6c4858d34f0440394c2aa772ebaca38>. **Author:** GRIP420 (model by Spaehling).
- **Licence:** CC BY 4.0. Used in their own game "Photonic Distress". **Attribution:** template, crediting
  "Spaehling / GRIP420".
- **Format:** glTF/GLB. About 33k tris, 6 materials; texture count not reported. Wheels: unknown.
- **Type:** box-body ambulance (German DRK livery). **Realism:** 4.
- **Fit:** excellent. It is the right body type for a Finnish ambulance; repaint it yellow and green.

### 6. Toyota Prius 2012
- **Link:** <https://sketchfab.com/3d-models/04eaf6b288cc466785365b7d985e3b29>. **Author:** Doz3.
- **Licence:** CC BY 4.0. **Attribution:** template.
- **Format:** glTF/GLB. About 19k tris, 9 textures, 23 materials. Wheels: unknown.
- **Type:** hybrid liftback. **Realism:** 4.
- **Fit:** excellent. It is the most common Finnish hybrid taxi and private car, and already at game polycount.

### 7. 2001 Toyota Yaris
- **Link:** <https://sketchfab.com/3d-models/443fd49eb0844557a06854cae0b61267>. **Author:** Dave Love (`Tyler_Dave`).
- **Licence:** CC BY 4.0. **Attribution:** template.
- **Format:** glTF/GLB. About 101k tris, 27 textures, with an interior. Wheels: unknown.
- **Type:** supermini hatchback. **Realism:** 4. **Fit:** a very common older Finnish small car.

### 8. Volvo S90 Recharge
- **Link:** <https://sketchfab.com/3d-models/9462b07c10244fd4a28d86846dc9e3a9>. **Author:** lazercar.
- **Licence:** CC BY 4.0. The page gives a Google Drive link to the `.blend` and textures as the preferred download.
  **Attribution:** template.
- **Format:** `.blend` plus PBR textures (56 textures). About 3.27M tris (render quality), so heavy decimation is needed.
- **Type:** current executive sedan. **Realism:** 5. **Fit:** a modern Volvo; the most realistic car found.

### 9. BMW M3 E30 (checked on disk)
- **Link:** <https://sketchfab.com/3d-models/ac3c7013434e403e8faff87948caf422>. **Author:** Martin Trafas (`TinoD2`).
- **Licence:** CC BY 4.0. **Attribution:** template.
- **Format:** `.blend` (Blender 3.3) plus full PBR texture folders (base colour, metallic, roughness,
  normal, AO, emission for lamps). About 117k tris on Sketchfab. **Wheels: separate.** The `.blend` holds 4 rims,
  4 tyres, 4 brake discs and 4 calipers as separate objects, plus one body object. Downloaded to
  `~/turku3d/loops/carmodels/tinod2-e30/` (Drive mirror linked from the model page, 144 MB RAR).
- **Type:** 1980s compact sedan (a sporty variant of a common car). **Realism:** 5.
- **Fit:** very good for an older-car slot; the plain E30 shape is common.

### 10. 1995 Fiat Punto GT
- **Link:** <https://sketchfab.com/3d-models/48db6facb4b64e99b60f36b8c01185e1>. **Author:** Lionsharp Studios (`lionsharp`).
- **Licence:** CC BY 4.0. Author: "free for use in your projects. Also commercial." **Attribution:** template.
- **Format:** glTF/GLB. About 35k tris, 20 textures. Wheels: unknown.
- **Type:** 1990s European hatchback. **Realism:** 4. **Fit:** a game-sized everyday 90s European car.

## Further usable candidates

All rows are CC BY 4.0 on Sketchfab (attribution: template), unless the licence column says otherwise. The
"W" column (wheels) reads: **S** separate (stated or checked), **?** unknown, **F** fused (scan). Tris are
Sketchfab totals.

### Everyday cars (hatchback, sedan, estate)
| # | Model | Author | Tris | W | Real. | Completeness and fit |
|---|---|---|---|---|---|---|
| 11 | [Generic Sedan Car](https://sketchfab.com/3d-models/58c33766470d46e7b2aed542650494e5) | MMC Works (`mmcworks`) | 113k | S | 4 | Unbranded design, so no rebadging. Rough interior, textures, "separated parts and rigged with Rigacar". Ideal fictional traffic sedan. |
| 12 | [Generic passenger car pack](https://sketchfab.com/3d-models/20f9af9b8a404d5cb022ac6fe87f21f5) (sedan, wagon, hatch, compact, coupé, minivan, sport, pickup…) | Comrade1280 | ~7k each | ? | 3 | Unbranded ("trademark claim proof"), textured, realistic proportions but low detail. Good far-LOD or background traffic. |
| 13 | [Mercedes 190E EVO 1982](https://sketchfab.com/3d-models/15e5576f4b724f2db357165caed3492b) | Betier Models | 179k | S | 4 | Blender rig plus PBR. W201 is a classic Finnish used car (plain trim needs the EVO kit removed). |
| 14 | [2000 Jaguar XJ Sport X308](https://sketchfab.com/3d-models/192011ef37994e8ca2f09cd1d73ca166) | Fishboe (`ministephen`) | 26k | ? | 4 | "Low poly, no interior, optimised for game engines". Older luxury sedan. |
| 15 | [Triumph Dolomite Sprint (Textured)](https://sketchfab.com/3d-models/ab3547e947e2496e843032684c4b6afe) | Fishboe | 58k | S | 4 | Substance textures, white paint variant included, "wheels are also separate objects". 70s British sedan. |
| 16 | [1994 Peugeot 405 GLX facelift](https://sketchfab.com/3d-models/b9b69099434c45bdb3265417dd720752) | Mehrdad995 | 25k | ? | 3 | Made for mobile games. A 90s European sedan, game-sized. |
| 17 | [Volvo 240 styled sedan](https://sketchfab.com/3d-models/66697669831b47cea7874fc8c4544e74) | Libau Media (`robinmikart`) | 63k | S | 3 | GL and Turbo variants, "all the objects are separate". An iconic Nordic older car. |
| 18 | [Volvo 740](https://sketchfab.com/3d-models/2d44c05d36424ceb96e6da7451df782d) | Dennis Andersson (`bigslavehd`) | 197k | ? | 4 | No textures (materials only); good shape. Common old Nordic sedan. |
| 19 | [Volkswagen Golf Mk2](https://sketchfab.com/3d-models/23e8f278995448e7be905b0777a31529) | Neo_minigan | 1.85M | ? | 5 | 28 textures, render quality. A classic hatchback; decimate. |
| 20 | [Volkswagen Golf 8 GTI](https://sketchfab.com/3d-models/e87d7c0f8937481db236529beb5ecab7) | Mona x Supercars (`Car2022`) | 121k | ? | 3 | 17 textures; the thumbnail shows shading artefacts. Current Golf shape, so a useful base. |
| 21 | [Fiat Abarth 500](https://sketchfab.com/3d-models/b59a403dfa1d40318c1126e658f87c19) | Luquita (`speedmodel`) | 115k | ? | 4 | 46 textures. City car. |
| 22 | [BMW 3 Series Touring E46 2001](https://sketchfab.com/3d-models/478330312c3d46fd94999c3133ce0366) | Luquita (`speedmodel`) | 1.19M | ? | 4 | Estate, materials only. Common used estate in Finland. |
| 23 | [Tesla 2018 Model 3](https://sketchfab.com/3d-models/5ef9b845aaf44203b6d04e2c677e444f) | Ameer Studio (`uchiha.321abc`) | 684k | ? | 4 | 22 textures. Common EV. |
| 24 | [Tesla Model S P100D](https://sketchfab.com/3d-models/26ef15adc5ee4d8bbcb4a4f95ca49168) | Ameer Studio | 563k | ? | 4 | 20 textures. |
| 25 | [Mercedes-Benz EQE AMG](https://sketchfab.com/3d-models/1f1dfa0adfc943039d3870a5b958f069) | Casacade Models | 1.10M | ? | 4 | Current EV sedan. |
| 26 | [Audi e-Tron GT](https://sketchfab.com/3d-models/a48a8c4ac4474c47b8ddf5bba754db40) | gbarzu | 1.26M | ? | 4 | Custom headlights. Premium EV. |
| 27 | [Free Mazda 6 Sedan 2011, low poly](https://sketchfab.com/3d-models/5742c2b07f224d53916307cc1e0f9cc5) | 3D Piece (`3dpiece`) | 15k | ? | 3 | 2 textures (baked), AR/VR optimised. Mid sedan as a far-LOD/background car. |
| 28 | [Hyundai Accent 2013](https://sketchfab.com/3d-models/cec6b04b06724f1088129c1054ccb1ec) | L95XP (`I95XP`) | 951k | ? | 3 | Materials only. Everyday sedan. |
| 29 | [Honda Accord 2017](https://sketchfab.com/3d-models/68892cd369e84a218e5e5dcf82365ee3) | L95XP | 778k | ? | 3 | Materials only. |
| 30 | [Suzuki Esteem 1998](https://sketchfab.com/3d-models/d0ef65285179484191b75602d67804a1) | Artem P (`temp0.crazy`) | 157k | ? | 4 | 42 textures. 90s compact sedan or estate. |
| 31 | [Toyota Corolla E70 (1980)](https://sketchfab.com/3d-models/c4d66d21968c41da89114e2300578bc5) | ditovirnantio | 165k | ? | 3 | Old-timer sedan. |
| 32 | [Polski Fiat 126p](https://sketchfab.com/3d-models/edc1b9d866484e048b4c54d917cca900) | Martin Trafas (`TinoD2`) | 154k | ? | 5 | 52 textures. Eastern European classic. |
| 33 | [BMW E36 M3](https://sketchfab.com/3d-models/762b90efd3dc413cb61c3e3c8097b8ac) | Martin Trafas | 67k | ? | 4 | 90s compact sedan. |
| 34 | [LADA 2107](https://sketchfab.com/3d-models/31201f80892b404a8e49c272490a664c) | tiunov.se | 105k | ? | 4 | A Lada was a common Finnish import. |
| 35 | [LADA VAZ-2101](https://sketchfab.com/3d-models/b67bb648e67043f4b589aa83717dc58d) | panderlaike_design | 2.80M | ? | 5 | Red interior, render quality. |
| 36 | [Citroën 2CV Charleston 1986](https://sketchfab.com/3d-models/abdb2d11268b405c90b117e0992693ee) | MaximePages (`pagesm`) | 163k | ? | 5 | "Game ready mesh", 18 × 4K PBR maps, `.max`/`.fbx`/`.obj`, real-world scale. Rare in Finland but excellent quality. |
| 37 | [VW Beetle 1969](https://sketchfab.com/3d-models/3d166ec739cb440984cbcd17608675e0) | Matej Ondra (`mianted`) | 172k | ? | 4 | 4K textures, "suspension included". |
| 38 | [Fiat 500 (1970)](https://sketchfab.com/3d-models/213cd126fcd744809fb7996eb88366f5) | Parsa Farvadian | 499k | ? | 4 | Materials only. Author asks for credit to him or @Parsafarvadian (the template covers this). |
| 39 | [Renault 5](https://sketchfab.com/3d-models/6a984d7ebad3463d82312882fb4e6f1f), [Renault 11 Turbo](https://sketchfab.com/3d-models/a776bb99a02f47fb83890af0b61ce64a), [Fiat Uno Turbo Mk2](https://sketchfab.com/3d-models/56df35ff788e4c2a8ca59fcec5fe7158), [Peugeot 404](https://sketchfab.com/3d-models/b5899c33395642088a2b0ce0797a2c24), [Honda Accord Hatchback 1978](https://sketchfab.com/3d-models/bb853259a542400eb3e6eaed0a4557a1) | boitaloran | 1.9–4.1M | ? | 5 | A series of high-detail European 70s–80s classics with textures; heavy decimation needed. |
| 40 | [Peugeot 405 GLX 1987](https://sketchfab.com/3d-models/3cecab982cd14218b5447f69cb0603fb) | Mpgs.studio | 3.91M | ? | 4 | Materials only. |
| 41 | [Volvo PV654 Sedan v2](https://sketchfab.com/3d-models/65f8f21ef0824e62b9fe36d17ebd8b99) | Libau Media | 47k | ? | 4 | 1930s Volvo, historically a Swedish/Nordic taxi. Period piece. |
| 42 | [FREE 1950s Car Variety Pack](https://sketchfab.com/3d-models/067c3ca92ac0489daadc39ba8b208795) (Packard, Buick, Ford, Hudson…) | Libau Media | 1.18M total | S | 4 | Separate `.blend`/`.fbx` per car, 47 textures. Old-timers. |

### Taxis and police
| # | Model | Author | Tris | W | Real. | Completeness and fit |
|---|---|---|---|---|---|---|
| 43 | [2001 Crown Victoria Taxi Game Prop](https://sketchfab.com/3d-models/2429a825ff1646568e8e6e1453f9c501) | 8sianDude (`haoliu95`) | 34k | ? | 4 | Made for a L4D2 mod by the author, Substance 2K maps. US taxi; repaint it as a Finnish taxi or use it as a generic sedan. |
| 44 | [2001 Crown Victoria Police Interceptor Game Prop](https://sketchfab.com/3d-models/9f30d360cee343efb5a441978ddb57bd) | 8sianDude | 42k | ? | 4 | Same base with a lightbar. A police role (needs repainting in Finnish police livery). |
| 45 | [Generic civil service vehicles pack](https://sketchfab.com/3d-models/8ff2a13f30914932a70c7950cfa58465) (ambulance, city bus, fire truck, garbage truck, police sedan, school bus, tow truck…) | Comrade1280 | 9–13k each | ? | 3 | Unbranded, textured, all the service roles in one pack. Background/far traffic. |
| 46 | [Police car](https://sketchfab.com/3d-models/9166b13b6ae341f4bfc093edb71d74f4) and [LAPD car](https://sketchfab.com/3d-models/795fa0620bc44db8afcb06720a4dadd7) | SpatialNeglect (`jeandiz`) | 256k / 242k | ? | 4 | US police sedans, textured. |
| 47 | [2013 Chevrolet Caprice Police](https://sketchfab.com/3d-models/52757b210548435ca377e850930d0638) | Ddiaz Design | 27k | ? | 4 | Game-sized police car, 19 textures. |
| 48 | [Calgary Police Ford Explorer](https://sketchfab.com/3d-models/f1318e0523cd428c87f6653dd5cded85) | valvetin | 171k | ? | 4 | Police SUV. |

### Vans and light commercial
| # | Model | Author | Tris | W | Real. | Completeness and fit |
|---|---|---|---|---|---|---|
| 49 | [European Delivery Van](https://sketchfab.com/3d-models/0b2f1ad95a79419f9a092420024d329c) | Evan Hiltz | 21k | ? | 4 | Styled after a late-70s Mercedes T1 van, unbranded and textured. Game-sized delivery van. |
| 50 | [Volkswagen van T6](https://sketchfab.com/3d-models/b36c0e9bf97b479f9193cce337f4d4c2) | IrisProcess | 337k | ? | 3 | "Simple design", 21 textures. The most common Finnish trade van shape. |
| 51 | [Mercedes-Benz Sprinter (passenger)](https://sketchfab.com/3d-models/573b461cf5cf43daaf33928a6c1b370d) | Res1n | 1.14M | ? | 4 | "High quality"; materials only. Minibus/van. |
| 52 | [Mercedes-Benz Sprinter](https://sketchfab.com/3d-models/152f62800be34652af0545487129ca2e) | Savelliy 07 | 96k | ? | 3 | Materials only. |
| 53 | [DHL delivery van](https://sketchfab.com/3d-models/6f952a34d1f44905af42c803cba2805f) / [FedEx van](https://sketchfab.com/3d-models/554a6a4d30d342f2aca8735aaa69f954) | memoov (`movartD`) | ~790k | ? | 4 | "All made in Blender… free to use". A Sprinter-type van; remove the liveries. |
| 54 | [2003 Chevrolet Express / GMC Savana cargo van](https://sketchfab.com/3d-models/ed2a0b37a01e4e51ba5b82c35d4a9dae) | Ddiaz Design | 31k | ? | 4 | US full-size van, game-sized. |
| 55 | [Suzuki Carry Minivan](https://sketchfab.com/3d-models/5c0007c0c6ed4d298b8c80646efa09d2) | ProPolyModels | 153k | ? | 4 | Detailed exterior, simple interior, real-world scale. Kei van. |
| 56 | [Suzuki Carry Blind Van](https://sketchfab.com/3d-models/f1ab63b5a2d94e4ebf9023a06f8aff71) | Dave Love | 158k | ? | 4 | 25 textures. |
| 57 | [Toyota Van 1982](https://sketchfab.com/3d-models/d3b5955e74cc4c619c4658a0e7104f42) | AndreiV (`vav206`) | 195k | ? | 4 | 80s van. |
| 58 | [Volkswagen Transporter T1 Panel Van](https://sketchfab.com/3d-models/882d41cabe7644a6b53d6760282bf2eb) | Render at Night | 383k | ? | 4 | Materials only; classic. |
| 59 | [FSC Żuk](https://sketchfab.com/3d-models/bb4OJb5V4L0hgYjM3vtGvPB7ZMt) | Lionsharp Studios | 12k | ? | 3 | Polish van, game-sized. |
| 60 | [UAZ-452](https://sketchfab.com/3d-models/94397cdca6044e6c9cb0caaf01156a84) | tnikita | 24k | ? | 3 | Soviet van, game-sized. |
| 61 | [Citroën H Van (scan)](https://sketchfab.com/3d-models/ec90bbb67585481a81c2de7d67b0af7d) | alban | 240k | F | 3 | iPhone photogrammetry: one baked texture, no glass, fused wheels. A parked-van prop only. |

### SUVs and pickups
| # | Model | Author | Tris | W | Real. | Completeness and fit |
|---|---|---|---|---|---|---|
| 62 | [Land Rover Defender 110](https://sketchfab.com/3d-models/5773ec04488b46148816b5f7a9f3f44f) | meeww | 688k | S | 4 | Blender rig (body control and front-wheel steer). |
| 63 | [Toyota Prado 2010](https://sketchfab.com/3d-models/acca60013e8b4adaaa0823389525b5b2) | Betier Models | 595k | S | 4 | "Rigged", exterior only. Common SUV. |
| 64 | [Range Rover Sport 2018](https://sketchfab.com/3d-models/d1fe581b3baf4af89275e9aa9a1392e2) | diw3d | 523k | ? | 4 | Exterior only, 12 textures. |
| 65 | [Cadillac XT6 2023](https://sketchfab.com/3d-models/b6a0be0c32e343b4812ec47660142824) | 3DShowroom | 201k | ? | 4 | 11 textures. |
| 66 | [1989 Chevrolet Suburban](https://sketchfab.com/3d-models/44bdb8317f0840b3bc858e606f86db6b) | Randomness | 124k | ? | 4 | 28 textures. |
| 67 | [2019 Chevrolet Silverado Trail Boss](https://sketchfab.com/3d-models/652324cc8a974d3a9869ce2b0f3beaaa) | Ddiaz Design | 479k | ? | 4 | Pickup, 21 textures. |
| 68 | [Ford Bronco](https://sketchfab.com/3d-models/09ebc68dcb634807a9dace7404ce5e66) | Casacade Models | 324k | ? | 4 | |

### Buses
| # | Model | Author | Tris | W | Real. | Completeness and fit |
|---|---|---|---|---|---|---|
| 69 | [Transit Bus, two service doors](https://sketchfab.com/3d-models/80ccd0ba61af4c0cbc862db81b85a3e1) | kurtismedia | 1.92M | S | 4 | Rigged: doors, windows, wheels with suspension. A modern low-floor city bus, the closest to a Föli bus. |
| 70 | [PAZ-652](https://sketchfab.com/3d-models/5604234eee6a453b83634dc4cad9aba4) and [LiAZ-677](https://sketchfab.com/3d-models/e6364a8af77048dd89203355b8dbd9ac) | romunql | 110k / 106k | ? | 4 | "You may freely use this model in your projects", 70–80 textures. Soviet buses for museum or period use. |
| 71 | [LAZ-695](https://sketchfab.com/3d-models/ddfd0b9846c24a058874521e9d170bcb) | Alt (`whisper.alt`) | 116k | ? | 4 | 26 textures. |
| 72 | [3D Bus](https://opengameart.org/content/3d-bus) (OpenGameArt) | ajanhallinta | 14,507 (checked) | S (checked: `Bus_Body` plus `wheelFL/FR/LR/RR`) | 2 | **CC0** ("Attribution is not required"). `.blend`/`.fbx`/`.obj`, materials only, no interior. Downloaded to `~/turku3d/loops/carmodels/oga-3dbus/`. Placeholder quality only. |
| 73 | [School bus (scan)](https://sketchfab.com/3d-models/d22d36e8883943cf8733120fde611c48) | alban | 165k | F | 3 | Photogrammetry, a parked prop. |

### Trucks and municipal vehicles
| # | Model | Author | Tris | W | Real. | Completeness and fit |
|---|---|---|---|---|---|---|
| 74 | [Garbage truck](https://sketchfab.com/3d-models/9aee01b569f043b290cd7dc1abf38b71) | gelber (`liebherr996litronic`) | 1.00M | ? | 4 | 10 textures; the author releases his models freely and on principle. Refuse truck. |
| 75 | [Volvo FH16](https://sketchfab.com/3d-models/a7c5155c857949548a9687a617702c23) | gelber | 783k | ? | 4 | Nordic heavy truck tractor. |
| 76 | [Ural-4320 police truck (interior)](https://sketchfab.com/3d-models/05c559b38658478ca4e978881157515a) | Basic Hsu | 114k | ? | 4 | Military-style truck. |

### Older US cars (breadth)
| # | Model | Author | Tris | W | Real. | Notes |
|---|---|---|---|---|---|---|
| 77 | [Oldsmobile Cutlass Supreme Sedan '71](https://sketchfab.com/3d-models/78f76d386a4341b0b71745bdc50fd5ab) | Barbo (`barbo-autos`) | 70k | ? | 5 | Built over a photogrammetry cage, 27 textures. |
| 78 | [Lincoln Continental Mark V](https://sketchfab.com/3d-models/09587fee473d4b07ac7f10d2a65b47c4) | Barbo | 169k | ? | 5 | "60K tris without wheels", so the wheels are separate meshes. |
| 79 | [Chevrolet Caprice Station Wagon](https://sketchfab.com/3d-models/ee2fd85ef1994c00b6de0e93a019901b) | Doz3 | 63k | ? | 4 | |
| 80 | [1977 Ford LTD Wagon](https://sketchfab.com/3d-models/9140bf761568430fa1b1d8c935e26eed) | Andras Pall | 1.69M | ? | 5 | |
| 81 | [1965 Ford Mustang Convertible](https://sketchfab.com/3d-models/05f2a294087541ab82f681136d134344) | Kristian LaGrange | 112k | ? | 4 | |

### Sports and enthusiast cars (rare traffic, player cars)
| # | Model | Author | Tris | W | Real. |
|---|---|---|---|---|---|
| 82 | [Porsche 911 (930) Turbo 1975](https://sketchfab.com/3d-models/8568d9d14a994b9cae59499f0dbed21e), "Please give a credit to Lionsharp Studios" | Lionsharp Studios | 241k | ? | 5 |
| 83 | [Datsun 280Z](https://sketchfab.com/3d-models/0789ab2ece9442de94b3c41595e0ecbd) (Drive link: `.blend`/`.obj`/`.fbx`) | Martin Trafas | 107k | ? | 5 |
| 84 | [Toyota AE86 Sprinter Trueno](https://sketchfab.com/3d-models/a5737bf3cc9b4179a6e5ebe173ff70d9) | Martin Trafas | 706k | ? | 5 |
| 85 | [Nissan Skyline C110](https://sketchfab.com/3d-models/1a950b81ee274a0eb8014bd84ba047f5), [Corvette C7](https://sketchfab.com/3d-models/2b509d1bce104224b147c81757f6f43a) | Martin Trafas | 291k / 1.07M | ? | 5 |
| 86 | [Honda S800](https://sketchfab.com/3d-models/fa6c6113f1e34d9baaff80b5b586a25d), [Triumph Spitfire MkIII](https://sketchfab.com/3d-models/9c5c1a73f65046459051f9d0bf1132a0) | Yakpower (`Alexios_Apokaukos`) | 185k / 155k | ? | 5 |
| 87 | [Porsche 930](https://sketchfab.com/3d-models/de1ffd344c41481892511f7fd332c136), [Mercedes 300 SL](https://sketchfab.com/3d-models/505241c829c540a4921533000736904e), [Mazda MX-5 NA](https://sketchfab.com/3d-models/d51fcd44b74f4daf8012c41e0400c041), [Honda NSX 1990](https://sketchfab.com/3d-models/1cc15628a00a4739a6b6c01128927c8d) | Lexyc16 | 52–101k | ? | 4 |
| 88 | [GMC Motorhome reimagined](https://sketchfab.com/3d-models/6hiH0iyDqXqtdD9wbqSbyLLhKmz), "YES to commercial use. Leave a credit." | Lionsharp Studios | 43k | ? | 4 |

**Usable total: 88 numbered entries, about 105 individual models** (rows 1, 12, 39, 42, 45 and the paired rows hold
several each). The Golf Mk4 below brings it to 89.

### Conditional (usable after one check)
- [VW Golf Mk4 (fan art)](https://www.blendswap.com/blend/3469), BlendSwap, Jay-Artist, 2011: **CC0** on the page,
  staff pick. BlendSwap needs a login to download, so triangle count and texture quality are unchecked
  (likely realism 3, from 2011).
- **Max-7215** ([Mercedes Sprinter 2006](https://sketchfab.com/3d-models/f69de1315bb049c8946d57f6006acd73),
  [Iveco Daily 2014](https://sketchfab.com/3d-models/30003fba5b074d6eb7bc74fd5e1e7d19),
  [DAF CF 75](https://sketchfab.com/3d-models/2f2ced3e54f24b4f8bee695e3261bd30),
  [Renault Premium carrier](https://sketchfab.com/3d-models/b63d1888fde442ffab2c6534abcee647),
  [BMW 1 Series 2010](https://sketchfab.com/3d-models/757bc6c49d894ece85a6950c0a7fbb67),
  [Mercedes LP 608 dump truck](https://sketchfab.com/3d-models/63f87292f8aa488e8d5dcafd4ec7b3ec)): excellent
  European commercial vehicles (38–154k, 4K Substance textures, realism 4). They are published as CC BY, but the
  descriptions say "This model is not free of rights. You must inform me of your usage." Notify the author
  (Discord `max7215`) before shipping; then they rank in the top 10 for vans and trucks.
- [Honda Civic Type R '98](https://sketchfab.com/3d-models/b9604dd71c6546ec842702682019492a) (tiedtke): a remodel
  of another author's (@Louweee___) widebody model. Check that the upstream model is CC BY too.
- **BlackSnow02** (e.g. [BMW E39](https://sketchfab.com/3d-models/531a5a93da5d493d9918eb36f011c20d),
  [Golf Mk1](https://sketchfab.com/3d-models/191d4905f1274cee8539a39a0a672fd9),
  [Mercedes W212](https://sketchfab.com/3d-models/820387a848a8449eb6565ff35e74388a),
  [LADA 2103](https://sketchfab.com/3d-models/9d02e433d9dc4b92a271a4c8641b6211)) and **David_Holiday** (e.g.
  [BMW M3 2013](https://sketchfab.com/3d-models/3be9ac82a47e4639b7d0698e1697feb9),
  [Impala Highway Patrol](https://sketchfab.com/3d-models/016e93bac3c94a9fa78caf9630553b6b),
  [Charger NYPD](https://sketchfab.com/3d-models/578e2bfb9c8f415d932527a938e2fc64)): dozens of high-end CC BY cars
  with empty or one-line descriptions and no making-of. Provenance is unverified; ask the authors before use.
- [Khronos CarConcept](https://github.com/KhronosGroup/glTF-Sample-Assets) (Eric Chadwick, CC BY 4.0): a realistic
  glTF but a concept car; useful as a car-paint/material reference rather than traffic.
- [Poly Haven "Covered Car"](https://polyhaven.com/a/covered_car) (CC0, 12.6k tris): not a drivable car, but a good
  parked covered-car prop; also the CC0 rusted wheel rims and old tyre.

## Checked and unusable

| Source | Result | Reason |
|---|---|---|
| Kenney Car Kit / Toy Car Kit | CC0 | Cartoon and low poly; it is the look the game used first. Excluded by Jonni. |
| Quaternius LowPoly Cars ("Realistic Car Pack – Nov 2018") | CC0 | Untextured stylised low poly despite the file name. Skipped. |
| RG Poly, Kytric voxel vehicles, GGBotNet PSX cars, OpenGameArt low-poly cars, Godot "3D Car with Settingspanel", Summer Engine Car Kit | CC0 | Stylised, voxel or PSX style. |
| Poly Pizza | CC0/CC BY | Aggregates mostly Google Poly-style low-poly models. |
| Sketchfab **BHP3D** (2017 Skoda Superb, 2019 Karoq, 2016 VW Polo, Nissan Micra, Ford Focus) | labelled CC BY | Descriptions say "exported from a GTA SA mod" and credit Turn10 and mod teams: game rips, so the uploader cannot license them. |
| Sketchfab **www.vecarz.com / heynic** (dozens: Skoda Fabia WRC, Polo R WRC, Mustangs, Skylines…) | labelled CC BY | Re-uploads from a game-rip site. |
| Sketchfab **sohyalebret** ("K.S Edition", 2022 Peugeot 308, race cars) | labelled CC BY | "K.S" naming matches Kunos/Assetto Corsa content; probable rips. |
| Sketchfab "NFS", "Rocket League", "Cities Skylines", "Forza", "Assetto" uploads | labelled CC BY | Game rips. |
| Sketchfab [Scania S730](https://sketchfab.com/3d-models/891ee120d3734b439a9a5e63eaa10a4c) and [Ford Ranger 2023](https://sketchfab.com/3d-models/e93ac097ee9a46a4a9decca9aceed03c) (Asadawut.Kaewma) | labelled CC BY | "Model By Hum3d" (a commercial store model) and "Model From Ford Thailand Website": not the uploader's work. |
| Sketchfab raw scans (kryik1023 "Free Raw Scan" cars, Toyota Auris wagon scan, Passat scan) | CC BY | Licence fine, but 0.3–2.5M-tri single-texture scans with no glass and fused wheels. Wreck or prop use only. |
| Sketchfab CC0 filter | — | Almost no vehicles. Sketchfab CC0 is dominated by museum scans; vehicles there are CC BY. |
| Poly Haven | CC0 | No vehicles, only the covered-car prop, rims and tyre (listed above). |
| Khronos ToyCar | CC0 | A toy car material showcase. |
| Blender demo files | CC BY-SA | Only "Racing Car" (Pokedstudio, 2.77 splash): stylised race car, share-alike. |
| BlendSwap | CC0/CC BY/CC BY-SA | Usable licences exist (Golf Mk4 above), but downloads need a login and most car blends are older hobby models; "6 classic Volkswagens" is CC BY-SA. Worth a logged-in sweep later. |
| CGTrader free models | Royalty Free licence | §21A.1: products "may not be sold, given, or assigned to another person" in downloaded form; games must keep files inaccessible. Not redistributable in a public repo. |
| TurboSquid free models | Royalty-free licence | Standard licence forbids redistributing the model files (page returned 403 to the fetcher; terms as published). |
| Fab.com / Unreal Marketplace free | Fab Standard licence | No standalone redistribution of raw assets. |
| Free3D, RenderHub free | Personal or custom licences | Mostly personal use, unclear redistribution. |
| ShapeNet (ShapeNetCore cars) | Research terms | "Non-commercial research and educational purposes" only; redistribution only to colleagues bound by the terms. |
| BeamNG.drive / Assetto Corsa mods | Game EULAs, per-mod terms | No permissive licence; the communities themselves forbid ripped meshes; much content is ripped. |
| Rigs of Rods repository | Per mod (GPL/CC, often BY-SA) | Licences vary per mod and quality is dated; not checked individually. |
| Speed Dreams, TORCS, VDrift | GPL / Free Art Licence | Race cars, copyleft. |
| Smithsonian 3D (3d.si.edu) | CC0 | No automobile models in the open-access 3D collection. |
| NASA 3D Resources | Public domain | Spacecraft and rovers only. |
| Wikimedia Commons | Various | 3D is STL only (untextured), no usable cars. |

## Recommended next step

1. Store a free Sketchfab account's API token in `~/.config/turku3d/sketchfab.token` (not in git). Pull the
   top 10 except CARLA, plus rows 11, 12, 43, 45, 49 and 69, as GLB and original source into
   `~/turku3d/loops/carmodels/`.
2. Run each through the existing Blenderwright MCP fleet pipeline (`tools/dev/fleet-mcp.py`): split wheels at
   the hubs, debadge, decimate to the LOD0/1/2 budgets, write a `spec.json`, and add a `LICENSES.md` row from the
   template above. The `cars` and `cars2` loops can consume this list in place of modelling from scratch.
3. In parallel, export the CARLA vehicle set to FBX once on a Linux or Windows host with the CARLA Unreal
   Editor. That one job covers the ambulance, fire truck, Sprinter, bus, HGV, taxi and police roles.
4. Ask Max-7215 about the European vans and trucks; with consent they fill the delivery-van and truck slots
   at high quality.
