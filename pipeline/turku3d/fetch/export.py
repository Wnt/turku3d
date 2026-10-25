"""Export checksums and area reports (no bulk data) into the repository."""
import argparse
import shutil
from .. import config as C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--area', default='office-block', choices=sorted(C.AREAS))
    a = ap.parse_args()
    dest = C.DATA_MANIFESTS / 'checksums'
    dest.mkdir(exist_ok=True)
    for kind in ('laz', 'wfs', 'ortho', 'osm'):
        shutil.copyfile(C.raw_dir(kind) / 'SHA256SUMS', dest / f'{kind}.sha256')
    shutil.copyfile(C.raw_dir() / f'REPORT-{a.area}.json', dest / f'REPORT-{a.area}.json')


if __name__ == '__main__':
    main()
