import json, sys
from streetlevel import streetview
for pid in sys.argv[1:]:
    p=streetview.find_panorama_by_id(pid)
    json.dump({"lat":p.lat,"lon":p.lon,"heading":p.heading,"elevation":p.elevation,"date":str(p.date)},open(f"meta_{pid}.json","w"))
    streetview.get_panorama(p,zoom=3).save(f"pano_{pid}_preview.jpg",quality=90)
    print(pid,p.date,p.elevation)
