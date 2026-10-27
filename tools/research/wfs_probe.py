"""Fetch WFS layers in a bbox from Turku TeklaOGCWeb and summarize attributes / geometry types.
usage: wfs_probe.py outdir xmin,ymin,xmax,ymax layer [layer...]"""
import sys, os, time, urllib.parse, urllib.request, collections, xml.etree.ElementTree as ET
W = 'https://turku.asiointi.fi/TeklaOGCWeb/WFS.ashx'
UA = 'Mozilla/5.0 (X11; Linux x86_64) Chrome/150'
out, bb, layers = sys.argv[1], sys.argv[2], sys.argv[3:]
x0, y0, x1, y1 = bb.split(',')
os.makedirs(out, exist_ok=True)
for L in layers:
    flt = (f'<Filter><BBOX><PropertyName>Geometry</PropertyName><gml:Box xmlns:gml="http://www.opengis.net/gml">'
           f'<gml:coordinates>{x0},{y0} {x1},{y1}</gml:coordinates></gml:Box></BBOX></Filter>')
    q = dict(service='WFS', version='1.0.0', request='GetFeature', typeName=L, filter=flt)
    if L.split(':')[0] in ('bldg', 'frn', 'luse', 'veg', 'dem', 'tran'):
        q = dict(service='WFS', version='1.1.0', request='GetFeature', typeName=L, bbox=f'{x0},{y0},{x1},{y1}')
    u = W + '?' + urllib.parse.urlencode(q)
    t = time.time()
    try:
        b = urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': UA}), timeout=300).read()
    except Exception as e:
        print(L, 'ERROR', e); continue
    fn = os.path.join(out, L.replace(':', '_') + '.gml')
    open(fn, 'wb').write(b)
    dt = time.time() - t
    try:
        root = ET.fromstring(b)
    except Exception as e:
        print(L, len(b), 'bytes parse error', b[:300]); continue
    members = [m for m in root if m.tag.endswith('featureMember') or m.tag.endswith('cityObjectMember') or m.tag.endswith('featureMembers')]
    feats = []
    for m in members:
        feats.extend(list(m))
    gtypes = collections.Counter(); attrs = collections.defaultdict(collections.Counter)
    zs = 0
    for f in feats:
        for e in f.iter():
            tg = e.tag.split('}')[1]
            if tg in ('Point', 'LineString', 'Polygon', 'MultiPolygon', 'MultiLineString', 'MultiSurface', 'Solid', 'CompositeSurface', 'TriangulatedSurface', 'Triangle', 'MultiCurve'):
                gtypes[tg] += 1
            if tg in ('coordinates', 'posList', 'pos') and e.text and zs == 0:
                zs = e.get('srsDimension') or (len(e.text.split()[0].split(',')) if tg == 'coordinates' else '?')
        for c in f:
            tg = c.tag.split('}')[1]
            if len(c) == 0 and c.text and len(c.text.strip()) < 60:
                attrs[tg][c.text.strip()] += 1
    print(f'== {L}: {len(b)} B, {dt:.1f}s, {len(feats)} features, geom {dict(gtypes)}, dim {zs}')
    for k, v in attrs.items():
        if len(v) <= 1 and k not in ('Tyyppi', 'Luokka', 'Kohdeluokka'):
            continue
        print('   ', k, v.most_common(12))
