"""One bounded local ACE-Step run. No service, login, external audio or artist reference."""
from pathlib import Path
import json,time,signal,sys,os
# The board's catalogue.py is not the spaCy dependency named catalogue.
sys.path=[p for p in sys.path if Path(p or '.').resolve()!=Path(__file__).resolve().parent]
work=Path.home()/'turku3d/loops/sounds-astra'
os.environ['HF_HOME']=str(work/'work/hf-cache')
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
import torch
import soundfile as sf
from acestep.pipeline_ace_step import ACEStepPipeline

torch.set_num_threads(3)
torch.set_num_interop_threads(2)
torch.mps.set_per_process_memory_fraction(.48)
def deadline(*args):raise TimeoutError('Local model run exceeded the 8-minute compute budget')
signal.signal(signal.SIGALRM,deadline);signal.alarm(480)
choices={
 'm05-auransilta-fm':dict(duration=105,seed=110102,prompt='Instrumental downtempo nu jazz, 96 BPM, warm intimate electric piano with rich minor ninth jazz voicings, lyrical muted trumpet melody in short answering phrases, deep rounded electric bass groove, dry detailed live drum kit, brushed snare, crisp gentle shaker, subtle tape delay, spacious stereo studio production, late night drive along a northern European waterfront, sophisticated restrained groove, contrasting eight bar sections, instrumental only, no singing, no speech, no vocal sounds'),
 'm06-night-run':dict(duration=110,seed=110103,prompt='Instrumental driving electronic breakbeat, 124 BPM, powerful tight acoustic drums with syncopated breakbeat fills, deep controlled analog synthesizer bass, memorable short arpeggiated minor key melody, shimmering polysynth chords, tense but playful night driving energy, gradual arrangement changes and a quiet breakdown followed by a full energetic return, polished balanced stereo studio mix, full instrumental track, no singing, no speech, no vocal sounds'),
}
id=sys.argv[1] if len(sys.argv)>1 else 'm05-auransilta-fm';cfg=choices[id]
out=work/'work/model-audio';out.mkdir(exist_ok=True)
start=time.monotonic()
pipe=ACEStepPipeline(checkpoint_dir=str(work/'work/ace-weights'),torch_compile=False,cpu_offload=False,overlapped_decode=True)
# The current torchaudio save API delegates to torchcodec. Use the installed
# soundfile writer explicitly; this changes only file I/O, not synthesis.
def save_audio(target_wav,idx,save_path=None,sample_rate=48000,format='wav'):
    sf.write(save_path,target_wav.detach().float().cpu().numpy().T,sample_rate,subtype='PCM_24')
    return save_path
pipe.save_wav_file=save_audio
print('Starting local MPS generation',id,'dtype',pipe.dtype,'budget 480 s',flush=True)
result=pipe(audio_duration=cfg['duration'],prompt=cfg['prompt'],lyrics='[instrumental]',infer_step=27,guidance_scale=15,manual_seeds=[cfg['seed']],scheduler_type='euler',cfg_type='apg',save_path=str(out/f'{id}.wav'))
signal.alarm(0)
record={'id':id,**cfg,'model':'ACE-Step/ACE-Step-v1-3.5B','model_revision':'82cd0d7b6322bd28cd4e830fe675ddb6180ce36c','code_revision':'1bee4c9f5b43e30995f8d4d33b3919197ce1bd68','weight_licence':'Apache-2.0','source_url':'https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B','steps':27,'device':'Apple M1 Max / MPS','seconds_wall':round(time.monotonic()-start,2),'result':result}
(work/'evidence'/f'{id}-generation.json').write_text(json.dumps(record,indent=2,default=str))
print('Finished',id,record['seconds_wall'],'seconds',flush=True)
