"""Fetch a bbox from several Turku WMS layers at fine px and estimate native resolution
(run-length of identical pixels under nearest-neighbour upsampling + blur metric).
usage: wms_res.py outdir xmin,ymin,xmax,ymax px_m layer..."""
import sys, os, io, urllib.parse, urllib.request, numpy as np
from PIL import Image
W = 'https://turku.asiointi.fi/TeklaOGCWeb/WMS.ashx'
out, bb, px = sys.argv[1], [float(v) for v in sys.argv[2].split(',')], float(sys.argv[3])
os.makedirs(out, exist_ok=True)
w = int(round((bb[2]-bb[0])/px)); h = int(round((bb[3]-bb[1])/px))
for L in sys.argv[4:]:
    q = dict(SERVICE='WMS', VERSION='1.1.1', REQUEST='GetMap', LAYERS=L, STYLES='', SRS='EPSG:3877',
             BBOX=','.join('%.3f' % v for v in bb), WIDTH=w, HEIGHT=h, FORMAT='image/png')
    u = W + '?' + urllib.parse.urlencode(q)
    r = urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 Chrome/150', 'Referer': 'https://turku.asiointi.fi/IMS/fi'}), timeout=120)
    b = r.read(); ct = r.headers.get('content-type')
    if 'image' not in ct:
        print(L, 'ERR', ct, b[:200]); continue
    im = np.asarray(Image.open(io.BytesIO(b)).convert('RGB')).astype(int)
    fn = os.path.join(out, L.replace(' ', '_') + '.png'); Image.fromarray(im.astype(np.uint8)).save(fn)
    white = (im.min(2) > 250).mean()
    # run lengths of identical consecutive pixels along rows
    eq = (np.abs(np.diff(im, axis=1)).sum(2) == 0)
    changes = (~eq).sum(1).mean()
    run = w / max(changes, 1)
    # blur metric: energy ratio between 1-px and 4-px differences
    g = im.mean(2)
    d1 = np.abs(np.diff(g, axis=1)).mean(); d4 = np.abs(g[:, 4:] - g[:, :-4]).mean()
    print(f'{L:32s} {len(b)/1e6:.2f} MB white {white:.2f} mean-run {run:.1f}px (~{run*px*100:.1f} cm)  d1/d4 {d1/max(d4,1e-9):.2f}')
