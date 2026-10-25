#!/usr/bin/env python3
"""Fetch cached Mapillary perspective photos without persisting credentials or signed URLs.

API fields: https://mapillary.github.io/mapillary-python-sdk/docs/mapillary.config.api/mapillary.config.api.entities/
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from qalib import geo
from qalib.paths import data_dir


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ids', required=True, help='comma-separated numeric image IDs')
    ap.add_argument('--out', type=Path, default=data_dir()/'mapillary')
    a=ap.parse_args()
    ids=a.ids.split(',')
    if not all(x.isdigit() for x in ids): ap.error('IDs must be numeric')
    token=(Path.home()/'.config/turku3d/mapillary.token').read_text().strip()
    a.out.mkdir(parents=True,exist_ok=True)
    for ident in ids:
        if (a.out/f'{ident}.json').exists() and (a.out/f'{ident}.jpg').exists(): continue
        response=requests.get(f'https://graph.mapillary.com/{ident}',headers={'Authorization': 'OAuth '+token},
            params={'fields':'id,computed_geometry,computed_compass_angle,captured_at,camera_type,camera_parameters,thumb_2048_url'},timeout=30)
        if not response.ok: raise SystemExit(f'Mapillary metadata HTTP {response.status_code}')
        m=response.json();url=m.pop('thumb_2048_url')
        image=requests.get(url,timeout=30)
        if not image.ok: raise SystemExit(f'Mapillary image HTTP {image.status_code}')
        lon,lat=m['computed_geometry']['coordinates'];m['E'],m['N']=geo.wgs84_to_en(lat,lon)
        (a.out/f'{ident}.jpg').write_bytes(image.content)
        (a.out/f'{ident}.json').write_text(json.dumps(m,indent=2))
        print(f'{ident}: cached')


if __name__=='__main__': main()
