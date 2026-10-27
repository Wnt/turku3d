## Result: DONE

## What changed

Registered facade_kit.test.mjs as a knip entry and added it to package-js-tests alongside prop_atlas.test.mjs. Existing regression assertions are preserved. Also registered the documented standalone Kupittaa QA CLI introduced by the concurrent main landing.

Voice checks now read committed audio/voice/addresses.json instead of published tile data. Voice-build still collects published pools and writes the voiced address inventory after successfully generating clips. Generated the 50 missing address clips (483 existing clips reused). Added a synthetic inventory regression test and checked the runtime fallback: unknown address clips leave the full HUD text visible without attempting unavailable audio.

## How to verify

```sh
source ../sandbox.env
node --test pipeline/turku3d/package/prop_atlas.test.mjs pipeline/turku3d/package/facade_kit.test.mjs
npx knip --no-progress --include-entry-exports
make voice-test voice-check
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

## Gate output

Package JS tests: 2 passed (1.24 s). Knip: passed (2.5 s).
Voice tests: 19 passed (1.91 s); voice-check passed.
Fast gate: GREEN (5.014 s), including policy, lint, type checks, voice tests and game unit tests.
Voice-build: 50 synthesized and encoded, 483 reused, none removed (63.4 s synthesis/build).

## Follow-ups

None.
