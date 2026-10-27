# AI-regenerated textures for the Turku game (Codex + local tools)

Date: 2026-10-25. Pilot area: Läntinen Rantakatu 15 (EPSG:3877 x 23459668, y 6704254).
Samples: `docs/research/ai-textures/`. Full runs (JSONL event logs, prompts, full-resolution outputs):
`osgallery-dev:~/turku3d/research/ai-textures/local-runs/` (also in this session's scratchpad).

Status labels: **VERIFIED** = observed in this session; **UNVERIFIED** = from documentation or prior knowledge, not tested here.

## TL;DR

- **Codex can generate and edit images non-interactively.** `codex exec` with a ChatGPT login has a built-in
  `image_gen` tool, reached through the system skill `imagegen`. No API key is needed. Each image takes about
  65–100 s. Eight calls (10 images plus one labelling job) left the weekly usage at **0 %** (Pro plan). **VERIFIED**
- Output lands in `~/.codex/generated_images/<thread_id>/exec-<uuid>.png`. The model then copies it into the
  workspace when the prompt asks for that. **VERIFIED**
- Quality is good: clean, rectified albedo images with no people, cars or umbrellas. They work for
  **material swatches, facade bays and trim strips**, and for an "inspired by" regeneration of a whole facade.
  Weak points: tiling is not guaranteed, edits do not keep pixels exactly, the style can drift (Turku clay roof →
  Mediterranean barrel tiles), "height maps" come out as grey shaded albedo, and there is no seed or size control. **VERIFIED**
- Multimodal labelling of facade photos into a JSON material and grid spec works: about 49 s for 2 images,
  with plausible values. **VERIFIED**
- **The remote Codex CLI (0.144.4) is too old for `gpt-6-astra`** ("requires a newer version of Codex").
  The Mac's Codex CLI 0.160.0 works. **VERIFIED**
- Recommended approach: a **shared material library plus trim sheets plus per-building specs**, not a unique
  texture for every building. Codex labels buildings and generates library entries. Local, deterministic tools
  then make the textures tile, derive the normal/ORM maps and pack KTX2.

## 1. Codex image capabilities (tested)

| Item | Finding | Status |
|---|---|---|
| Codex CLI on the Mac | `/Users/wnt/.local/bin/codex`, **0.160.0**, ChatGPT login, `features: image_generation stable true`, `view_image stable true` | VERIFIED |
| Codex CLI on osgallery-dev | `/usr/bin/codex` **0.144.4** (HANDOVER said 0.158). `codex exec -m gpt-6-astra` → HTTP 400 "The 'gpt-6-astra' model requires a newer version of Codex". Upgrade it (root-owned npm global) or install a user-prefix copy before running a batch there | VERIFIED |
| Image input | `-i file` (repeatable); all models in `models_cache.json` list `input_modalities: [text, image]` | VERIFIED |
| Image generation and editing | Built-in tool `image_gen.imagegen(prompt, referenced_image_paths?, num_last_images_to_include?, transparent_background?)`, from the tool declaration in a session log. It has **no size, quality, seed or n parameter**. The skill (`~/.codex/skills/.system/imagegen/SKILL.md`) says the built-in mode "Does not require OPENAI_API_KEY" | VERIFIED |
| Works in `codex exec` without interaction | Yes. `codex exec -m gpt-6-astra -C <tmpdir> --skip-git-repo-check --sandbox workspace-write --json - -i img < prompt.md` (retired research wrapper `ai-textures/call.sh`; maintained runner: `textures/turkutex/codex/runner.py`). `read-only` is fine for labelling, but generation needs `workspace-write` so the file can be copied into the cwd | VERIFIED |
| Where outputs go | `~/.codex/generated_images/<thread_id>/exec-<uuid>.png`. `thread_id` is the first JSONL event (`thread.started`). The image_gen call itself does **not** appear as an item in the `--json` stream; only messages and shell commands do. A batch runner should therefore collect results from that folder by thread id and not rely on the model's copy | VERIFIED |
| Output sizes seen | Square requests → 1254×1254. Portrait 1024×1536 matched the request. Landscape → ~2172×724 (3:1; 4:1 was asked) | VERIFIED |
| Latency | 64–98 s for one image, 169–193 s for two. Five calls in parallel finished in 193 s wall time without errors | VERIFIED |
| Usage | Weekly usage was 0 % before and 0 % after all 8 successful calls (`codex-usage --percent` on the Mac; Pro plan). Each call ≈ 117–138 k input tokens (≈ 100–120 k cached: the system prompt and skill) and ≈ 1 k output. The real cost per image under the plan cannot be seen at this granularity | VERIFIED (cost per image UNVERIFIED) |
| Language | `~/.codex/AGENTS.md` on the Mac tells Codex to answer in Finnish, so free-text JSON values came back in Finnish. Add "All JSON values in English" to the prompts, or run with a clean `CODEX_HOME`/profile | VERIFIED |
| OpenAI Images API (`gpt-image-2`, `gpt-image-1.5`, `gpt-image-1`, `gpt-image-1-mini`) | Reached through the skill's fallback `scripts/image_gen.py`, which **requires `OPENAI_API_KEY`**. The ChatGPT login does not supply one: `auth.json` has `auth_mode: chatgpt` and an empty `OPENAI_API_KEY`, locally and remotely, and the env var is unset. That route needs a paid API key (not created). The API path would add size (up to 3840×2160), quality, `n`, masks and up to 16 input images | VERIFIED (key absent); API pricing UNVERIFIED |

### Experiments (gpt-6-astra, built-in image_gen)

| # | Input | Task | Result | Seconds |
|---|---|---|---|---|
| t1 | Commons CC0 photo of Läntinen Rantakatu 27b (green wooden house) | Rectified, tileable, de-lit albedo of one facade bay | `albedo_bay.jpg`: excellent bay with clapboard, carved surround, granite plinth, dark glass and no clutter. Wraps horizontally visually (but see note) | 67 |
| t2 | Commons CC0 photo of LR 21 (1930s yellow block) | A new facade "inspired by" it: 6 storeys, 8 bays | `facade_regen.jpg`: clean orthographic elevation, 6×8 windows, door, 2 balconies. Usable as a building texture or for cutting a trim sheet | 93 |
| t3 | 5 cm true-ortho crop of the riverside promenade (people, shadows) | (a) tileable paving albedo, (b) granite quay-edge trim | `ground_paving.jpg` tiles well in 2×2 (`cmp_tiling.jpg`, right). `quay_edge_trim.jpg` is fine but 3:1, not 4:1 | 193 |
| t4 | Ortho crops of a clay-tile roof and a dark standing-seam roof | Two tileable roof albedos | Standing seam: good and tiles. Clay: tiles visually, but **style drift** to Mediterranean barrel tiles | 93 |
| t5 | Both facade photos | Structured JSON labelling (no generation) | `labels_t5.json`; see §2 | 49 |
| t6 | LR27b photo | "Improve" mode: rectify and clean the *same* building (remove umbrellas, chairs, ropes, signs, drainpipe) | `facade_cleaned.jpg`: very convincing, with layout and ornament kept. It invented one narrow window and a vent | 98 |
| t7 | t1 albedo | Matching height map | Pixel-aligned (gradient correlation 0.667, best at shift 0,0), but it is a **grey shaded albedo, not a height map**. Codex said so itself. A Sobel normal map made locally from it (`normal_bay_from_height.jpg`) is usable for coarse relief only | 169 |
| t8 | t4 clay tile, offset by half | Repair the centre seams while keeping the borders | Failed: border pixels changed by mean 6.7/255, it added a new horizontal misalignment (`cmp_tiling.jpg`, middle), and the Nordic tile shape was not applied. **Do seam work locally and deterministically** | 64 |

Codex calls: 8 successful on the Mac and 1 rejected on the remote (400, no usage). Within the ~10 limit.
Usage before and after: 0 % → 0 %.

## 2. Multimodal labelling into a procedural spec (tested)

Prompt `p5.md` → strict JSON per image (`labels_t5.json`). Highlights (VERIFIED output; plausibility checked by eye):

- LR21: render `#e8cc7e`, 6 floors, window grid 6×7, casement windows with 2 panes, simple surrounds, stone plinth
  0.5 m, gable roof in clay tile `#805a4d` at ~28°, occluders: trees, hedge, fence. Confidence 0.62
  (heavy tree occlusion; it reported `has_attic_or_dormers: true`, which is doubtful).
- LR27b: wood clapboard `#929b85`, 1 floor plus dormers, 5 bays, ornamented surrounds, rusticated granite
  plinth ~1.0 m, standing-seam metal roof `#666874` at ~40°, ground floor in use as a restaurant, occluders listed. Confidence 0.87.

These specs map directly onto a procedural facade generator: grid → modules, materials → library IDs, hex
colours → per-building tint. Window counts and heights should be cross-checked against lidar (eave height, roof
type) and against the footprint length / bay width. The two sources do not need to agree 1:1, because the game
city is "inspired by" the real one.

## 3. Open-source and local tools to complement Codex

None of these were installed or benchmarked in this session. They are **UNVERIFIED** unless noted. On the Mac:
Blender 5.2.1, numpy and Pillow are present (VERIFIED). torch, scipy and KTX tools are not installed (VERIFIED).
The remote has 10 CPUs, 48 GB RAM and no GPU.

| Need | Tools | Fit |
|---|---|---|
| Deterministic seamless tiling | Offset + local inpaint / Poisson blend (numpy/OpenCV), Material Maker "make tileable", ComfyUI circular-padding ("seamless") nodes, Texture Lab | Required: t8 shows that Codex edits do not preserve pixels |
| Normal / height / roughness / AO from albedo | DeepBump (Blender add-on, ONNX, CPU), Material Maker, classical Sobel from luminance (done here in numpy), Marigold normals (diffusion, MPS feasible), RGB↔X (albedo, normal, roughness and metallic from a photo; SD-based) | DeepBump in the installed Blender is the cheapest start |
| Generating PBR materials locally | StableMaterials, MatFuse, ControlMat (photo → SVBRDF), Material Palette (material regions from one photo → tileable PBR) | Good for a library; slow on MPS (minutes per 512² set) |
| Diffusion texture synthesis | ComfyUI on MPS + SDXL/FLUX.1 (schnell/dev) + ControlNet Tile/Canny conditioned on ortho or facade, seamless nodes | Offline alternative to Codex; FLUX on M1 Max ≈ 1–3 min per 1024² image (estimate) |
| Delighting / intrinsic decomposition | Careaga & Aksoy ordinal-shading intrinsic (CPU/MPS), Marigold-IID, RGB↔X | Useful in "improve real photo" mode for hero buildings |
| Upscaling ortho / pano crops | Real-ESRGAN (ncnn-vulkan has a macOS build), SwinIR, chaiNNer with 4x-UltraSharp | Ortho at 5 cm is too blurry for close-up ground (see `crop_promenade.png`). Use it only as a layout/colour/decal mask, not as detail |
| Texturing whole 3D meshes | TEXTure, Text2Tex, Paint3D, DreamMat, TRELLIS / Hunyuan3D-paint | Mostly CUDA-only and made for single objects. Poor fit for a city of buildings; perhaps for props |
| Compression for the browser | KTX-Software `toktx`, `basisu` (ETC1S for albedo, UASTC + RDO for normals), glTF-Transform `ktx2` | Required for the WebGPU/WebGL2 build |

## 4. Proposed pipeline

Principle: **AI makes reusable assets; geometry and specs come from data.** Lidar and footprints give exact
shape and collision. Codex gives semantics and art. Local tools make the output tile and pack it.

```
per building (footprint + lidar roof)                 per ground tile (100–250 m)
  ├─ refs: Street View / Mapillary crops (2–4),         ├─ refs: true-ortho 5 cm crop (2022), 2025 ortho
  │   ortho roof crop, lidar eave/ridge heights         │   + kerb/road vectors, lidar DSM
  ├─ Codex LABEL (read-only, ~25–50 s/bldg)            ├─ Codex LABEL (segment surfaces: asphalt, slab, sett,
  │   → spec.json {style, floors, bays, materials,      │   grass, kerb types → material IDs per polygon)
  │      colours, roof, ground-floor use, occluders}    │   or a classical classifier on ortho RGB + DSM
  ├─ MATCH spec → material library + facade module set  ├─ MATCH → library ground materials + decals
  │   (miss → enqueue Codex GENERATE job for new        │   (manholes, stains, markings as atlas decals)
  │    library entry / trim sheet)                      │
  ├─ hero/landmark? → Codex IMPROVE (t6-style cleaned   └─ splat/ID map (8-bit) + per-tile tint, no unique
  │   facade) → unique texture                              albedo per tile
  └─ procedural facade mesh (Blender/Python): modules on the bay grid,
     UVs into trim sheets, per-building tint + wear seed in a vertex attribute

library job (Codex GENERATE, ~80 s/img) → local post: make tileable (offset+inpaint) → normals/height
  (DeepBump/Sobel) → roughness/AO heuristics → QA metrics (seam test, colour range) → KTX2 (ETC1S/UASTC)
  → texture arrays / trim sheets
```

Outputs:
- **Material library**, about 150–300 entries: 1024² albedo, normal and ORM, stored as KTX2 in texture arrays
  (ground, walls, roofs).
- **Facade trim sheets**, about 20–40 style families (Turku wooden house, 1930s functionalist render, 1960s
  concrete element, Jugend/stone, red brick, modern glass). Each is a 2048² trim sheet plus 2–4 "bay" atlases
  (t1 / t2-style images, sliced).
- **Per-building data**: `spec.json` plus tint and wear. **Unique textures only for hero buildings** (t6-style
  cleaned facades), 2048×1024 each.

Browser memory (estimate, UNVERIFIED until measured): BC7/ASTC is 1 byte/px, so a 1024² map with mips ≈ 1.4 MB
and three maps ≈ 4.2 MB per material. A 64-material resident set is about 270 MB. That is too high, so stream by
district, keep most materials at 512² (≈ 1 MB per set) and use 1024² only for ground and street level. Trim sheets
let one 2048² set (≈ 17 MB) cover hundreds of buildings. Target ≤ 300 MB of GPU texture memory in total. KTX2
ETC1S download ≈ 0.1–0.3 MB per 1024² albedo.

Consistency between neighbouring buildings:
- Generate each style family in **one Codex job**, with a shared "style bible" reference image passed to
  every call.
- Make per-building variation with shader tint and wear masks, not new images.
- Generate a district palette once (hex colours from labelling, clustered).
- Keep ground continuous with splat maps across tile borders.

Caching and reproducibility: image_gen has no seed, so treat outputs as **content-addressed assets**. Use the key
`sha256(prompt_version + input image hashes + model)` and keep every accepted image, its prompt and its thread_id
in `library/manifest.jsonl`. Never regenerate an accepted asset unless its prompt version changes.

Batch orchestration, reusing `inventory/run-codex.sh` and the objects scheduler from leikkipuistodata:
- `jobs.jsonl`, 4–5 parallel runners (5 in parallel was VERIFIED here).
- Each job runs in an isolated temp dir (`-C tmp --sandbox workspace-write`, copies only of its inputs).
- Check `codex-usage --percent` before every call and stop at ≥ 70 % (exit 3).
- Collect from `~/.codex/generated_images/<thread_id>/`. Write results and failures to `out.jsonl` and
  `failed.json` so a rerun is resumable.
- Run QA scripts (seam ratio, size, colour histogram, a cheap Codex "accept/reject" vision check if needed).
- Run on the **Mac** (CLI 0.160) or upgrade the remote CLI first.

Cost and time:
- **Library bootstrap:** 200 materials + 30 trim families ≈ 300 images × ~80 s ≈ 6.7 h serial, ≈ 1.5 h with 5
  runners. VERIFIED rate; total UNVERIFIED.
- **Per building:** labelling ≈ 25–50 s (VERIFIED 49 s for 2 photos). For about 500 buildings/km² in the dense
  centre (UNVERIFIED count): ≈ 3.5–7 h serial, ≈ 1–1.5 h with 5 runners.
- **Hero buildings:** about 5 % get a cleaned facade at ~100 s each.
- **Per km²:** once the library exists, ≈ 1.5–2 h of wall time with 5 runners.
- **Weekly-quota share:** unknown. The 8 calls here stayed at 0 %, so the next step is a 30–50 call pilot that
  logs the percentage after every 10 calls.

## 5. Risks and open questions

- No size, seed or quality control in the built-in tool. Square images come out at 1254² and need resampling.
  For exact sizes, masks or `n` variants, the Images API needs a paid API key.
- Edits re-render the whole image (t8), so you cannot do pixel-precise inpainting through Codex. Seams, alignment
  and the extra maps (normal, height, ORM) must be made locally.
- Style drift toward generic or Mediterranean looks (t4 clay tile). Prompts need a Turku/Nordic "style bible"
  and reference crops, plus a QA pass.
- Real reference photos: Street View terms restrict derivative use. Prefer Mapillary (CC BY-SA) and
  Commons CC0/CC BY photos, and your own photos. Generated "inspired by" textures sidestep most of this.
  Not legal advice.
- Remote CLI version mismatch: the batch must run on the Mac or after a CLI upgrade.
- The quota cost per image under ChatGPT Pro is unmeasured. The research wrapper used a 70 % cap guard; it was retired in Wave 0 in favor of the maintained textures runner.

## Verification

An adversarial check, run on 2026-10-25 at about 16:45–16:50 UTC. It used **3 Codex calls**:
- v1: image generation on the Mac in a read-only sandbox.
- v2: a remote `gpt-6-astra` probe, which was rejected.
- v3: a labelling rerun.

Weekly usage was 0 % before and 0 % after (Pro plan). Artifacts are in `docs/research/ai-textures/verify_*`.

| # | Claim | Verdict | Evidence / correction |
|---|---|---|---|
| 1 | `codex exec` can generate images non-interactively with no API key, and the files land in `~/.codex/generated_images/<thread_id>/` | **CONFIRMED** | v1 ran in 47 s, rc 0, and wrote `exec-2d5c….png` under the thread_id from `thread.started`. The model also printed the path. `auth.json` has `auth_mode: chatgpt` and no key, both locally and remotely. |
| 1a | "Generation needs `--sandbox workspace-write`" | **REFUTED** | v1 generated fine with `--sandbox read-only`. `workspace-write` is only needed if you want the model to copy the file into the cwd. A batch runner should use `read-only` and collect from `generated_images` itself, which is safer. |
| 1b | "The image_gen call is not visible, so collect by thread_id" | **PARTLY CORRECTED** | The call is missing from the `--json` stream, but it **is** logged in `~/.codex/sessions/YYYY/MM/DD/rollout-*-<thread_id>.jsonl` as a `custom_tool_call` to `tools.image_gen__imagegen({prompt: …})`. Use it as the provenance log (exact prompt). The generated image is also fed back into context as `input_image`, which explains the ~60–138 k input tokens per call. |
| 1c | "8 calls → 10 images" | **CORRECTED** | `generated_images` holds **12** PNGs from the 7 generating calls. t2, t6 and t7 each produced **2 images** where 1 was requested: hidden retries or variants. The collector must handle more than one PNG per thread, for example by taking the one the model referenced or the newest. Usage per call is also higher than the deliverable count suggests. |
| 1d | No size control; square output is 1254² | **CONFIRMED** | v1 asked for 2048×2048 and got 1254×1254. |
| 2 | Remote CLI 0.144.4 rejects `gpt-6-astra` | **CONFIRMED** | v2 returned the same HTTP 400 "requires a newer version of Codex". Remote usage is still 0 %. |
| 3 | Weekly usage stayed at 0 %, so the cost is negligible | **WEAKENED** | `codex-usage --percent` returns an **integer** `usedPercent`. The weekly window **reset at 18:33 local (16:33 UTC) today** (`resetsAt` = 2026-11-01 18:33), which is exactly when the experiments started. The "before" reading (16:32 UTC) was taken in the *previous* window. "0 % → 0 %" therefore only says that 11 calls cost < 1 % of a fresh Pro window. It does not show a per-image cost. The 30–50 call pilot remains necessary. |
| 4 | Output quality is usable as game textures; clay roof drifts Mediterranean | **CONFIRMED, drift is fixable** | v1 asked explicitly for a "Finnish interlocking double-S, NOT Mediterranean barrel" tile and got a correct Nordic pantile (`verify_claytile_nordic_2x2.jpg`). Drift is a prompt problem, not a model limit, so put the style bible in prompts. |
| 4a | Tiling: "clay roof tiles visually"; paving and seam tiles fine | **PARTLY REFUTED** | Wrap-seam / interior-gradient ratio (1.0 = invisible): ground_paving 1.3/1.1 and standing seam 1.3/1.2 are good. **roof_claytile (t4) X = 5.8**, a visible vertical seam. albedo_bay Y = 6.4, which is expected because the bay has a plinth and only wraps horizontally. v1 clay is 1.4/1.7, but its top/bottom brightness differs by 14/255, a low-frequency gradient that shows as banding over large roofs. **Every Codex texture needs the local make-tileable pass plus a low-frequency flatten (high-pass / histogram equalise); none should be trusted as tileable.** |
| 4b | t6 "invented one narrow window and a vent" | **LIKELY WRONG** | The reference photo has a narrower window and a louvred vent at the right end. The edit is more faithful than claimed. It did drop the signage transoms and the dormers and roof, because it was cropped to the wall. |
| 4c | De-lit albedo | **MOSTLY** | Facade bays still carry baked contact shadows under sills and surrounds (AO-like). That is acceptable for a game look, but it is not pure albedo, so do not add strong SSAO on top. |
| 5 | Labelling gives usable structured JSON | **CONFIRMED with caveats** | v3 reran the same prompt plus "English values" in 31 s. Floors, roof type and material, wall material and heights were stable. **Window cols / bays changed by ±1 on both buildings** (LR21 7→6, LR27b 5→6). ground_floor_use flipped (shop→residential). Pitch varied 3–5°. Enum fields came back as free text ("casement with transom", "rough-faced stone blocks", "rendered masonry"). `has_attic_or_dormers: true` together with `roof.has_dormers: false` is internally inconsistent. **Fix:** use `codex exec --output-schema <schema.json>` with strict enums (VERIFIED: the flag exists in 0.160), take bay counts from lidar or footprint length (Codex value as a prior only), and optionally take the majority of 2–3 runs for hero buildings. |
| 6 | Local tools fit on the M1 Max (were UNVERIFIED) | **PARTLY VERIFIED** | **DeepBump** (ONNX, onnxruntime 1.31 CPU, CoreML EP available) turned the 1254² albedo into normals in **4.9 s** and the normals into a seamless height map in **0.5 s**, with plausible output (`verify_deepbump_sheet.jpg`). This replaces the useless Codex "height map" (t7). **KTX2:** Homebrew has no `ktx-software` formula, but npm `ktx2-encoder@0.4.2` (basis_universal WASM) in Node encoded a 1024² albedo to **ETC1S + mips = 164 KB in 5.1 s** and a normal map to **UASTC = 1.26 MB in 9 s** (no zstd). That confirms the 0.1–0.3 MB albedo estimate. UASTC normals need zstd/RDO to be web-friendly, or use KTX-Software `toktx --zcmp` from the GitHub pkg. Still UNVERIFIED: ComfyUI/SDXL/FLUX speed on MPS, StableMaterials, Material Palette, RGB↔X, Marigold, Real-ESRGAN. |
| 7 | Time estimates (1.5 h library with 5 runners; 1.5–2 h per km²) | **PLAUSIBLE, slightly optimistic** | The arithmetic is consistent with the measured 47–100 s per call. Hidden duplicate generations (1c), the local post-processing and the QA rejects/reruns are not counted, so budget about +30–50 %. |

**Net effect on the architecture:** the architecture holds: a material library plus trim sheets plus spec-driven procedural facades, with Codex as the generator and labeller, and deterministic local post-processing. Changes:
- Run generation with `--sandbox read-only`.
- Collect by thread_id, handling several PNGs per thread, and log prompts from the session rollout.
- Make label JSON strict with `--output-schema`.
- Make the tileable pass and a low-frequency flatten mandatory.
- Use DeepBump for normal and height maps, and ktx2-encoder or toktx for KTX2.
- Measure the quota share before scaling, because the 0 % reading is not evidence of zero cost.
