"""Reusable street-furniture geometry in metres, with explicit evidence limits.

The heritage lantern silhouette is informed by private reference Vastavalo 509952
(Kirjastosilta approach, filename dated 2014-10-19). No reference pixels are used
in the geometry or materials. Its stepped plinth, taper, crossarm, four glazing
panels and finial are visible observations. Dimensions below are engineering
priors, NOT surveyed measurements or evidence of an installation at the office.
The caller owns placement provenance and must choose this family explicitly.
"""
from __future__ import annotations

import math

HERITAGE_LANTERN = {
    "family": "turku-heritage-lantern-v1",
    "dimensions": [0.68, 4.8, 0.54],
    "emitterOffset": [0.0, 4.13, 0.0],
    "source": "Vastavalo 509952, 141019-048; private visual observation",
    "dimensionConfidence": "inferred prior; unregistered photograph",
    "heightUncertaintyM": 0.8,
    "placementConfidence": "not supplied by family",
}


class _Geometry:
    def __init__(self):
        self.positions = []
        self.indices = []

    def face(self, points):
        start = len(self.positions) // 3
        self.positions.extend(v for p in points for v in p)
        for i in range(1, len(points) - 1):
            self.indices.extend((start, start + i, start + i + 1))

    def taper(self, y0, y1, r0, r1, segments=12, cx=0.0, cz=0.0, phase=0.0):
        """Closed, outward-wound polygonal frustum; separate faces retain edges."""
        lower = [(cx + r0 * math.cos(phase + i * math.tau / segments), y0,
                  cz + r0 * math.sin(phase + i * math.tau / segments)) for i in range(segments)]
        upper = [(cx + r1 * math.cos(phase + i * math.tau / segments), y1,
                  cz + r1 * math.sin(phase + i * math.tau / segments)) for i in range(segments)]
        for i in range(segments):
            j = (i + 1) % segments
            self.face((lower[i], upper[i], upper[j], lower[j]))
        self.face(tuple(reversed(upper)))
        self.face(lower)

    def box(self, center, size):
        x, y, z = center
        a, b, c = (v * 0.5 for v in size)
        lo = [(x-a,y-b,z-c),(x+a,y-b,z-c),(x+a,y-b,z+c),(x-a,y-b,z+c)]
        hi = [(px,y+b,pz) for px,_,pz in lo]
        self.face(lo)
        self.face(tuple(reversed(hi)))
        for i in range(4):
            j = (i + 1) % 4
            self.face((lo[i],hi[i],hi[j],lo[j]))

    def beam(self, start, end, radius, sides=6):
        direction = [b-a for a,b in zip(start,end)]
        length = math.sqrt(sum(x*x for x in direction))
        axis = [x/length for x in direction]
        other = (0,1,0) if abs(axis[1]) < 0.9 else (1,0,0)
        u = [axis[1]*other[2]-axis[2]*other[1],axis[2]*other[0]-axis[0]*other[2],axis[0]*other[1]-axis[1]*other[0]]
        ul = math.sqrt(sum(x*x for x in u)); u = [x/ul for x in u]
        v = [axis[1]*u[2]-axis[2]*u[1],axis[2]*u[0]-axis[0]*u[2],axis[0]*u[1]-axis[1]*u[0]]
        rings = [[tuple(c[k]+radius*(u[k]*math.cos(i*math.tau/sides)+v[k]*math.sin(i*math.tau/sides)) for k in range(3)) for i in range(sides)] for c in (start,end)]
        for i in range(sides):
            j=(i+1)%sides
            self.face((rings[0][i],rings[0][j],rings[1][j],rings[1][i]))
        self.face(tuple(reversed(rings[0]))); self.face(rings[1])

    def record(self, name, material, collision, position, yaw):
        c, s = math.cos(yaw), math.sin(yaw)
        transformed=[]
        for i in range(0,len(self.positions),3):
            x,y,z=self.positions[i:i+3]
            transformed.extend((position[0]+c*x+s*z,position[1]+y,position[2]-s*x+c*z))
        return {"id":name,"materialId":material,"positions":transformed,"indices":self.indices,"collision":collision}


def heritage_lantern(position: tuple, yaw: float = 0) -> list[dict]:
    """Return three meshes at an explicit support position (Y up, yaw radians).

    IDs are entity-local suffixes. The compiler adds entityId/supportId and an
    emitter at ``HERITAGE_LANTERN['emitterOffset']``. A solid plinth and shaft
    supply contact; elevated trim and glazing have no needless triangle collider.
    Materials: iron linear RGB (.025,.03,.035), glass (.75,.58,.33).
    """
    if len(position) != 3 or not all(math.isfinite(v) for v in (*position,yaw)):
        raise ValueError("Lantern position and yaw must be finite")
    base, trim, panes = _Geometry(), _Geometry(), _Geometry()
    base.box((0,0.045,0),(0.42,0.09,0.42))
    base.taper(0.09,0.15,0.24,0.19,8,phase=math.pi/8)
    base.taper(0.15,0.60,0.18,0.13,8,phase=math.pi/8)
    base.taper(0.60,0.66,0.15,0.15,12)
    base.taper(0.66,0.72,0.15,0.095,12)
    base.taper(0.72,3.72,0.095,0.045,12)
    trim.taper(3.68,3.76,0.065,0.065,12)
    trim.beam((-0.34,3.58,0),(0.34,3.64,0),0.023,8)
    trim.taper(3.76,3.86,0.055,0.13,8)
    # Square rings use a circumradius; 45 degrees yields flat +/-X and +/-Z sides.
    phase=math.pi/4
    trim.taper(3.84,3.89,0.15*math.sqrt(2),0.15*math.sqrt(2),4,phase=phase)
    trim.taper(4.36,4.41,0.25*math.sqrt(2),0.25*math.sqrt(2),4,phase=phase)
    lower=[(sx*0.14,3.89,sz*0.14) for sx,sz in ((1,1),(-1,1),(-1,-1),(1,-1))]
    upper=[(sx*0.24,4.36,sz*0.24) for sx,sz in ((1,1),(-1,1),(-1,-1),(1,-1))]
    for i in range(4):
        j=(i+1)%4
        panes.face((lower[i],upper[i],upper[j],lower[j]))
        trim.beam(lower[i],upper[i],0.016,6)
    trim.taper(4.41,4.46,0.27*math.sqrt(2),0.27*math.sqrt(2),4,phase=phase)
    trim.taper(4.46,4.66,0.27*math.sqrt(2),0.055*math.sqrt(2),4,phase=phase)
    trim.taper(4.66,4.70,0.06,0.06,8)
    trim.taper(4.70,4.75,0.025,0.045,8)
    trim.taper(4.75,4.80,0.045,0.005,8)
    return [base.record("post","iron","solid",position,yaw),
            trim.record("lantern-frame","iron","passable",position,yaw),
            panes.record("lantern-panes","glass","passable",position,yaw)]


PROMENADE_BENCH = {
    "family": "turku-promenade-bench-v1",
    "dimensions": [1.8, 0.87, 0.61],
    "seatHeightM": 0.45,
    "source": "Google panorama hOH16IE5o9B6ar0x7Cm-9w, 2019-06, 15 Lantinen Rantakatu; private observation",
    "dimensionConfidence": "inferred ergonomic prior; unregistered depth",
    "dimensionUncertaintyM": 0.15,
    "forward": "+Z; long axis +X",
}
LITTER_BIN = {
    "family": "turku-ribbed-litter-bin-v1",
    "dimensions": [0.48, 0.95, 0.48],
    "source": PROMENADE_BENCH["source"],
    "dimensionConfidence": "inferred prior; image ratio to pedestrians, no survey",
    "dimensionUncertaintyM": 0.15,
}


def _check_attachment(position, yaw):
    if len(position) != 3 or not all(math.isfinite(v) for v in (*position,yaw)):
        raise ValueError("Furniture position and yaw must be finite")


def promenade_bench(position: tuple, yaw: float = 0, *, backrest: bool = True) -> list[dict]:
    """Dark slatted timber/iron bench. Long axis +X; seated viewer faces +Z.

    Matched office evidence has two distinct kinds: a backed, curved-arm bench
    beside the facade and a separate backless block bench near the river. The
    source importer must choose; river distance alone does not establish type.
    Dimensions are ergonomic priors. Model base is exactly the support plane.
    """
    _check_attachment(position,yaw)
    wood, iron = _Geometry(), _Geometry()
    for x in (-0.65,0.65):
        if backrest:
            for z in (-0.18,0.18):
                iron.box((x,0.01,z),(0.08,0.02,0.07))
                iron.beam((x,0.02,z),(x,0.415,z*0.7),0.026,8)
            iron.beam((x,0.39,-0.23),(x,0.39,0.25),0.03,8)
            iron.beam((x,0.38,-0.23),(x,0.84,-0.30),0.025,8)
            # Curved continuous iron arm, with open space underneath.
            curve=[(x,0.48,0.20),(x,0.58,0.25),(x,0.65,0.18),
                   (x,0.67,0.04),(x,0.65,-0.12),(x,0.61,-0.25)]
            for a,b in zip(curve,curve[1:]):iron.beam(a,b,0.021,8)
        else:
            iron.box((x,0.19,0),(0.11,0.38,0.50))
    for z in (-0.21,-0.105,0,0.105,0.21):
        wood.box((0,0.428,z),(1.8,0.044,0.092))
    if backrest:
        for y,z in ((0.59,-0.245),(0.71,-0.267),(0.83,-0.29)):
            wood.box((0,y,z),(1.8,0.09,0.04))
    else:
        # Backless river bench has a low closed skirt below its slatted top.
        for z in (-0.245,0.245):wood.box((0,0.33,z),(1.8,0.15,0.035))
        for x in (-0.8825,0.8825):wood.box((x,0.33,0),(0.035,0.15,0.455))
    return [iron.record("bench-frame","iron","solid",position,yaw),
            wood.record("bench-slats","wood","solid",position,yaw)]


def litter_bin(position: tuple, yaw: float = 0) -> list[dict]:
    """Cylindrical dark ribbed bin with recessed opening and a foot flange.

    The dark recess is geometry, not a painted hole or a point light. A closed
    inner cylinder provides a stable solid contact surface instead of individual
    collision ribs. Body and rim share the existing iron material.
    """
    _check_attachment(position,yaw)
    body, ribs = _Geometry(), _Geometry()
    body.taper(0,0.10,0.24,0.24,20)
    body.taper(0.10,0.85,0.205,0.205,20)
    # A shallow black-metal interior stops below the open lip, preserving depth.
    body.taper(0.85,0.89,0.205,0.205,20)
    for i in range(24):
        a=i*math.tau/24; x,z=0.218*math.cos(a),0.218*math.sin(a)
        ribs.beam((x,0.12,z),(x,0.90,z),0.009,5)
    # Annular lip is emitted explicitly, so the top is visibly open.
    for i in range(24):
        a,b=i*math.tau/24,(i+1)*math.tau/24
        outer=[(0.24*math.cos(v),0.95,0.24*math.sin(v)) for v in (a,b)]
        inner=[(0.18*math.cos(v),0.95,0.18*math.sin(v)) for v in (a,b)]
        ribs.face((inner[0],inner[1],outer[1],outer[0]))
        ribs.face((outer[0],outer[1],(outer[1][0],0.89,outer[1][2]),(outer[0][0],0.89,outer[0][2])))
        ribs.face((inner[1],inner[0],(inner[0][0],0.89,inner[0][2]),(inner[1][0],0.89,inner[1][2])))
    return [body.record("bin-body","iron","solid",position,yaw),
            ribs.record("bin-ribs","iron","passable",position,yaw)]


FLAT_CANOPY_LAMP = {
    "family": "turku-flat-canopy-lamp-v1",
    "dimensions": [1.2, 5.6, 0.84],
    "supportRadiusM": 0.10,
    "emitterOffset": [0, 4.85, 0],
    "source": "Google panoramas hOH16IE5o9B6ar0x7Cm-9w and 9YcdVIRiQEgvodxtjAX71A, 2019-06; private observations",
    "dimensionConfidence": "inferred prior; broad rectangular reflector visible, dimensions not surveyed",
    "heightUncertaintyM": 0.8,
}
CROOK_LAMP = {
    "family": "turku-crook-lamp-v2",
    "dimensions": [1.16, 10.60, 0.42],
    "supportRadiusM": 0.105,
    "emitterOffset": [-0.72, 9.18, 0],
    "source": FLAT_CANOPY_LAMP["source"],
    "dimensionConfidence": "two calibrated panorama views: spiral absoluteH12.4-13.1m; lidar support around2.13m; inferred family height, not surveyed fixture dimensions",
    "heightUncertaintyM": 1.0,
    "registration": "CityFurniture_19460502 matched in two views;19460503 partial silhouette; other positions require independent association",
}


def flat_canopy_lamp(position: tuple, yaw: float = 0) -> list[dict]:
    """Slender modern pole and broad rectangular reflector, not a T-arm lamp.

    A short cylindrical uplight sits below the plate. The family declares its
    source position; selecting a physical light direction is the emitter compiler's
    responsibility. No glow is painted into the reflector's daytime material.
    """
    _check_attachment(position,yaw)
    post, head, glass = _Geometry(), _Geometry(), _Geometry()
    post.taper(0,0.08,0.10,0.10,12)
    post.taper(0.08,5.56,0.075,0.046,12)
    post.taper(0.70,0.77,0.082,0.082,12)
    head.box((0,5.58,0),(1.2,0.04,0.84))
    head.taper(4.37,4.67,0.085,0.085,12)
    head.taper(4.67,4.81,0.085,0.11,12)
    glass.taper(4.81,4.85,0.10,0.10,12)
    return [post.record("canopy-post","iron","solid",position,yaw),
            head.record("canopy-reflector","iron","passable",position,yaw),
            glass.record("canopy-source","glass","passable",position,yaw)]


def crook_lamp(position: tuple, yaw: float = 0) -> list[dict]:
    """Riverfront scroll-crook lamp with a compact downward-facing bell.

    The hook extends along local -X. This family is distinct from the four-pane
    heritage lantern. The reference association is an observation, not proof that
    every riverside municipal pole carries this fixture.
    """
    _check_attachment(position,yaw)
    post, head, glass = _Geometry(), _Geometry(), _Geometry()
    post.taper(0,0.12,0.105,0.105,12)
    post.taper(0.12,0.60,0.105,0.085,12)
    post.taper(0.60,9.90,0.085,0.036,12)
    for y in (0.60,9.48,9.62):head.taper(y,y+0.045,0.065,0.065,12)
    # The large scroll curls inward above a separately suspended bell. The first
    # draft's small hook/5.28m height was disproved before any office placement.
    curve=[]
    for i in range(37):
        angle=i*2.35*math.pi/36
        scale=max(.25,1-.75*max(0,angle-1.05*math.pi)/(1.30*math.pi))
        curve.append((-.45+.45*scale*math.cos(angle),9.90+.68*scale*math.sin(angle),0))
    for a,b in zip(curve,curve[1:]):head.beam(a,b,.018,6)
    # Small counter-scroll on the pole's opposite side is visible in the second
    # panorama. Keep it slender and open, without filling the scroll silhouette.
    counter=[(.105-.105*math.cos(i*1.6*math.pi/12),9.72+.105*math.sin(i*1.6*math.pi/12),0) for i in range(13)]
    for a,b in zip(counter,counter[1:]):head.beam(a,b,.012,5)
    x=-.72
    attach=min(curve,key=lambda p:(p[0]-x)**2+(p[1]-9.47)**2)
    head.beam(attach,(x,9.47,0),.015,6)
    head.taper(9.32,9.47,.18,.045,12,cx=x)
    head.taper(9.29,9.32,.21,.21,12,cx=x)
    glass.taper(9.18,9.29,.15,.18,12,cx=x)
    return [post.record("crook-post","iron","solid",position,yaw),
            head.record("crook-head","iron","passable",position,yaw),
            glass.record("crook-source","glass","passable",position,yaw)]
