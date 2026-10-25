#!/usr/bin/env python3
"""
Keyless scrape of Google Maps business / user-contributed photos for places near a point.
RESEARCH USE ONLY: this uses Google Maps' internal web endpoints (not a public API),
which is against Google Maps ToS for bulk use and can break or be rate-limited at any time.
Keep volumes tiny (a handful of places), cache everything, never redistribute photos.

Steps (all plain HTTPS GETs, no cookies beyond a consent bypass, no key):
 1. https://www.google.com/search?tbm=map&q=<category>&pb=!4m12!1m3!1d<m>!2d<lon>!3d<lat>...
    -> ")]}'"-prefixed JSON with ~20 places: feature id 0x..:0x.., name, address, lat/lon.
 2. https://www.google.com/maps/place/data=!4m2!3m1!1s<feature id>
    -> HTML that embeds a /maps/preview/place?... URL; fetching it returns the place payload
    with ~50-70 photo URLs (lh3.googleusercontent.com/grass-cs/..., /gps-cs-s/..., /p/...).
 3. Append "=s0" to a photo URL -> the original upload (e.g. 4000x3000, camera EXIF kept,
    GPS stripped).  "=w2048" etc. for a resized copy.

Usage:
  python3 -I tools/refs/gmaps_place_photos.py --center 60.44839,22.26726 --query museum \
      --out data/refs/gmaps_places --places 3 --photos 10
  python3 -I tools/refs/gmaps_place_photos.py --fid 0x468c76fc772a9751:0x9ea21e2e451378ab --photos 10
"""
import argparse, json, os, re, time, urllib.parse, urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
HDR = {"User-Agent": UA, "Accept-Language": "en", "Cookie": "CONSENT=YES+cb"}
PHOTO_RE = re.compile(r"lh[0-9]\.googleusercontent\.com/(grass-cs|gps-cs-s|gps-proxy|geougc-cs|p)/([A-Za-z0-9_-]{40,})")


def get(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def search(lat, lon, query, span_m=1500):
    pb = "!4m12!1m3!1d%d!2d%f!3d%f!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!7i20!10b1" % (span_m, lon, lat)
    url = "https://www.google.com/search?tbm=map&authuser=0&hl=en&gl=fi&q=%s&pb=%s" % (urllib.parse.quote(query), pb)
    s = get(url).decode("utf-8", "ignore")
    out = {}
    for m in re.finditer(r'\[null,null,(-?\d+\.\d+),(-?\d+\.\d+)\],"(0x[0-9a-f]+:0x[0-9a-f]+)","((?:[^"\\]|\\.)*)",null,\[([^\]]*)\]((?:,null)*),"((?:[^"\\]|\\.)*)"', s):
        lat_, lon_, fid, name, cats, _, addr = m.groups()
        dec = lambda t: json.loads('"%s"' % t)
        out.setdefault(fid, dict(fid=fid, name=dec(name), lat=float(lat_), lon=float(lon_),
                                 categories=[dec(c.strip('"')) for c in cats.split('","')], address=dec(addr)))
    return list(out.values())


def place_photos(fid):
    html = get("https://www.google.com/maps/place/data=!4m2!3m1!1s%s?hl=en" % fid).decode("utf-8", "ignore")
    m = re.search(r'/maps/preview/place\?[^"]+', html)
    if not m:
        return []
    body = get("https://www.google.com" + m.group(0).replace("&amp;", "&")).decode("utf-8", "ignore")
    seen = []
    for kind, tok in PHOTO_RE.findall(body):
        u = "https://lh3.googleusercontent.com/%s/%s" % (kind, tok)
        if u not in seen:
            seen.append(u)
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--center"); ap.add_argument("--query", default="museum")
    ap.add_argument("--fid", help="skip search, use this feature id")
    ap.add_argument("--places", type=int, default=3); ap.add_argument("--photos", type=int, default=10)
    ap.add_argument("--size", default="s0", help="s0 = original; w2048 = 2048 px wide")
    ap.add_argument("--out", default="data/refs/gmaps_places")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.fid:
        places = [dict(fid=a.fid, name=a.fid)]
    else:
        lat, lon = map(float, a.center.split(","))
        places = search(lat, lon, a.query)
        for p in places:  # rank by ground distance (search results are relevance-ordered)
            p["dist_m"] = round((((p["lat"] - lat) * 111320) ** 2 + ((p["lon"] - lon) * 111320 * 0.4932) ** 2) ** 0.5)
        places.sort(key=lambda p: p["dist_m"])
        json.dump(places, open(os.path.join(a.out, "search_%s.json" % a.query), "w"), indent=1, ensure_ascii=False)
        for p in places:
            print("%-40s %-38s %5dm %s" % (p["fid"], p["name"][:38], p["dist_m"], p["address"]))
    for p in places[: a.places]:
        urls = place_photos(p["fid"])
        d = os.path.join(a.out, p["fid"].replace(":", "_")); os.makedirs(d, exist_ok=True)
        json.dump(dict(place=p, photo_urls=urls), open(os.path.join(d, "photos.json"), "w"), indent=1, ensure_ascii=False)
        print("%s: %d photo urls" % (p["name"], len(urls)))
        for i, u in enumerate(urls[: a.photos]):
            fn = os.path.join(d, "%02d.jpg" % i)
            if not os.path.exists(fn):
                open(fn, "wb").write(get(u + "=" + a.size)); time.sleep(0.3)
        time.sleep(1.0)


if __name__ == "__main__":
    main()
