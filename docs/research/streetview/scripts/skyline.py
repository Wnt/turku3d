# Pano pose refinement against airborne lidar via SKYLINE matching (robust to sparse facades).
import sys, json, numpy as np, laspy
from pyproj import Transformer
from PIL import Image
pid = sys.argv[1]; tiles = sys.argv[2:]
meta = json.load(open(f"meta_{pid}.json"))
X0, Y0 = Transformer.from_crs(4326, 3877, always_xy=True).transform(meta["lon"], meta["lat"])
W, H = 2048, 1024
# --- observed skyline from the photo (blue-sky mask, first 6 consecutive non-sky px from top)
im = np.asarray(Image.open(f"pano_{pid}_preview.jpg").resize((W,H))).astype(int)
r,g,b = im[...,0], im[...,1], im[...,2]
sky = ((b - r > 25) & (b > 120)) | ((r+g+b > 700))
sky[H//2:] = False
nonsky = ~sky
run = np.zeros_like(nonsky, int)
for k in range(6): run += np.roll(nonsky, -k, axis=0)
first = np.argmax(run >= 6, axis=0).astype(float)
obs_valid = sky[:5].all(0) & (first > 5) & (first < H*0.49)
# --- DSM (max z, 0.5 m) from lidar within 220 m
R = 220; pts=[]
for t in tiles:
    l = laspy.read(t); x,y,z = np.asarray(l.x),np.asarray(l.y),np.asarray(l.z)
    m = (abs(x-X0)<R)&(abs(y-Y0)<R)&(np.asarray(l.classification)!=7); pts.append(np.c_[x[m],y[m],z[m]])
P = np.vstack(pts); cell=0.5
x0, y0 = X0-R, Y0-R; n = int(2*R/cell)
ix = ((P[:,0]-x0)/cell).astype(int).clip(0,n-1); iy = ((P[:,1]-y0)/cell).astype(int).clip(0,n-1)
dsm = np.full((n,n), -50.0); np.maximum.at(dsm, (iy,ix), P[:,2])
near = np.hypot(P[:,0]-X0, P[:,1]-Y0) < 2.0
zg = np.percentile(P[near,2], 10)
print(f"points {len(P)}, ground under pano {zg:.2f}, google elevation {meta['elevation']:.2f} -> implied cam height {meta['elevation']-zg:.2f}")
heading = meta["heading"]
az = heading + ((np.arange(W)+0.5)/W - 0.5)*2*np.pi
dist = np.arange(1.5, 200, 0.5)
sa, ca = np.sin(az), np.cos(az)
def skyline(dx,dy,h):
    xs = X0+dx + dist[None,:]*sa[:,None]; ys = Y0+dy + dist[None,:]*ca[:,None]
    jx = ((xs-x0)/cell).astype(int).clip(0,n-1); jy = ((ys-y0)/cell).astype(int).clip(0,n-1)
    el = np.arctan2(dsm[jy,jx] - (zg+h), dist[None,:]).max(1)
    return (0.5 - el/np.pi)*H, el
def score(dx,dy,h):
    rows, el = skyline(dx,dy,h); best=(1e9,0)
    for s in range(-30,31):
        rr = np.roll(rows, s); ee = np.roll(el, s)
        m = obs_valid & (ee > np.radians(3))
        e = np.median(np.abs(rr[m]-first[m])) if m.sum()>200 else 1e9
        if e < best[0]: best=(e,s)
    return best
res=[]
for h in (2.0, 2.5, 3.0):
  for dx in np.arange(-4,4.01,0.5):
    for dy in np.arange(-4,4.01,0.5):
      e,s = score(dx,dy,h); res.append((e,dx,dy,h,s))
res.sort()
e0 = score(0,0,meta['elevation']-zg)
px = 360/W
print(f"no-correction (dx=dy=0, h=google): median skyline err {e0[0]:.2f}px at yaw shift {e0[1]} px ({e0[1]*px:.2f} deg)")
for e,dx,dy,h,s in res[:6]: print(f"err {e:.2f}px ({e*px:.2f} deg)  dx {dx:+.1f} dy {dy:+.1f} h {h} yaw {s*px:+.2f} deg")
e,dx,dy,h,s = res[0]
rows,_ = skyline(dx,dy,h); rows=np.roll(rows,s)
vis = im.copy().astype(np.uint8)
for u in range(W):
    if obs_valid[u]: vis[int(first[u]), u] = [255,0,0]
    rv = int(np.clip(rows[u],0,H-1)); vis[max(rv-1,0):rv+1, u] = [255,255,0]
Image.fromarray(vis).save(f"skyline_{pid}.jpg", quality=88)
