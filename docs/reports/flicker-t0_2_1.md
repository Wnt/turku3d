# Facade flicker at Vartiovuorenkatu 2a

The 2026-10-29 18:19 recording resolves to building **103440141R** (OSM
way/89568494), source part `Building_95061059`, address **Vartiovuorenkatu 2a**.
The long wall is `103440141R_a12dce365b`; its short end is
`103440141R_9b426bfd50`. Raycasting the recorded pose into the downloaded OCI GLB
hits the long wall at engine (635.173, 25.921, 383.352), 23.401 m away.

## Cause and scope

The competing appearances are different facade-kit instructions interpolated
within the **same triangles**, rather than two resident surfaces. The recorded
building ships one kit wall object, with no stored facade-map object or component
instances. Its wall program reports no rendered 3D components. The decoded
near-coplanar wall check finds zero overlapping wall triangles on this building.
The client removes the old first-pass model before adding its full replacement.
The batch identity in the HUD concealed the source identity.

`assemble_structures` splits triangles to subtract coincident architectural
surfaces. When any triangle changes, it expands *every* triangle in that mesh to
three independent vertices. It previously retained `facade_kit_walls` ranges in
the old indexed-quad vertex order. The binder then assigned instructions to the
wrong vertices and left the tail at zero. Interpolated module IDs, origins and
sizes produced triangular texture mixtures and unstable atlas selection.

A read-only audit of 727 existing OCI cached L0 manifests found **232 distinct
buildings across 19 tiles** with mixed per-triangle program fields. In `t0_2_1`,
14 buildings and 955 triangles had mixed fields; the target had 28 triangles
mixing `_KIT_LAYOUT` alone. These are measured cached-artifact counts, not a claim
about every possible future tile. Local raw evidence remains in the agent's
sandbox reports; no reference imagery or city geometry is committed.

## Generic correction

The shared assembler remaps instructions for every output vertex, including
untouched triangles in a changed mesh, and invalidates box-instancing hints on
cut geometry. The binder requires complete, disjoint ranges and one instruction
per triangle. The final decoded shipping audit checks all program attributes and
expands every GPU instance with its node transforms at float32 precision. It
checks vertical faces from **all layers**, including props and vegetation, and
stores overlap identities/counts in `visual[tile].wall_surfaces`. Mixed program
attributes fail packaging. Surface overlap diagnostics remain nonfatal for live
availability, as required by the existing generator policy, but the selective
OCI acceptance probe requires zero overlaps.

The old wall invariant was both geometrically limited and too early: it ignored
props/vegetation, ran before instancing/packing, discarded its report, and could
not detect corrupted shader attributes. Props now enter wall assembly with their
prototype transforms applied; this also removes the four pre-existing sign-face
overlaps found in the target tile's decoded artifact. GPU vegetation remains
instanced and is expanded only for the final audit.

The HUD uses raycast triangle index ranges and instance indices to resolve the
source building. Component instances retain source metadata; roofs retain their
building ID; matched OSM street/house-number addresses reach both visual passes.

## Verification

Selected checks cover wall retessellation, facade components, binder validation,
final transformed instance audits, HUD ray hits and TypeScript compilation.
`tools/research/flicker/audit-programs.mjs ROOT` audits existing manifests without
building tiles. `python3 tools/research/flicker/probe.py` builds `t0_2_1` and
`t0_2_0` on the exact deployed OCI source, first and full, verifies source counts,
checks all shipped triangles and surface diagnostics, and compares rolling health.
Deployed results are recorded after the bounded asynchronous run.
