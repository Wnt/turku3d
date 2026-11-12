# Source-qualified primary signs

The A3 frozen municipal input contains 116 supported sign-code records inside
the playable bounds: 37 E1 crossing signs, 21 E2 parking signs, 12 C38 parking
restrictions, 11 C37 stopping restrictions, nine B5 yield signs, 11 D3.1 keep-right
signs, nine E6 bus-stop signs and six H20 supplementary plates. The prepared
producer previously selected only E2 and the separately recovered C1 code.

The extension reuses the existing E1, B5/B6, C37/C38 and D3.1 prototypes, including
their code-specific shapes, procedural face atlases and mounting-height priors.
It does not treat a supplementary plate or bus-stop record as a newly qualified
standalone assembly. Existing C1/E2 products retain their earlier placement
contract. All emitted sign meshes have explicit fixture/support identity and
the furniture collision material.

Municipal point records contain a stable identity, code and plan position but
no heading or mounting relationship in this frozen layer. Cached primary sign
yaw comes from the existing right-hand-traffic source adapter. It is an inferred
approach direction, not a measured heading. Centreline/no-road defaults are not
accepted for newly added plates.

Several crossing records are separated by only 0.1–0.3 m. These may represent
shared or back-to-back plates, and adding a pole for each would invent an
assembly. New primary signs are therefore held when another municipal plate
is within 0.6 m. This is an uncertainty filter, not a surveyed pole dimension.
In A3 it leaves 33 isolated candidates before contact filtering: seven E1,
four B5, eight C37, ten C38 and four D3.1. Held clusters require registered photo
or mounting evidence before geometry is emitted. No source positions move.

The complete post footprint must contact the final declared support within
the unchanged 0.03 m allowance; unsupported new candidates remain held. Focused
tests cover cluster/default-heading rejection, supported prototype/contact and
actual encoded collision material, and a post crossing the coverage boundary.
Final counts, paired normal-game views and deployment acceptance belong to the
subsequent sign release, not to this source-input commit.
