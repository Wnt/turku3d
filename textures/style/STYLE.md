# Turku texture style bible

Every Codex texture prompt includes the fragments below (`textures/prompts/fragments/*.md`,
substituted as `$style`, `$negatives`, `$albedo_rules` by the job builders). Change a fragment →
bump `prompt_version` in the jobs so the content-hash cache regenerates the affected assets.

## 1. The look

Turku, south-west Finland, 60.45° N. A low, horizontal, river city: the Aura river with stone
quays, linden and maple rows, granite kerbs and cobbles, 1–6 storey buildings. Recognisably
**Nordic**, not generic European and never Mediterranean.

- **Light:** high-latitude, soft and cool. Textures are *de-lit albedo*: no sun, no cast shadows, no
  sky reflections. The engine adds the sun.
- **Palette:** muted, earthy and slightly desaturated. Ochre and yellow render (`#d9c38a`–`#e8cc7e`),
  Turku red/"punamulta" ochre-red (`#8a3b2b`), sage and grey-green wood (`#929b85`), pale grey and
  off-white boards, sandstone and grey granite (`#8d8a84`), brown-black tar, dark green or
  near-black window frames, white-painted trim. Avoid saturated primaries.
- **Materials by era (central Turku):**
  - pre-1850 and empire: horizontal wooden clapboard or log walls with profiled corner boards and carved
    window surrounds; high rusticated **granite plinths** with small cellar vents; standing-seam
    sheet-metal or Nordic pantile roofs.
  - 1850–1900 historicist: rendered or stone townhouses; cornices, pilasters, pediments over windows,
    string courses, arched ground-floor windows (e.g. the City Hall at Aurakatu 2).
  - 1900–1920 Jugend: rough-faced granite bases, natural stone, steep roofs, organic ornament.
  - 1920s classicism and 1930–50s functionalism: smooth render in pale yellow, grey and white; ribbon
    and grid windows; flat or low roofs.
  - 1960–70s: concrete elements, exposed aggregate, balconies with corrugated or glass fronts.
- **Windows:** casement windows with 2–6 panes, wooden frames, deep reveals. Glass reads as **dark,
  neutral grey-blue** with at most a faint uniform sheen; never mirror the street or sky.
- **Wear:** clean but lived in: slight weathering on wood, faint rain streaks under sills, darker
  plinth tops, small render patches. No graffiti, posters or stickers.
- **Ground:** grey granite setts and kerbs, light grey concrete pavers, dark asphalt with pale
  aggregate, gravel paths in parks.

## 2. Geometry rules for facade textures

- **Orthographic, rectified elevation**: the facade plane is parallel to the image, verticals are vertical,
  courses and sills horizontal. No perspective, no lens distortion.
- Frame **from the ground line (or plinth foot) to the eaves/cornice top**. No sky, roof, ground, street or
  neighbouring buildings unless the prompt asks for them.
- Keep the **real bay rhythm** (window count and spacing, door positions) from the reference photos
  for photo-referenced facades (ARCHITECTURE D6 use 3, D7). Ornament and colour follow the photos.
- Repeatable strips (long facades, library entries) must have **the same content at the left and right
  edges** (wrap horizontally); the local post-processor enforces this anyway.
- Scale cue: state the real width and height in metres in every prompt.

## 3. Negative constraints (always)

No people, cars, bicycles, scooters, street furniture, poles, traffic signs, lamps, umbrellas,
terraces, flower pots, scaffolding, construction wraps, fences, drainpipes in front of the wall, wires,
signs, text, logos, house numbers, posters, graffiti, watermarks, borders, frames, vignettes. No
cast shadows, no sun patches, no specular highlights, no reflections of trees/sky in windows, no
lens flare, no HDR look, no Mediterranean/Spanish/Italian features (barrel tiles, terracotta stucco,
shutters, wrought-iron balconies) unless the photos show them.

## 4. Process rules

- Codex only **generates and labels**. Seams, tiling, normals, height, ORM and compression are
  made locally and deterministically (`turkutex post`).
- Every accepted image keeps its prompt, `thread_id`, inputs and hashes (`out.jsonl`,
  `library/manifest.jsonl`). Image generation has no seed, so accepted assets are content-addressed and never
  regenerated unless the prompt version changes.
- Reference imagery from Google Street View / Apple Look Around is used **offline as a reference
  only** and is never shipped or committed; generated textures are new "inspired by" images.
  Previews committed to `docs/pilot/` are small and only for review.
- Values in JSON answers are always English (the Mac's `~/.codex/AGENTS.md` asks for Finnish by default).
