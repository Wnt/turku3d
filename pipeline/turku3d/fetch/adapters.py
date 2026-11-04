"""Layers a city does not serve in the internal (Turku) schema, derived from layers it does serve.

``infstreet``: Espoo's ``GIS:Katualueet`` answers 401, but ``GIS:InfStreet`` (open) holds the same
street-part polygons from the same street register product. Each part becomes a ``GIS:Katualueet``
row with Turku's ``Katuosalaji``/``Katuosalaji_koodi`` and ``Pintamateriaali``/``Pintamateriaali_koodi``
vocabulary (the street, kerb, parking and material producers key on these), plus ``Tunnus`` and
``Kadunnimi`` from the register label. The Espoo values are kept in ``source_*`` columns.

The maps cover every value seen in Tapiola (2026-11-05: 986 parts); an unseen value maps to
"Muu" / "Ei tietoa" and is counted in the summary's ``unmapped``, never dropped.
"""

from __future__ import annotations

import re

from .. import config
from .sources import WfsLayer

INFSTREET = WfsLayer("GIS:InfStreet", "1.0.0", "Espoo street parts (source of GIS:Katualueet)")

# (PARTCLASSTEXT, USETYPETEXT) -> (Katuosalaji, Katuosalaji_koodi); USETYPETEXT None = any other.
PART = {
    ("Ajorata", "Pysäköintialue"): ("Muu liikennealue", "12"),
    ("Ajorata", "Liikenteenjakaja"): ("Välikaista", "13"),
    ("Ajorata", "Pääkatu"): ("Ajorata", "1"),
    ("Ajorata", "Kokoojakatu"): ("Ajorata", "1"),
    ("Ajorata", "Tontti-/asunto-/liityntä-/pihakatu"): ("Ajorata", "1"),
    ("Ajorata", "Pääväylä - maantie"): ("Ajorata", "1"),
    ("Ajorata", None): ("Ajorata", "1"),
    ("Kevyt liikenne", "Erotus/Välikaista"): ("Välikaista", "13"),
    ("Kevyt liikenne", "Jalkakäytävä"): ("Jalkakäytävä", "2"),
    ("Kevyt liikenne", "Pyörätie"): ("Pyörätie", "3"),
    ("Kevyt liikenne", "Yhdistetty pyörätie ja jalkakäytävä"): ("Yhdistetty kevyen liikenteen väylä", "4"),
    ("Kevyt liikenne", "Puistoraitti"): ("Kävelytie", "16"),
    ("Kevyt liikenne", "Tori/Aukio"): ("Kävelytie", "16"),
    ("Kevyt liikenne", "Polkupyöräpysäköinti"): ("Muu", "6"),
    ("Kevyt liikenne", "Potkulautapysäköinti"): ("Muu", "6"),
    ("Kevyt liikenne", None): ("Yhdistetty kevyen liikenteen väylä", "4"),
}
# PAVINGCLASSTEXT -> (Pintamateriaali, Pintamateriaali_koodi) in Turku's codes.
PAVING = {
    "Asfalttibetoni AB": ("Asfalttibetoni", "1"),
    "Betoni": ("Betoni", "14"),
    "Luonnonkivi": ("Luonnonkivi", "011"),
    "Kenttäkivi": ("Kenttäkivi", "4"),
    "Puu": ("Puu", "13"),
    "Kivituhka / Sora / Murske": ("Kivituhka", "18"),
    "Nurmikivi": ("Nurmikivi", "10"),  # grass pavers: no Turku code, "Muu" (10) with its own name
    "Metalli": ("Metalli", "19"),
}


def _part(cls, use):
    found = PART.get((cls, use)) or PART.get((cls, None))
    return found or ("Muu", "6")


def infstreet_rows(frame):
    """InfStreet frame -> Katualueet frame (same geometry columns); (frame, unmapped counts)."""
    unmapped: dict[str, int] = {}
    out = frame.copy()
    kinds, kcodes, mats, mcodes, names = [], [], [], [], []
    for cls, use, paving, label in zip(
        frame.get("PARTCLASSTEXT"), frame.get("USETYPETEXT"), frame.get("PAVINGCLASSTEXT"),
        frame.get("LABEL"), strict=True,
    ):
        cls = cls if isinstance(cls, str) else ""
        use = use if isinstance(use, str) else ""
        if (cls, use) not in PART:
            unmapped[f"part:{cls}/{use}"] = unmapped.get(f"part:{cls}/{use}", 0) + 1
        kind, code = _part(cls, use)
        paving = paving if isinstance(paving, str) else ""
        mat, mcode = PAVING.get(paving, ("Ei tietoa", "0"))
        if paving and paving not in PAVING:
            unmapped[f"paving:{paving}"] = unmapped.get(f"paving:{paving}", 0) + 1
        kinds.append(kind)
        kcodes.append(code)
        mats.append(mat)
        mcodes.append(mcode)
        names.append(re.sub(r"^\d+", "", label).strip() if isinstance(label, str) else "")
    rename = {c: f"source_{c}" for c in out.columns if c.isupper() and c not in ("ID",)}
    out = out.rename(columns=rename)
    out["layer"] = "GIS:Katualueet"
    out["Id"] = out.pop("ID") if "ID" in out.columns else ""
    out["Tunnus"] = frame.get("LABEL")
    out["Kadunnimi"] = names
    out["Katuosalaji"] = kinds
    out["Katuosalaji_koodi"] = kcodes
    out["Pintamateriaaliryhma"] = "Katuosan pintamateriaalit"
    out["Pintamateriaali"] = mats
    out["Pintamateriaali_koodi"] = mcodes
    for c in ("Id", "Tunnus", "Kadunnimi", "Katuosalaji", "Katuosalaji_koodi", "Pintamateriaaliryhma",
              "Pintamateriaali", "Pintamateriaali_koodi"):
        out[c] = out[c].astype("string")
    return out, unmapped


def derive(layer: WfsLayer, area: config.Area, adapter: str, log=print) -> dict:
    import geopandas as gpd

    from .common import kind_dir
    from .wfs import fetch_remote, write_frame

    if config.CITY.ground == "nl-ahn":
        from turku3d.nl import convert

        return convert.derive(layer, area, adapter, log=log)
    if adapter != "infstreet":
        raise ValueError(f"unknown WFS adapter {adapter!r} for {layer.name}")
    s = fetch_remote(INFSTREET, area, log=log)
    if s.get("status") not in ("ok", "empty"):
        return {"layer": layer.name, "status": s.get("status", "error"), "derived_from": INFSTREET.name}
    src = kind_dir("wfs") / area.name / f"{INFSTREET.slug}.parquet"
    summary = kind_dir("wfs") / area.name / f"{layer.slug}.summary.json"
    marker = s.get("pages_sig")
    if summary.exists() and (summary.with_name(f"{layer.slug}.parquet")).exists():
        import json

        old = json.loads(summary.read_text())
        if old.get("source_pages_sig") == marker and old.get("adapter") == adapter:
            log(f"wfs  {layer.name}: cached (from {INFSTREET.name})")
            return old
    frame = gpd.read_parquet(src)
    out, unmapped = infstreet_rows(frame) if len(frame) else (frame, {})
    nfeat = int(out["feature_key"].nunique()) if len(out) else 0
    result = write_frame(
        layer.name, layer.slug, area, out, nfeat,
        derived_from=INFSTREET.name, adapter=adapter, source_pages_sig=marker, unmapped=unmapped,
    )
    log(f"wfs  {layer.name}: {nfeat} parts derived from {INFSTREET.name}; unmapped {unmapped or 'none'}")
    return result
