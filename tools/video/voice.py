"""Offline Kokoro auditions and sentence-addressed narration; no downloads."""
import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from kokoro import KModel, KPipeline

ap = argparse.ArgumentParser()
ap.add_argument('mode', choices=['audition', 'narrate'])
ap.add_argument('--voice', default='bf_emma')
a = ap.parse_args()
root = Path(os.environ['TURKU3D_WORK']) / 'video/audio'
root.mkdir(parents=True, exist_ok=True)
model_root = next((Path.home() / 'turku3d/tools/voice/hf').rglob('snapshots/*/config.json')).parent
torch.set_num_threads(3)
model = KModel(repo_id='hexgrad/Kokoro-82M', config=str(model_root / 'config.json'),
               model=str(model_root / 'kokoro-v1_0.pth')).to('cpu').eval()
pipes = {}

def say(text, voice, speed=1.03, with_timing=False):
    if voice[0] not in pipes:
        pipes[voice[0]] = KPipeline(lang_code=voice[0], repo_id='hexgrad/Kokoro-82M', model=model)
    pipe = pipes[voice[0]]
    pack = pipe.load_single_voice(str(model_root / 'voices' / f'{voice}.pt'))
    torch.manual_seed(2027)
    spoken=text.replace('Turku', '[Turku](/tˈurku/)')
    chunks=[]; timing=[]; offset=0
    for r in pipe(spoken, voice=pack, speed=speed):
        data=r.audio.numpy()
        for token in r.tokens or []:
            if token.start_ts is not None and token.end_ts is not None:
                timing.append({'text':token.text,'start':offset+token.start_ts,'end':offset+token.end_ts})
        chunks.append(data);offset+=len(data)/24000
    data=np.concatenate(chunks)
    return (data,timing) if with_timing else data

if a.mode == 'audition':
    for p in sorted((model_root / 'voices').glob('*.pt')):
        if p.stem[0] not in 'ab':
            continue
        output = root / f'audition-{p.stem}.wav'
        if not output.exists():
            sf.write(output, say('From laser points to a playable city. Explore Turku, starting at Läntinen Rantakatu fifteen.', p.stem), 24000)
        print(output.name, flush=True)
else:
    story = json.loads(Path('tools/video/story.json').read_text())
    index = []
    try:
        previous = json.loads((root / 'index.json').read_text())
    except FileNotFoundError:
        previous = []
    for scene in story:
        # Sentence clips give subtitles exact boundaries and avoid long TTS breath groups.
        import re
        sentences = re.split(r'(?<=[.!?])\s+', scene['text'])
        for i, sentence in enumerate(sentences):
            cached = next((r for r in previous if r['scene'] == scene['id'] and r['sentence'] == sentence
                           and r.get('timing_version') == 2 and f'-{a.voice}' in Path(r['path']).stem and Path(r['path']).exists()), None)
            key = hashlib.sha256((a.voice + '\0v2-timed-2\0' + sentence).encode()).hexdigest()[:12]
            output = Path(cached['path']) if cached else root / f"{scene['id']}-{i}-{a.voice}-{key}.wav"
            if not cached:
                data, timing = say(sentence, a.voice, with_timing=True)
                active = np.flatnonzero(np.abs(data) > 0.002)
                if len(active):
                    begin=max(0, active[0]-1200)
                    data = data[begin:min(len(data), active[-1]+2400)]
                    timing=[{**t,'start':max(0,t['start']-begin/24000),'end':min(len(data)/24000,t['end']-begin/24000)} for t in timing]
                sf.write(output, data, 24000, subtype='PCM_24')
            else:timing=cached['tokens']
            data, rate = sf.read(output)
            index.append({'scene':scene['id'], 'sentence':sentence, 'path':str(output), 'seconds':len(data)/rate, 'speed':1.03,
                          'timing_version':2,'tokens':timing})
        print(scene['id'], flush=True)
    (root / 'index.json').write_text(json.dumps(index, indent=2, ensure_ascii=False))
