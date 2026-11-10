"""Publishable one-shot radio candidates from our local model runs.

Generated output is offered under CC0 to the extent any rights are held.
Model weights are Apache-2.0; no claim that Apache licenses the generated music.
"""
from audio_common import *
import sys
rows=json.loads((WORK/'work/music.json').read_text())
for id in sys.argv[1:]:
    x=decode(WORK/'work/model-audio'/f'{id}.wav',channels=2)
    assert np.isfinite(x).all() and np.max(abs(x))>.01
    x=fade(x,.04,2.5)
    row=save(id,x,loop=False,target=-21,bitrate='160k')
    row.update(method='local ACE-Step v1, 27 steps; original text prompt; short end fade',model='ACE-Step/ACE-Step-v1-3.5B',model_revision='82cd0d7b6322bd28cd4e830fe675ddb6180ce36c',weight_licence='Apache-2.0',licence='CC0-1.0 to the extent rights are held')
    rows=[r for r in rows if r['id']!=id];rows.append(row)
    print(row,flush=True)
write_manifest('music',rows)
