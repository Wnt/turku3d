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
