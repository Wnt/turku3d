# Pose check of a GSV pano against Turku lidar: render lidar as equirect range map, grid search dx,dy,dz,yaw vs Google depth.
import sys, json, numpy as np, laspy
from pyproj import Transformer
from PIL import Image
pid = sys.argv[1]; tiles = sys.argv[2:]
meta = json.load(open(f"meta_{pid}.json"))
g = np.load(f"depth_{pid}.npy")             # 256x512, -1 = sky
H, W = g.shape
X0, Y0 = Transformer.from_crs(4326, 3877, always_xy=True).transform(meta["lon"], meta["lat"])
pts=[]; cols=[]
for t in tiles:
    l = laspy.read(t)
    x, y, z = np.asarray(l.x), np.asarray(l.y), np.asarray(l.z)
    m = (np.hypot(x-X0, y-Y0) < 80) & (l.classification != 7)
    pts.append(np.c_[x[m], y[m], z[m]]); cols.append(np.c_[l.red[m], l.green[m], l.blue[m]] / 256)
    cls = np.asarray(l.classification)[m]
P = np.vstack(pts); C = np.vstack(cols).clip(0,255).astype(np.uint8)
print("lidar pts within 80 m:", len(P))
gnd = P[(np.hypot(P[:,0]-X0, P[:,1]-Y0) < 2.0)]
zg = np.median(np.sort(gnd[:,2])[:max(1,len(gnd)//3)])
# camera height from google depth: lower rows -> d*sin(-elev)
v = (np.arange(H)+0.5)/H; elev = (0.5 - v)*np.pi
rows = (elev < np.radians(-35)) & (elev > np.radians(-60))
hh = (g[rows] * np.sin(-elev[rows])[:,None]); hgs = float(np.median(hh[g[rows] > 0]))
print(f"pano XY {X0:.2f} {Y0:.2f}; lidar ground z {zg:.2f}; google elevation field {meta['elevation']:.2f}; google cam height from depth {hgs:.2f}")
heading = meta["heading"]
def render(dx, dy, dz, w=W, h=H, rgb=False):
    d = P - [X0+dx, Y0+dy, zg+hgs+dz]
    r = np.linalg.norm(d, axis=1)
    az = np.arctan2(d[:,0], d[:,1])                  # from north, clockwise
    el = np.arcsin(d[:,2]/np.maximum(r,1e-6))
    u = (((az - heading)/(2*np.pi) + 0.5) % 1.0) * w
    vv = (0.5 - el/np.pi) * h
    ui, vi = u.astype(int) % w, np.clip(vv.astype(int), 0, h-1)
    idx = vi*w + ui
    o = np.argsort(-r)                               # far first, near overwrite
    rng = np.full(h*w, np.nan, np.float32); rng[idx[o]] = r[o]
    if rgb:
        im = np.zeros((h*w,3), np.uint8); im[idx[o]] = C[o]; return rng.reshape(h,w), im.reshape(h,w,3)
    return rng.reshape(h,w)
valid_g = (g > 0) & (g < 50); valid_g[-50:] = False   # skip car / nadir
def score(R, shift):
    Rr = np.roll(R, shift, axis=1); m = valid_g & np.isfinite(Rr)
    return np.median(np.abs(np.log(Rr[m]/g[m]))), m.sum()
best=(9,None)
for dz in (-0.5, 0, 0.5):
  for dx in np.arange(-3,3.01,0.5):
    for dy in np.arange(-3,3.01,0.5):
      R = render(dx,dy,dz)
      for s in range(-10,11):
        e,n = score(R,s)
        if e < best[0]: best=(e,(dx,dy,dz,s))
print("coarse best median|log ratio| %.4f at dx,dy,dz,shift(px)=%s" % best)
print("at zero offset: %.4f" % min(score(render(0,0,0),s)[0] for s in [0]))
dx,dy,dz,s = best[1]
R, im = render(dx,dy,dz,2048,1024,rgb=True)
im = np.roll(im, s*4, axis=1)
pano = np.asarray(Image.open(f"pano_{pid}_preview.jpg").resize((2048,1024)))
blend = (0.5*pano + 0.5*np.where(im.sum(2,keepdims=True)>0, im, pano)).astype(np.uint8)
Image.fromarray(np.vstack([im, blend])).save(f"lidar_vs_pano_{pid}.jpg", quality=85)
print("yaw correction deg:", s*360/W)
