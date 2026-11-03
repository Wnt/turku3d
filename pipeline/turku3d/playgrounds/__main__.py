"""python -m turku3d.playgrounds import --survey ~/leikkipuistodata [--out PATH]"""

import argparse
import json
from pathlib import Path

from . import dataset_path, import_survey, write


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("import", help="normalise a leikkipuistodata checkout")
    run.add_argument("--survey", type=Path, required=True)
    run.add_argument("--out", type=Path, help="default: $TURKU3D_DATA/derived/playgrounds/playgrounds.json")
    args = parser.parse_args(argv)
    dataset = import_survey(args.survey.expanduser())
    path = write(dataset, args.out or dataset_path())
    print(json.dumps(dict(out=str(path), **dataset["counts"], sha256=dataset["source"]["sha256"])))


if __name__ == "__main__":
    main()
