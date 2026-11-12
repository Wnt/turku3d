"""Additive timber-classical details tied to existing authored openings.

Reference proportions are visual priors, not measured survey geometry. No source
pixels are shipped. The caller retains the building's facade program and chooses
its material; these meshes only add the shallow projecting crown silhouette.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from .families import _Geometry, _check_attachment

TIMBER_CROWN = {
    "family": "turku-timber-classical-crown-v1",
    "source": "Petrelius restored oblique and opposite-river photographs, Jalkipeli article 2025-02-01; capture dates unknown",
    "sourceUrl": "https://www.jalkipeli.net/petreliuksen-talo-on-taynna-hienoja-yksityiskohtia/",
    "dimensions": "opening-relative width, projection 0.24m, head rise 0.39m flat / 0.64m triangular",
    "dimensionConfidence": "visual prior +/-0.10m; opening anchors retain authored program confidence",
    "materialPolicy": "reuse authored facade trim colour; reference colour is not calibrated",
}
# Copied from the existing office program's _KIT_TRIM linear values. This avoids
# a conflicting new palette while adding geometric relief to retained appearance.
OFFICE_ORNAMENT_MATERIAL = {
    "id": "office-ornament", "color": [0.20155625, 0.23074006, 0.20155625],
    "roughness": 0.82, "metalness": 0.0,
}


def _extrude(g, outline, back, front):
    """Closed XY polygon, counterclockwise viewed from outward +Z."""
    if sum(outline[i][0]*outline[(i+1)%len(outline)][1]-outline[(i+1)%len(outline)][0]*outline[i][1] for i in range(len(outline))) < 0:
        outline=list(reversed(outline))
    rear=[(x,y,back) for x,y in outline];face=[(x,y,front) for x,y in outline]
    g.face(face);g.face(tuple(reversed(rear)))
    for i in range(len(face)):
        j=(i+1)%len(face)
        g.face((rear[i],rear[j],face[j],face[i]))


def timber_window_crown(position: tuple, yaw: float = 0, *, opening_width: float,
                        pediment: bool = False, material_id: str = "office-ornament") -> list[dict]:
    """Window head centred at position, wall face Z=0, outward +Z.

    Keep clear of the existing glass opening. The two hanging brackets occupy
    the jamb edges, and every horizontal crown lies above the opening head.
    Local X is horizontal, Y up; yaw follows the common scene family convention.
    """
    _check_attachment(position,yaw)
    if not math.isfinite(opening_width) or not 0.5 <= opening_width <= 4:
        raise ValueError("Crown requires an explicit opening width in metres")
    g=_Geometry();half=opening_width/2
    g.box((0,.095,.075),(opening_width+.22,.13,.15))
    # Narrow fascia, shadow undercut and three projecting moulding courses.
    for y,h,extra,depth in ((.19,.06,.29,.18),(.255,.07,.37,.21),(.315,.05,.43,.24)):
        g.box((0,y,depth/2),(opening_width+extra,h,depth))
    count=max(4,round((opening_width+.12)/.18));pitch=(opening_width+.12)/count
    for i in range(count):
        g.box(((i-(count-1)/2)*pitch,.143,.125),(pitch*.43,.075,.11))
    for sign in (-1,1):
        x=sign*(half+.095)
        # Tapered support cheek, with a small foot and upper capital.
        _extrude(g,[(x-.055,-.27),(x+.055,-.27),(x+.085,.11),(x-.085,.11)],.015,.12)
        g.box((x,-.245,.09),(.14,.05,.17))
        g.box((x,.065,.12),(.21,.08,.21))
    if pediment:
        span=half+.215;base=.34;rise=.30;top=base+rise
        _extrude(g,[(-span,base),(span,base),(0,top)],.015,.075)
        # Raking cornices have real flat faces; do not approximate with tubes.
        _extrude(g,[(-span-.035,base-.015),(.018,top+.025),(.018,top-.045),(-span+.02,base-.085)],.055,.20)
        _extrude(g,[(-.018,top-.045),(-.018,top+.025),(span+.035,base-.015),(span-.02,base-.085)],.055,.20)
    else:
        g.box((0,.365,.105),(opening_width+.39,.05,.21))
    return [g.record("window-crown",material_id,"passable",position,yaw)]


def office_window_details() -> list[dict]:
    """Ten reference-supported crowns on the retained office program grid.

    No crown is added to the adjoining four-bay grey wing. Anchor JSON is derived
    from the game's authored GLB, not copied from a city or imagery dataset.
    """
    data=json.loads(Path(__file__).with_name("office_detail_anchors.json").read_text())
    meshes=[]
    for anchor in data["anchors"]:
        for mesh in timber_window_crown(tuple(anchor["position"]),anchor["yaw"],
                                       opening_width=anchor["openingWidth"],pediment=anchor["pediment"]):
            mesh["id"]=anchor["id"]+"-"+mesh["id"]
            meshes.append(mesh)
    return meshes
