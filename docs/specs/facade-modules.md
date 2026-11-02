# Runtime facade modules

`game/src/render/facadeModules.ts` derives solid frames and projecting sills from
the tile's metric facade program. It applies to every streamed tile, without
building IDs, coordinates, reference images or additional tile payloads.

Reviewed rectangular opening patches retain their existing recess. Procedural
windows use the same bay pitch, alternating phase, floor heights, opening ratios
and frame/trim colours as the facade shader. Ground-floor entrance bays,
shopfronts and plain bases are excluded. Roof-shaped walls and passage cuts use
UV-triangle coverage: the entire proposed window must lie on surviving wall.
Arches and partial reviewed openings retain their existing treatment.

The shared box family has five instances per window: four frame members and a
sill, totalling 60 triangles and 380 bytes of instance transforms/colours. Frame
width (up to 55 mm) and sill projection are bounded appearance priors, rather
than survey measurements. Pools occupy 32 m spatial cells; detail disappears
110 m from the cell centre. Pool geometry and untextured PBR materials are
shared through the existing material lifecycle. Collision remains the surveyed
building shell. Distance-LOD instances keep their own shadows instead of joining
the permanent tile shadow batches.

Streaming construction shares the renderer's 2 ms preparation budget. Parsing and
instance-buffer assembly yield in bounded chunks; the measured CPU time, maximum
slice and elapsed time are retained on the pool root. Recentring a cell translates
its existing bounds instead of rescanning every box. A 1 mm conservative margin
covers the second Float32 transform upload.
