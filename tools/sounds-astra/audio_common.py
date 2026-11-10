"""Deterministic, original soundboard tools. Original code/output: CC0-1.0.

Third-party inputs retain their own licences. No downloaded files enter git.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import numpy as np
from scipy import signal

ROOT = Path(__file__).resolve().parents[2]
WORK = Path.home() / 'turku3d/loops/sounds-astra'
BOARD = WORK / 'board'
RATE = 48000
RNG = np.random.default_rng(10261110)

def decode(path, channels=1):
    # Explicit arithmetic stereo downmix for analysis; FFmpeg's default -ac 1
    # uses 0.707 coefficients, which would make stereo comparisons 3 dB louder.
    probe=subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_entries','stream=channels','-of','csv=p=0',str(path)],capture_output=True,text=True,check=True)
    source_channels=int(probe.stdout.strip())
    mix=['-af','pan=mono|c0=0.5*c0+0.5*c1'] if channels==1 and source_channels==2 else []
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), *mix, '-f', 'f32le', '-ar', str(RATE), '-ac', str(channels), '-'], capture_output=True, check=True)
    data = np.frombuffer(r.stdout, dtype='<f4').copy()
    return data.reshape(-1, channels) if channels > 1 else data

def filt(x, hz, kind='lowpass', order=2):
    return signal.sosfilt(signal.butter(order, hz, btype=kind, fs=RATE, output='sos'), x, axis=0)

def fade(x, attack=.01, release=.03):
    x = np.array(x, copy=True)
    a, r = min(len(x)//2, round(attack*RATE)), min(len(x)//2, round(release*RATE))
    shape = (-1,1) if x.ndim == 2 else (-1,)
    if a: x[:a] *= np.linspace(0,1,a).reshape(shape)
    if r: x[-r:] *= np.linspace(1,0,r).reshape(shape)
    return x

def loop_join(x, seconds=.25):
    """Overlap the tail onto the head, then retain the same continuous junction.

    Linear weights avoid gain bumps for correlated ambience. Output length is
    reduced by the overlap; internal timing is otherwise untouched.
    """
    n = min(round(seconds*RATE), len(x)//4)
    shape = (-1,1) if x.ndim == 2 else (-1,)
    ramp = np.linspace(0,1,n,endpoint=False).reshape(shape)
    x = np.array(x, copy=True)
    x[-n:] = x[-n:]*(1-ramp) + x[:n]*ramp
    return x[n:]

def active_rms(x):
    mono = x.mean(axis=1) if x.ndim == 2 else x
    n = len(mono)//480
    if not n: return 0
    frames=np.sqrt(np.mean(mono[:n*480].reshape(n,480)**2, axis=1))
    selected=frames[frames > max(1e-6, frames.max()*.1)]
    return np.sqrt(np.mean(selected**2)) if len(selected) else 0

def save(id, x, loop=False, target=-22, bitrate=None, normalise=True):
    x=np.asarray(x,dtype=np.float64)
    x-=x.mean(axis=0)
    if normalise:
        x*=10**(target/20)/max(active_rms(x),1e-9)
    peak=float(np.max(np.abs(x)))
    if peak>.79: x*=.79/peak
    channels=1 if x.ndim==1 else x.shape[1]
    bitrate=bitrate or ('96k' if channels==1 else '160k')
    dest=BOARD/'audio'/f'{id}.ogg'; dest.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(['ffmpeg','-y','-v','error','-f','f32le','-ar',str(RATE),'-ac',str(channels),'-i','pipe:0','-c:a','libopus','-b:a',bitrate,'-application','audio','-map_metadata','-1',str(dest)],input=np.asarray(x,dtype='<f4').tobytes(),check=True)
    return {'id':id,'file':f'audio/{id}.ogg','seconds':round(len(x)/RATE,4),'channels':channels,'loop':loop,'bytes':dest.stat().st_size,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}

def add(dst, src, at, gain=1., pan=0, wrap=False):
    start=round(at*RATE)
    if dst.ndim==2 and src.ndim==1:
        src=src[:,None]*np.array([np.cos((pan+1)*np.pi/4),np.sin((pan+1)*np.pi/4)])[None,:]
    if start<0:
        src=src[-start:];start=0
    if wrap:
        start%=len(dst)
        n=min(len(src),len(dst)-start);dst[start:start+n]+=src[:n]*gain
        if n<len(src):dst[:len(src)-n]+=src[n:]*gain
    else:
        n=min(len(src),len(dst)-start)
        if n>0:dst[start:start+n]+=src[:n]*gain

def write_manifest(name, rows):
    (WORK/'work'/f'{name}.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n')
