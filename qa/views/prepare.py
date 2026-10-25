#!/usr/bin/env python3
"""Build the 20-view office reference set from existing read-only photo caches.

Only perspective crops and provenance are written into QA_DATA. Mapillary images are
undistorted with published normalized focal/k1/k2. Approximate poses stay low confidence.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from PIL import Image
from qalib import geo, registry
from qalib.pano import perspective
from qalib.paths import data_dir, rel
from views.heading import solve_heading
Image.MAX_IMAGE_PIXELS = None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--street-cache', type=Path, required=True)
    ap.add_argument('--photo-cache', type=Path, required=True, help='directory containing gmaps_spheres and 360tour')
    ap.add_argument('--mapillary-cache', type=Path, required=True)
    ap.add_argument('--registry', type=Path, default=registry.default_registry())
    a=ap.parse_args()
    out=data_dir()/'refs';out.mkdir(parents=True,exist_ok=True)
    rows=[]
    def add(view, image):
        target=out/f"{view['id']}.jpg";image.save(target,quality=93)
        view['image']=rel(target);view['area']='office-block';rows.append(view);registry.validate(view)
    def pano_view(ident, eq, heading, e,n,h,yaw,pitch,fov,source,date,confidence,w=960,hh=720):
        pose=dict(E=round(e,3),N=round(n,3),H=round(h,3),yaw=round(yaw%360,3),pitch=pitch,roll=0.,fov=fov)
        v=dict(id=ident,width=w,height=hh,pose=pose,time=None,source=source,capture_date=date,pose_confidence=confidence)
        add(v,perspective(eq,heading,pose,w,hh))
    sv_ids=['hOH16IE5o9B6ar0x7Cm-9w','GX4MZSFv2sCCPBWA8fNV0w','yf3WuGulqNxbTokkfH2HyQ',
            '9YcdVIRiQEgvodxtjAX71A','Ng7nDt08o1hOnHducUXOQw','8qDFHujwGqqIoUrPixDBXw',
            'yz7Il6o5Px4mZnWIV9myDw','-dy0uKYbCeJMLapdqPU0_A','UDCViuu4vkr9dnv1ZShiAw','YpJel-Jkzb-h1gkunsP7og']
    for i,ident in enumerate(sv_ids):
        folder=a.street_cache/'google'/ident;m=json.loads((folder/'meta.json').read_text())
        e,n=m['E'],m['N'];heading=geo.true_to_grid_bearing(e,n,math.degrees(m['heading_rad']))
        yaw=geo.bearing(e,n,geo.OFFICE_E,geo.OFFICE_N)
        pano_view(f'sv_{i+1:02}',Image.open(folder/'pano_z5.jpg'),heading,e,n,m['elevation'],yaw,10.,65.,
            dict(kind='google_streetview',pano_id=ident,pano_heading=math.degrees(m['heading_rad']),
                 url=f'https://www.google.com/maps/@?api=1&map_action=pano&pano={ident}',note='Cached zoom 5; pano tilt ignored'),m['date'],
            dict(level='medium',xy_m=1.5,h_m=.7,angle_deg=2.,note='Metadata pose; not yet lidar registered'))
    folder=a.street_cache/'lookaround/17922395357700297309';m=json.loads((folder/'meta.json').read_text())
    e,n=m['E'],m['N']
    # This cache stitches camera centre faces inverse travel; Apple headings are CCW.
    heading=geo.true_to_grid_bearing(e,n,-math.degrees(m['heading_rad']) + 180)
    for i,yaw in enumerate([230.,255.,290.]):
        pano_view(f'la_{i+1:02}',Image.open(folder/'equi_z2.jpg'),heading,e,n,m['elevation'],yaw,8.,55.,
            dict(kind='apple_lookaround',pano_id=m['id'],pano_heading=heading,
                 note='Side-face cache: centre = inverse travel, heading CCW converted to grid; visually checked, unregistered'),m['date'],
            dict(level='low',xy_m=2.,h_m=1.,angle_deg=10.,note='Camera metadata and side-face stitch; orientation needs lidar registration'))
    spheres=json.loads((a.photo_cache/'gmaps_spheres/photospheres.json').read_text())
    for i,ident in enumerate(['CIHM0ogKEICAgIDJgurEqwE','CIHM0ogKEICAgIDJguqm6wE']):
        m=next(x for x in spheres if x['id']==ident);e,n=geo.wgs84_to_en(m['lat'],m['lon'])
        heading=geo.true_to_grid_bearing(e,n,m['heading_deg'])
        pano_view(f'boat_{i+1:02}',Image.open(a.photo_cache/f'gmaps_spheres/{ident}_7680.jpg'),heading,e,n,2.,
            geo.bearing(e,n,geo.OFFICE_E,geo.OFFICE_N),12.,55.,
            dict(kind='gmaps_photosphere',pano_id=ident,pano_heading=m['heading_deg'],url=m['permalink'],
                 note='2023 boat-level panorama; H=2 m assumed, compass supplied by uploader'),m['date'],
            dict(level='low',xy_m=5.,h_m=1.5,angle_deg=10.,note='Boat height and uploaded compass unregistered'))
    folder=a.photo_cache/'360tour/panorama_B5D39CE1_B9C8_4DE2_41E6_5B7E45CDFEA0'
    m=json.loads((folder/'meta.json').read_text());e,n=geo.wgs84_to_en(m['lat'],m['lon'])
    landmarks=[]
    for name,lat,lon,x,source in [('Cathedral spire',60.4524,22.2781,1287,'https://en.wikipedia.org/wiki/Turku_Cathedral'),
                                ('Michael church spire',60.44873,22.24797,571,'https://mapcarta.com/37542244')]:
        le,ln=geo.wgs84_to_en(lat,lon)
        landmarks.append(dict(name=name,E=le,N=ln,tour_yaw=(x/2048-.5)*360,source=source,
                              measurement='manually identified spire column on 2048-wide equirect preview; about 2 px uncertainty'))
    solved=solve_heading(e,n,landmarks)
    (data_dir()/'drone-heading.json').write_text(json.dumps(solved,indent=2))
    heading=solved['heading_offset_grid_deg']
    pano_view('drone_01',Image.open(folder/'equirect_14476.jpg'),heading,e,n,m['gps_alt_m'],
        geo.bearing(e,n,geo.OFFICE_E,geo.OFFICE_N),-26.,46.8,
        dict(kind='360tour',pano_id=m['id'],pano_heading=heading,url='https://turku.360tour.fi/',
             note='Grid heading solved from two church spires; see drone-heading.json; copyright A1 Media Oy, internal QA'),
        '2016-05-12',dict(level='low',xy_m=10.,h_m=20.,angle_deg=3.,note='Two-landmark heading fit; drone GPS altitude not N2000 verified'))
    for ident in ['773201984101318','611404776950262','1553002508431099','4652560791426523']:
        m=json.loads((a.mapillary_cache/f'{ident}.json').read_text());im=np.array(Image.open(a.mapillary_cache/f'{ident}.jpg').convert('RGB'))
        h,w=im.shape[:2];f,k1,k2=m['camera_parameters'];f*=max(w,h)
        K=np.array([[f,0,w/2],[0,f,h/2],[0,0,1.]])
        im=cv2.undistort(im,K,np.array([k1,k2,0.,0.]))
        # Crop away distorted borders; focal remains unchanged before the uniform resize.
        pad=round(min(w,h)*.04);im=im[pad:h-pad,pad:w-pad];h,w=im.shape[:2]
        image=Image.fromarray(im);image.thumbnail((960,720));rw,rh=image.size
        e,n=m['E'],m['N'];yaw=geo.true_to_grid_bearing(e,n,m['computed_compass_angle'])
        view=dict(id=f'mly_{ident}',width=rw,height=rh,time=None,
             pose=dict(E=round(e,3),N=round(n,3),H=6.1,yaw=round(yaw,3),pitch=0.,roll=0.,fov=math.degrees(2*math.atan(h/2/f))),
             source=dict(kind='mapillary',pano_id=ident,url=f'https://www.mapillary.com/app/?pKey={ident}',
                         camera_parameters=m['camera_parameters'],note='Undistorted normalized focal/k1/k2; centre crop 4%; height/pitch/roll approximate'),
             capture_date=datetime.fromtimestamp(m['captured_at']/1000,timezone.utc).date().isoformat(),
             pose_confidence=dict(level='low',xy_m=3.,h_m=1.5,angle_deg=10.,note='SfM position and compass; H=6.1 m, pitch/roll=0 assumptions; registration required'))
        add(view,image)
    a.registry.write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in rows))
    print(f'{len(rows)} views -> {a.registry}');print(json.dumps(solved,indent=2))


if __name__=='__main__': main()
