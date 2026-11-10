"""Fetch the exact public recording previews listed in the committed manifest."""
from pathlib import Path
import json,hashlib,urllib.request
manifest=json.loads((Path(__file__).parent/'sources.json').read_text())
dest=Path.home()/'turku3d/loops/sounds-astra/inputs';dest.mkdir(parents=True,exist_ok=True)
for row in manifest:
    file=dest/row['local_file']
    if not file.exists():
        with urllib.request.urlopen(row['download'],timeout=60) as response:file.write_bytes(response.read())
    assert hashlib.sha256(file.read_bytes()).hexdigest()==row['sha256'],row['id']
    print('Verified input',row['id'],row['author'])
