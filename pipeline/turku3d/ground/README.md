# turku3d.ground

Wave 1 ground producers publish collision and visual layers through `turku3d.layers`;
see [`docs/specs/layers.md`](../../../docs/specs/layers.md). Production packaging reads
those manifests and does not interpolate a fallback heightfield.

The retired Phase 0 interpolation and tile builder live under `pipeline/tests/fixtures/`.
They exercise heightfield interpolation, canonical-grid caching, orientation and shared
seams using synthetic inputs. Run their regressions with `make tile-test`.
