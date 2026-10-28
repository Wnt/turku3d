"""Offline Whisper audit of the delivered mix; never downloads a model."""
import json
import os
from pathlib import Path
import torch
import whisper

root=Path(os.environ['TURKU3D_WORK'])/'video/review'
model_file=Path.home()/'turku3d/tools/voice/cache/whisper/small.en.pt'
assert model_file.is_file()
torch.set_num_threads(3)
model=whisper.load_model(str(model_file),device='cpu')
result=model.transcribe(str(Path.home()/'turku3d/media/pipeline-video/turku3d-pipeline-v2.mp4'),
                        language='en',fp16=False,word_timestamps=True,verbose=False)
(root/'whisper.json').write_text(json.dumps(result,indent=2)+'\n')
(root/'whisper.txt').write_text(result['text']+'\n')
print(result['text'],flush=True)
