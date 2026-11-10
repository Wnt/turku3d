"""Prepare licensed gull recordings and original synthesis for the independent board."""
from audio_common import *
import shutil

rows=[]
def emit(id,x,**kw):
    row=save(id,x,**kw);rows.append(row);print(id,row['seconds'],flush=True)

def crop(x,a,b):return fade(x[round(a*RATE):round(b*RATE)],.015,.06)

# The exact checked-in bytes remain the first A/B choices.
for sound in ['gulls','screech','roll']:
    src=ROOT/'game/public/assets/audio'/f'{sound}.ogg';dest=BOARD/'audio'/f'current-{sound}.ogg'
    shutil.copyfile(src,dest);x=decode(src)
    rows.append({'id':f'current-{sound}','file':f'audio/current-{sound}.ogg','seconds':len(x)/RATE,'channels':1,'loop':True,'bytes':dest.stat().st_size,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})

for src in sorted((WORK/'work/baseline').glob('*.f32')):
    x=np.fromfile(src,dtype='<f4')
    if 'truck' in src.stem:x=loop_join(x[RATE:],.25)
    emit(src.stem,x,loop='truck' in src.stem,target=-22)

herring=filt(decode(WORK/'inputs/538016.mp3'),180,'highpass')
beach=filt(decode(WORK/'inputs/353416.mp3'),110,'highpass')
common=filt(decode(WORK/'inputs/73616.mp3'),220,'highpass')
single=crop(herring,8.30,9.13)
series=crop(herring,8.25,12.75)
reply=crop(herring,13.7,18.85)
emit('g01-herring-single',single)
emit('g02-herring-series',series)
# Mixed-species source: do not claim an isolated long call or a species-validated individual syllable.
emit('g03-common-lakeside',crop(common,1.2,24.9))
emit('g04-herring-beach',crop(beach,0,7.4))

# A composition with separate sparse emitters and a quiet synthetic waterfront bed.
# Early reflections, not a cathedral reverb; not claimed as a Turku field recording.
duration=64
n=round(duration*RATE);t=np.arange(n)/RATE
harbour=np.zeros((n,2))
for channel in range(2):
    white=RNG.normal(0,1,n);water=filt(filt(white,180,'highpass'),1600)
    swell=.6+.18*np.sin(2*np.pi*t/7.3+channel)+.12*np.sin(2*np.pi*t/13.7)
    harbour[:,channel]=water*swell*.015
for at,call,pan,gain,cutoff in [(2,series,-.6,.23,6500),(15,reply,.45,.12,4200),(30,single,-.15,.3,8000),(39,series,.7,.07,2800),(53,reply,-.7,.13,3800)]:
    dry=filt(call,cutoff);add(harbour,dry,at,gain,pan)
    add(harbour,filt(dry,4200),at+.004,gain*.20,pan)
    add(harbour,filt(dry,2400),at+.064,gain*.12,-pan)
    add(harbour,filt(dry,1600),at+.139,gain*.07,pan*.2)
emit('g05-harbour',loop_join(harbour,4),loop=True,target=-27,bitrate='128k')

# Source-filter study of alarm and mew gestures. Parameter ranges guided by
# European herring-gull alarm literature; these are clearly labelled imitations.
def gull_voice(duration,kind='mew',variant=0):
    t=np.arange(round(duration*RATE))/RATE;u=t/duration
    if kind=='kek':
        f0=385-55*u+6*np.sin(2*np.pi*19*t);env=np.sin(np.pi*np.clip(u,0,1))**.7
    else:
        f0=np.interp(u,[0,.12,.3,.7,1],[780,1390,1270,940,720])
        f0+=14*np.sin(2*np.pi*7*t)+filt(RNG.normal(0,1,len(t)),35)*12
        env=(1-np.exp(-t*50))*np.exp(-u*1.5)*np.minimum(1,(1-u)*10)
    phase=2*np.pi*np.cumsum(f0)/RATE
    x=np.zeros(len(t))
    for harmonic in range(1,14):
        freq=harmonic*f0
        formant=.12+np.exp(-((freq-1230)/400)**2)+.65*np.exp(-((freq-1980)/400)**2)+.4*np.exp(-((freq-3220)/650)**2)
        x+=np.sin(harmonic*phase+variant*.2)*formant/harmonic**.85
    breath=filt(filt(RNG.normal(0,1,len(t)),650,'highpass'),5500)
    return fade((x+.10*breath)*env,.006,.03)
alarm=np.zeros(round(2.8*RATE))
for i,at in enumerate([.15,.44,.78,1.17,1.62,2.10]):add(alarm,gull_voice(.19+.012*i,'kek',i),at,.75+i*.035)
emit('g06-alarm-model',alarm)
emit('g07-mew-model',np.pad(gull_voice(1.12), (round(.15*RATE),round(.4*RATE))))

# Same recorded call at near/mid/far: audibly lower high-frequency content and
# more diffuse early reflections. Gains are kept relative, not renormalised per step.
distance=np.zeros((round(17*RATE),2))
for at,gain,hz,pan,metres in [(0.3,1,11000,-.2,15),(6,.38,4000,.15,60),(11.7,.14,2100,.45,150)]:
    dry=filt(series,hz);add(distance,dry,at,gain,pan)
    # Image-source path difference over a flat water plane: emitter 12 m,
    # listener 1.7 m. Bank/building reflections are a separate harbour effect.
    delay=(np.hypot(metres,12+1.7)-np.hypot(metres,12-1.7))/343
    add(distance,filt(dry,3600),at+delay,gain*.24,pan)
emit('g08-distance',fade(distance),target=-22)

# Real tyre recordings, retain their natural attacks. Original source files are
# three-second demonstrations, not generic reusable sustained-drift beds.
for id,source in [('t01-recorded-chirp','2368'),('t07-recorded-skid','2370')]:
    x=filt(decode(WORK/'inputs'/f'{source}.mp3'),230,'highpass')
    emit(id,fade(x,.005,.04))

# Common gain for synth demonstrations preserves changes in intensity/load.
paths=sorted((WORK/'work/tyres').glob('*.f32'))
peak_rms=active_rms(np.fromfile(WORK/'work/tyres/t04-drift-heavy.f32',dtype='<f4'))
gain=10**(-22/20)/peak_rms
for src in paths:
    x=np.fromfile(src,dtype='<f4')*gain;isloop='drift' in src.stem
    x=loop_join(x[RATE:],1) if isloop else fade(x,.015,.07)
    emit(src.stem,x,loop=isloop,normalise=False)
write_manifest('effects',rows)
