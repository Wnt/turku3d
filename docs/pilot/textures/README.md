# Texture pilot: two office-block facades (2026-10-25)

> Images derived from street-level photos (Google Street View, Apple Look Around, Mapillary) or
> Google 3D tiles were removed from the public repository on 2026-10-27; they remain in the local archive.

Small review previews only (full-resolution images, KTX2 files and Street View crops are in
`~/turku3d/data/textures/{refs,pilot}/` on the Mac and are never committed). Full record: `pilot.json`.
Rebuild: `make textures-pilot` (cached; no new Codex calls unless a prompt changes), previews only:
`make textures-pilot-previews`.

| facade | source | refs (Google SV, newest layer) | Codex label | Codex improve | output |
|---|---|---|---|---|---|
| `lr15_se`: Läntinen Rantakatu 15, river facade (office), 38.8 m, 1 storey, wood, 1809 | kantakartta footprint 103295893K, edge facing 151° | 4 partial views, 2019-06, 6–7 m away (promenade; the river-side panos 75 m away were >80 % hidden behind a terrace/quay in the depth proxy and rejected) | 49 s, 25k tokens: wooden_house, 14 bays, clapboard `#BDBBB0`, granite plinth 1.1 m, all 4 views usable but the facade was mid-restoration in 2019 | 83 s, 90k tokens (70k cached), 1 PNG 2176×722 | 6-bay repeatable strip (16.6 × 5.5 m) → crop to whole bays → 2048×512, seam ratio 1.86 → 0.0; KTX2 albedo 158 KB, ORM 104 KB, normal 796 KB |
| `aurakatu2_se`: Aurakatu 2 (City Hall) facade on Läntinen Rantakatu, 22.5 m, 2 storeys | footprint 1032958849, edge facing 151° | 1 full oblique (69°) + 2 frontal partial (2019-06) used; a 2026-07 view was labelled unusable (renovation wrap) | 34 s, 28k tokens: public_building, historicist, 7 bays, arched windows, salmon render `#B98B7E` over rusticated grey | 88 s, 94k tokens, 1 PNG 1756×896 | whole unique facade 2048×1024 (`--tile none`); KTX2 albedo 316 KB, ORM 173 KB, normal 1.58 MB |

Files: `*_refs.jpg` (reference crops with the target facade outlined), `*_codex_raw.jpg` (Codex output),
`*_albedo.jpg` (post-processed), `lr15_se_tile2x2.jpg` (wrap test), `*_maps.jpg` (DeepBump normal and height, ORM).

**Codex usage.** Weekly window (Pro): 1 % before the first pilot call (19:52), 1 % after all four calls
(20:01). The rollout float `used_percent` also read 1.0 after each call. The quota counter is an integer, so
four calls (2 label + 2 image generation, ≈ 236k input tokens, 63 % cached) cost < 1 % of the window. Another
reading earlier in the session was 0 %; other workstreams may share the account, so per-call cost is still not resolved.
Log: `~/turku3d/data/textures/pilot/usage_log.jsonl` and `run_*/usage.jsonl`.

**Observations.**
- Both outputs keep the real identity (bay rhythm, window shapes, pediments, granite plinth with cellar vents,
  City Hall arches and cornice) and are clean, rectified and free of clutter. Codex itself noted slight baked
  shading under the ornaments (as in the research: not pure albedo).
- LR15 was photographed mid-restoration in 2019. The prompt asks for the finished state in the colours of the
  restored part, so the texture is "inspired by" rather than a copy of the 2019 photo.
- The Codex `notes` came back in Finnish (`~/.codex/AGENTS.md`). The improve prompt now asks for English; the
  label schema already forced English enums.
- UASTC normals are large (0.8–1.6 MB at 2048 px). For the 300 MB / download budgets, consider 1024 px normals or ETC1S normals for
  distant facades.
