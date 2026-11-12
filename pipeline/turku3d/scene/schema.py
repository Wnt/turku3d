"""Strict, finite scene contract; reject bad references before writing products."""
from __future__ import annotations

import math

SCHEMA = "turku-scene/1"
FIELDS = {
    "scene": "schema release id origin bounds sources materials boundaries supports meshes fixtures emitters coverage collision spawn assets lightOccluders",
    "source": "id sha256 kind uncertaintyM",
    "material": "id color roughness metalness water baseColorTexture normalTexture ormTexture",
    "boundary": "id positions",
    "support": "id kind boundaryIds meshId",
    "mesh": "id entityId materialId positions indices collision supportId normals uv render boundaryIds bakedVisibility nightIrradiance",
    "asset": "id entityId url sha256 position recipe bounds",
    "fixture": "id family supportId position yaw dimensions meshIds emitterIds sourceIds uncertaintyM",
    "emitter": "id fixtureId position color intensity range category aperture",
    "aperture": "shape position normal axisU size cutoutRadius",
    "lightOccluder": "id positions minY maxY",
    "coverage": "mode bounds entityIds",
    "collision": "url sha256 bytes tris h",
    "spawn": "position headingDeg vehicle",
    "vehicle": "position yaw",
}


def fields(value, kind, optional=()):
    if not isinstance(value, dict):
        raise ValueError(f"{kind} must be an object")
    expected = set(FIELDS[kind].split())
    if set(value) - expected or expected - set(optional) - set(value):
        raise ValueError(f"{kind} fields: expected {sorted(expected)}, got {sorted(value)}")


def vector(value, size, name):
    if not isinstance(value, list) or len(value) != size or any(
        isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in value
    ):
        raise ValueError(f"{name} must contain {size} finite numbers")


def validate_scene(scene):
    fields(scene, "scene")
    if scene["schema"] != SCHEMA:
        raise ValueError("Unsupported scene schema")
    for name in ("id", "release"):
        if not isinstance(scene[name], str) or not scene[name]:
            raise ValueError(f"{name} is required")
    vector(scene["origin"], 3, "origin")
    vector(scene["bounds"], 4, "bounds")
    if not (scene["bounds"][0] < scene["bounds"][2] and scene["bounds"][1] < scene["bounds"][3]):
        raise ValueError("Empty scene bounds")
    tables = {}
    for plural, kind in (("sources", "source"), ("materials", "material"), ("boundaries", "boundary"),
                         ("supports", "support"), ("meshes", "mesh"), ("fixtures", "fixture"),
                         ("emitters", "emitter"), ("assets", "asset"), ("lightOccluders", "lightOccluder")):
        if not isinstance(scene[plural], list):
            raise ValueError(f"{plural} must be an array, including when empty")
        rows = {}
        for row in scene[plural]:
            optional={"mesh":("normals","uv","render","boundaryIds","bakedVisibility","nightIrradiance"),"material":("water","baseColorTexture","normalTexture","ormTexture"),"emitter":("category","aperture")}
            fields(row, kind, optional.get(kind,()))
            key = row["id"]
            if not isinstance(key, str) or not key or key in rows:
                raise ValueError(f"Invalid or duplicate {plural} ID {key}")
            rows[key] = row
        tables[plural] = rows
    def ref(table, key):
        if key not in tables[table]:
            raise ValueError(f"Unknown {table} reference: {key}")
    for asset in scene["assets"]:
        vector(asset["position"], 3, "asset position")
        vector(asset["bounds"]["min"], 3, "asset bounds min")
        vector(asset["bounds"]["max"], 3, "asset bounds max")
        if asset["recipe"] not in ("building", "vegetation", "context") or len(asset["sha256"]) != 64:
            raise ValueError("Invalid building asset")
    for source in scene["sources"]:
        if len(source["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in source["sha256"]):
            raise ValueError("Source requires SHA-256")
        vector([source["uncertaintyM"]], 1, "source uncertainty")
        if source["uncertaintyM"] < 0:
            raise ValueError("Negative uncertainty")
    for material in scene["materials"]:
        if "water" in material:
            water=material["water"]
            if set(water)!={"point","normal","band"}:
                raise ValueError("Invalid explicit water recipe")
            vector(water["point"],3,"water plane point")
            vector(water["normal"],3,"water plane normal")
        for name in ("baseColorTexture","normalTexture","ormTexture"):
            if name in material:
                texture=material[name]
                if set(texture)!={"url","sha256","wrap"} or texture["wrap"] not in ("clamp","repeat") or len(texture["sha256"])!=64:
                    raise ValueError("Invalid material texture")
        vector(material["color"], 3, "linear color")
        vector([material["roughness"], material["metalness"]], 2, "material")
        if any(x < 0 or x > 1 for x in material["color"] + [material["roughness"], material["metalness"]]):
            raise ValueError("Material values outside [0,1]")
    for boundary in scene["boundaries"]:
        if len(boundary["positions"]) < 2:
            raise ValueError("Boundary must have at least two vertices")
        for point in boundary["positions"]:
            vector(point, 3, "boundary position")
    for support in scene["supports"]:
        if support["kind"] not in ("road", "footway", "quay", "ground"):
            raise ValueError("Unknown support kind")
        ref("meshes", support["meshId"])
        for key in support["boundaryIds"]:
            ref("boundaries", key)
    for mesh in scene["meshes"]:
        p, ix = mesh["positions"], mesh["indices"]
        if len(p) < 9 or len(p) % 3 or len(ix) < 3 or len(ix) % 3:
            raise ValueError("Invalid mesh array lengths")
        vector(p, len(p), "mesh positions")
        if any(type(i) is not int or i < 0 or i >= len(p) // 3 for i in ix):
            raise ValueError("Invalid triangle index")
        ref("materials", mesh["materialId"])
        if mesh["supportId"] is not None:
            ref("supports", mesh["supportId"])
        if "boundaryIds" in mesh:
            if not isinstance(mesh["boundaryIds"], list) or any(not isinstance(k,str) for k in mesh["boundaryIds"]):
                raise ValueError("mesh boundaryIds must be an array of IDs")
            for key in mesh["boundaryIds"]:
                ref("boundaries", key)
        if mesh["collision"] not in ("solid", "passable", "dynamic", "sensor"):
            raise ValueError("Unknown collision policy")
        for name, size in (("normals", len(p)), ("uv", len(p) // 3 * 2)):
            if name in mesh:
                vector(mesh[name], size, name)
        if 'bakedVisibility' in mesh:
            values=mesh['bakedVisibility']
            if not isinstance(values,list) or len(values)!=len(p) or any(type(v) is not int or not 0<=v<=255 for v in values):
                raise ValueError('bakedVisibility must contain one UINT8 RGB triple per vertex')
        if 'nightIrradiance' in mesh:
            vector(mesh['nightIrradiance'],len(p),'nightIrradiance')
            if any(v<0 for v in mesh['nightIrradiance']):raise ValueError('nightIrradiance must be nonnegative HDR RGB')
    for fixture in scene["fixtures"]:
        ref("supports", fixture["supportId"])
        for key in fixture["sourceIds"]:
            ref("sources", key)
        vector([fixture["uncertaintyM"]], 1, "fixture uncertainty")
        vector(fixture["position"], 3, "fixture position")
        vector(fixture["dimensions"], 3, "fixture dimensions")
        vector([fixture["yaw"]], 1, "fixture yaw")
        if any(x <= 0 for x in fixture["dimensions"]):
            raise ValueError("Fixture dimensions must be positive")
        for key in fixture["meshIds"]:
            ref("meshes", key)
        for key in fixture["emitterIds"]:
            ref("emitters", key)
    for emitter in scene["emitters"]:
        ref("fixtures", emitter["fixtureId"])
        vector(emitter["position"], 3, "emitter position")
        vector(emitter["color"], 3, "emitter color")
        vector([emitter["intensity"], emitter["range"]], 2, "emitter photometry")
        if emitter['intensity']<0 or emitter['range']<=0 or any(c<0 or c>1 for c in emitter['color']):
            raise ValueError('Invalid physical emitter values')
        if 'category' in emitter and emitter['category'] not in ('street','path','wall'):
            raise ValueError('Invalid emitter category')
        if 'aperture' in emitter:
            aperture=emitter['aperture'];fields(aperture,'aperture')
            if aperture['shape']!='rectangle':raise ValueError('Unsupported emitter aperture')
            for key in ('position','normal','axisU'):vector(aperture[key],3,'aperture '+key)
            vector(aperture['size'],2,'aperture size');vector([aperture['cutoutRadius']],1,'aperture cutout')
            if any(v<=0 for v in aperture['size']) or not 0<=aperture['cutoutRadius']<min(aperture['size'])/2:
                raise ValueError('Invalid aperture dimensions')
            n,u=aperture['normal'],aperture['axisU']
            if abs(sum(v*v for v in n)-1)>1e-6 or abs(sum(v*v for v in u)-1)>1e-6 or abs(sum(a*b for a,b in zip(n,u,strict=True)))>1e-6:
                raise ValueError('Aperture axes must be orthonormal')
    for occluder in scene['lightOccluders']:
        if len(occluder['positions'])<3:
            raise ValueError('Light occluder needs a polygon')
        for point in occluder['positions']:vector(point,2,'light occluder position')
        vector([occluder['minY'],occluder['maxY']],2,'light occluder height')
        if occluder['minY']>=occluder['maxY']:raise ValueError('Empty light occluder height')
    fields(scene["coverage"], "coverage")
    if scene["coverage"]["mode"] != "replace" or scene["coverage"]["bounds"] != scene["bounds"]:
        raise ValueError("Coverage must replace exactly the scene bounds")
    fields(scene["spawn"], "spawn")
    vector(scene["spawn"]["position"], 3, "spawn")
    vector([scene["spawn"]["headingDeg"]], 1, "heading")
    fields(scene["spawn"]["vehicle"], "vehicle")
    vector(scene["spawn"]["vehicle"]["position"], 3, "vehicle position")
    vector([scene["spawn"]["vehicle"]["yaw"]], 1, "vehicle yaw")
    if not isinstance(scene["collision"], dict):
        raise ValueError("collision must be a cell-ID map")
    for record in scene["collision"].values():
        fields(record, "collision")
        vector(record["h"], 2, "collision height bounds")
        if record["bytes"] <= 0 or record["tris"] <= 0 or len(record["sha256"]) != 64:
            raise ValueError("Invalid collision product")
    return scene
