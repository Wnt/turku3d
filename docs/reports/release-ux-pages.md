# Pages release UX (2026-10-28)

Public live and manual Pages builds select `VITE_PUBLIC_RELEASE=1`. Their start menu offers only Multiplayer,
and the top-left HUD shows the server-assigned nickname. F3/H toggles diagnostics, including the draft banner;
`?debug` starts with diagnostics. Local builds keep all start options and default diagnostics.

`make game-build` with the public target freezes two production variants: `build/game` for publication and
`build/check-game` for existing solo gameplay Chrome checks. Both travel through the stage cache and internal
release. `E2E_GAME_DIST` selects the companion for the integrator; Pages copies only the public variant.
Existing gameplay assertions remain intact. Ordinary local builds still produce the complete local menu.

The initial HTML contains the loading screen before the entry bundle arrives. Real loader hooks report active
work, completed/discovered tasks, bytes and timings: renderer/adapter, Rapier/WASM and collider construction,
visual tiles, embedded and external KTX2 transcodes, audio decode, voice indexing and multiplayer welcome.
The log is bounded and instrumentation stops after entry. The compact menu dock fits the desktop gutter;
joining returns to the full screen, which fades after placement and the first visible model allow movement.
The separate `fix-preload-tiles` task owns the stronger 360-degree preload and GPU warmup wait; it will consume
its worldLoad snapshot and feed the documented loading.begin/note hooks after rebasing onto this change.

The UTC cycle has an anchor of `2027-10-15T05:45:00Z`, a 1,800-second day and a 10-second skip. The clock runs
08:45–17:30 Europe/Helsinki, then fast-forwards through the skipped hours with the local date fixed to 15.10.2027.
Lighting returns smoothly through daylight; the solar disc fades during the middle of the rapid sweep to avoid
an HDR bloom flash. Directional light and shadows remain active. Sun/sky update each frame using the existing
shadow passes. PMREM is reused for 30 seconds and throughout the skip. `?time=` and QA cameras remain fixed;
the previous fixed QA default is unchanged. The slider and its code are deleted.

Foot, fly and car help progress is independent and stored per browser. Help hides after 60 seconds of active
exposure in that mode, or two distinct uses of at least 75% of its prompts. Pauses/menus do not spend the timer;
held input does not count repeatedly. Storage access is guarded. Five small unit cases cover the cycle, date,
transition brightness floor, solar-disc fade, help timeout, threshold and separate mode progress.

## Verification

```sh
source ../sandbox.env
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
scripts/dev/codex-task wait-cmd -- .venv/bin/python scripts/dev/tests/run.py test_live_build.py test_served.py test_disk_retention.py
scripts/dev/codex-task wait-cmd -- env VITE_PUBLIC_RELEASE=1 make game-build
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py browser -- node game/scripts/release-ux-qa.mjs
```

The standalone Chrome helper pins the real served tiles, starts only isolated sandbox servers, and writes
`$TURKU3D_WORK/reports/release-ux/` (WebGPU) or `release-ux-webgl/` with `UX_QA_BACKEND=webgl`.
WebGPU completed the menu/loading/HUD/F3/daylight/transition/help/persistence/fixed-time checks without browser
errors. Local development and the local production companion both showed the full menu. The WebGL run rendered
the loading, public menu, HUD and all daylight/transition views without browser errors, then its harness rejected
the valid one-minute help timeout because that assertion required key learning only. The assertion now accepts
the specified OR rule; the full optional WebGL harness was not rerun. Screenshots and the diagnostic are retained.
No browser specs were added; the integrator retains its existing suite.

At 1280x720 on M1 Max/Metal, the final WebGPU clock averaged about 0.0085 ms per frame including periodic work;
its scheduled IBL maximum was 1.62 ms. Frame p95 and draw counts are recorded in report.json. Host load ranged
well above 100 during these checks, so the paired frame figures are observations rather than an unloaded FPS
claim. The clock adds no shadow pass; the daylight and fixed-time views used the same draw count. Thirty-two
focused release/cache/tooling tests passed. Browser use stayed below the 25-minute brief budget.

## Final rebase repair

Main's `f3d8add4` introduced the dispatcher's 30-second repeat delay while leaving two early-reminder assertions and a 607-line module. The release branch preserves the delay, moves the shared speech representation/timing helpers to `missions/speech.ts` (retaining the lineSeconds re-export), and strengthens both assertions: silence after the announcement and at 29.9 seconds, then the address after crossing 30 seconds. All 48 focused mission/order tests pass.
