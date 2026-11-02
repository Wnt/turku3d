"""Read public COG windows into an immutable local horizon grid; never write raw data."""
import argparse, hashlib, json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.warp import transform_bounds, reproject, Resampling
from rasterio.windows import from_bounds
from rasterio.transform import from_origin

DEM = "https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
COVER = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N60E021_Map.tif"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--plan', type=Path, required=True)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
# Snap outward to 4 km far nodes; include one node beyond the requested ring.
import math
extent = json.loads(args.plan.read_text())['regional_bounds_local']
x0,z0,x1,z1 = [math.floor(v/4000)*4000-4000 if i<2 else math.ceil(v/4000)*4000+4000 for i,v in enumerate(extent)]
step = 15.625
shape = (int((z1-z0)/step)+1, int((x1-x0)/step)+1)
transform = from_origin(23459500+x0-step/2, 6704500-z0+step/2, step, step)
bounds = (23459500+x0-100, 6704500-z1-100, 23459500+x1+100, 6704500-z0+100)
urls = [DEM.format(name=f"Copernicus_DSM_COG_10_N60_00_E0{lon}_00_DEM") for lon in [21,22]]
height = np.full(shape, -9999, dtype=np.float32)
cover = np.zeros(shape, dtype=np.uint8)
with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif', GDAL_HTTP_MAX_RETRY='3'):
    for url in [*urls, COVER]:
        with rasterio.open(url) as src:
            west,south,east,north=transform_bounds('EPSG:3877',src.crs,*bounds,densify_pts=21)
            west=max(west,src.bounds.left);east=min(east,src.bounds.right)
            south=max(south,src.bounds.bottom);north=min(north,src.bounds.top)
            window=from_bounds(west,south,east,north,src.transform).round_offsets().round_lengths()
            data=src.read(1,window=window)
            target = cover if url==COVER else np.full(shape,-9999,dtype=np.float32)
            reproject(data,target,src_transform=src.window_transform(window),src_crs=src.crs,
                      src_nodata=src.nodata,dst_transform=transform,dst_crs='EPSG:3877',
                      dst_nodata=0 if url==COVER else -9999,init_dest_nodata=False,
                      resampling=Resampling.nearest if url==COVER else Resampling.bilinear)
            if url!=COVER:
                valid=target!=-9999; height[valid]=target[valid]
            print(json.dumps({'source':url,'window':list(data.shape)}),flush=True)
# The independently warped one-degree DEMs leave a subpixel seam. Interpolate
# only an interior gap narrower than two native DEM samples; never fill an area.
seam_pixels=int((height == -9999).sum())
for row in height:
    good=np.flatnonzero(row!=-9999); bad=np.flatnonzero(row==-9999)
    if not len(bad): continue
    runs=np.split(bad,np.flatnonzero(np.diff(bad)>1)+1)
    if good[0]!=0 or good[-1]!=len(row)-1 or any(len(run)>4 for run in runs):
        raise RuntimeError('Regional DEM has an area without source coverage')
    row[bad]=np.interp(bad,good,row[good])
if np.any(cover==0): raise RuntimeError('Regional land cover has uncovered pixels')
height[cover==80] = 0
file=args.out/'regional.npz'
np.savez_compressed(file,height=height,cover=cover,xmin=float(x0),zmin=float(z0),step=step)
sha=hashlib.sha256(file.read_bytes()).hexdigest()
(args.out/'sources.json').write_text(json.dumps({'sha256':sha,'sources':[*urls,COVER],
 'dem_seam_interpolated_pixels':seam_pixels,'crs':'EPSG:3877','extent_local':[x0,z0,x1,z1], 'grid_m':step,
 'attribution':['produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved',
 '© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium'],
 'liability':'The organisations in charge of the Copernicus programme by law or by delegation do not incur any liability for any use of the Copernicus WorldDEM-30',
 'licenses':['https://docs.sentinel-hub.com/api/latest/static/files/data/dem/resources/license/License-COPDEM-30.pdf','https://creativecommons.org/licenses/by/4.0/']},indent=2))
print(json.dumps({'sha256':sha,'bytes':file.stat().st_size}),flush=True)
