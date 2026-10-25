import collections
from streetlevel import lookaround
lat, lon = 60.4506, 22.2642
try:
    panos = lookaround.get_coverage_tile_by_latlon(lat, lon).panos
    print("LookAround panos in z17 tile:", len(panos))
    for p in panos[:5]: print(p.id, p.build_id, p.lat, p.lon, p.date, p.coverage_type, getattr(p,'elevation',None))
    print(collections.Counter(str(p.date)[:7] for p in panos))
except Exception as e:
    import traceback; traceback.print_exc()
for name,(la,lo) in {"Helsinki":(60.1699,24.9384),"Tampere":(61.4978,23.7610),"Turku-kauppatori":(60.4510,22.2670)}.items():
    try:
        print(name, len(lookaround.get_coverage_tile_by_latlon(la, lo).panos))
    except Exception as e: print(name, "ERR", e)
