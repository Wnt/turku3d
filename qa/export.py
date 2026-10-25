#!/usr/bin/env python3
"""Export a report with embedded images for private review (never ship reference photos)."""
import argparse
import base64
import html
import mimetypes
from pathlib import Path
import re


def export_report(source, destination):
    def inline(match):
        path = (source.parent / html.unescape(match.group(1))).resolve()
        mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        return 'src="data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode() + '"'
    document = re.sub(r'src="([^"]+)"', inline, source.read_text())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document)
    return destination


if __name__ == '__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source',type=Path)
    ap.add_argument('destination',type=Path)
    args=ap.parse_args()
    print(export_report(args.source,args.destination))
