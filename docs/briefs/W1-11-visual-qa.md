# W1-11: Visual QA loop against real photos, judged by Codex Astra (Phase 1 task 1.17)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. You own `qa/`.

## Goal

Automated "does it look like the real place" feedback. Render the game from the exact pose of a real reference photo,
give Codex gpt-6-astra both images, and get a structured list of fixes mapped to the asset that needs fixing. Phase 0
built the tooling and tested it only against degraded photos and a stub render.

## Deliverables

1. **About 20 reference views** around the office block, from all sources:
   - Street View and Look Around perspective crops.
   - Mapillary images (token in `~/.config/turku3d/mapillary.token`; never print or commit it).
   - The turku.360tour.fi drone pano "View over the River Aura", solving its heading offset.
   - Google Maps user photospheres: the 2023 boat-level ones facing the Läntinen Rantakatu facades.

   Each view gets a pose confidence, and the pose is refined against the lidar where possible. Skyline matching
   generalised poorly, so try feature matching against lidar or DSM renders, and record what works.
2. **The real game render**: `make tile-qa` renders every view (colour plus ID pass) from the assembled AOI tiles.
3. **Judge at scale**: run the Astra judge on all views in parallel, with the quota check.
   - Back-map each issue's bbox to object IDs: building, facade, material, tile.
   - Write `qa/issues.jsonl` and an HTML report with side-by-side images, issue boxes and scores.
4. **Regression score**: store per-view scores per commit, add a `make qa-regress` that fails when the mean score
   drops by more than a set margin, and keep a trend chart in the report.
5. **Calibration**: measure judge consistency. Run 3× on the same pair and report the score variance. Tune the prompt
   so trivial differences (cars, people, season, weather) do not dominate.

## Acceptance

- 20 views render at their poses. A report with real issues on the current build is attached in the result.
- Issues map to asset IDs.
- Judge variance is measured and documented.
- Quota use is reported.
