"""Original, deterministic offline compositions and instruments, CC0-1.0.

No recordings, pretrained model, imitation prompt, MIDI or external music input.
Render one track at a time. All rhythmic periods are exact integer bar lengths;
note tails, delays and reverb wrap around that length for genuine circular loops.
"""
from audio_common import *
import sys

def hz(midi):return 440*2**((midi-69)/12)

def instrument(midi,duration,kind,velocity=1):
    f=hz(midi);t=np.arange(round((duration+.6)*RATE))/RATE
    release=np.exp(-np.maximum(0,t-duration)/(0.15 if kind=='bass' else .22))
    if kind=='ep':
        # Bell and tine decay independently; velocity changes harmonic content.
        index=(1.1+velocity*.6)*np.exp(-t*3.5)
        y=np.sin(2*np.pi*f*t+index*np.sin(2*np.pi*f*2*t))*.72
        y+=.17*np.sin(2*np.pi*f*3*t)*np.exp(-t*5)
        env=(1-np.exp(-t*300))*np.exp(-t*1.4)*release
    elif kind=='glass':
        y=np.sin(2*np.pi*f*t)*np.exp(-t*.6)+.22*np.sin(2*np.pi*f*2.006*t)*np.exp(-t*1.7)
        y+=.1*np.sin(2*np.pi*f*3.99*t)*np.exp(-t*3)
        env=(1-np.exp(-t*220))*release
    elif kind=='pad':
        y=np.zeros(len(t))
        for h in range(1,7):
            y+=np.sin(2*np.pi*f*h*t+.08*np.sin(2*np.pi*.27*t))/(h*h)
            y+=.5*np.sin(2*np.pi*f*1.0018*h*t+.07*np.sin(2*np.pi*.34*t))/(h*h)
        env=(1-np.exp(-t*3))*release*.55
    elif kind=='bass':
        phase=2*np.pi*f*t
        y=np.sin(phase)*.8+np.sin(2*phase)*.23*np.exp(-t*4)+np.sin(3*phase)*.13*np.exp(-t*7)
        y+=.04*filt(RNG.normal(0,1,len(t)),1400)*np.exp(-t*50)
        env=(1-np.exp(-t*750))*np.exp(-t*1.8)*release
    elif kind=='pluck':
        # Damped string-like partials with a small pick transient, no sample.
        y=np.zeros(len(t))
        for h in range(1,min(19,int(16000/f))):
            y+=np.cos(2*np.pi*f*h*np.sqrt(1+.00004*h*h)*t)*np.sin(h*.39)/(h**1.2)*np.exp(-t*(1.4+h*.85))
        y+=.055*filt(RNG.normal(0,1,len(t)),3000)*np.exp(-t*90)
        env=(1-np.exp(-t*600))*release
    elif kind=='lead':
        y=np.zeros(len(t))
        for h in range(1,min(12,int(16000/f))):
            y+=(np.sin(2*np.pi*f*h*t)+.45*np.sin(2*np.pi*f*1.002*h*t))/(h**1.4)*np.exp(-t*h*.45)
        env=(1-np.exp(-t*250))*np.exp(-t*2)*release*.65
    else:raise ValueError(kind)
    return fade(y*env*velocity,.002,.03)

def drum(kind,velocity=1):
    t=np.arange(round(.65*RATE))/RATE;n=RNG.normal(0,1,len(t))
    if kind=='kick':
        freq=49+105*np.exp(-t*45)
        y=np.sin(2*np.pi*np.cumsum(freq)/RATE)*np.exp(-t*13)+.06*filt(n,3500)*np.exp(-t*180)
    elif kind=='snare':
        noise=filt(filt(n,1100,'highpass'),9500)
        y=(.62*noise*np.exp(-t*22)+.32*np.sin(2*np.pi*178*t)*np.exp(-t*25)+.16*np.sin(2*np.pi*320*t)*np.exp(-t*40))
        y*=1+.13*np.sin(2*np.pi*95*t)*np.exp(-t*20)
    elif kind=='rim':
        y=(np.sin(2*np.pi*860*t)+.45*np.sin(2*np.pi*1520*t)+.5*filt(n,2200))*np.exp(-t*95)*.4
    elif kind=='hat':
        y=filt(n,6800,'highpass')*np.exp(-t*66)*.3
    elif kind=='openhat':
        y=filt(n,6300,'highpass')*np.exp(-t*14)*.25
    elif kind=='shaker':
        y=filt(n,4200,'highpass')*np.sin(np.pi*np.minimum(t/.14,1))**2*np.exp(-t*18)*.22
    elif kind=='clap':
        y=np.zeros(len(t));noise=filt(n,[900,6500],'bandpass')
        for at,g in [(0,.6),(.012,.8),(.024,.6),(.038,.7)]:y+=noise*np.exp(-np.maximum(t-at,0)*55)*(t>=at)*g
        y*=.2
    else:raise ValueError(kind)
    return fade(y*velocity,.0005,.01)

class Song:
    def __init__(self,bpm,bars):
        self.bpm=bpm;self.bars=bars;self.beat=60/bpm;self.seconds=bars*4*self.beat
        self.n=round(self.seconds*RATE)
        self.stems={k:np.zeros((self.n,2),np.float32) for k in ['harmony','melody','bass','drums']}
        self.kicks=[]
    def note(self,stem,beat,midi,duration,kind,gain=.1,pan=0,human=False):
        at=beat*self.beat+(RNG.uniform(-.007,.007) if human else 0)
        at%=self.seconds
        x=instrument(midi,duration*self.beat,kind,RNG.uniform(.88,1.03))
        add(self.stems[stem],x,at,gain,pan,wrap=True)
    def chord(self,beat,notes,duration,kind,gain=.04):
        for j,note in enumerate(notes):self.note('harmony',beat+j*.008,note,duration,kind,gain,(-.6+j*.3),True)
    def hit(self,beat,kind,gain):
        add(self.stems['drums'],drum(kind,RNG.uniform(.88,1.04)),(beat*self.beat)%self.seconds,gain,RNG.uniform(-.15,.15),wrap=True)
        if kind=='kick':self.kicks.append(beat*self.beat)
    def finish(self,id,duck=.12,space=.14,delay=.12):
        # Subtle ducking on harmony only, no bus pumping or crushed percussion.
        contour=np.ones(self.n)
        for at in self.kicks:
            i=round(at*RATE);n=min(round(.35*RATE),self.n-i);contour[i:i+n]-=duck*np.exp(-np.arange(n)/RATE*15)
        self.stems['harmony']*=contour[:,None]
        dry=sum(self.stems.values())
        send=self.stems['harmony']+self.stems['melody']+.14*self.stems['drums']
        # Circular stereo reverb with independent decorrelated tails.
        for channel in range(2):
            length=round(1.7*RATE);t=np.arange(length)/RATE
            ir=filt(RNG.normal(0,1,length),4300)*np.exp(-t*4)
            ir[:round(.022*RATE)]=0;ir/=np.sqrt(np.sum(ir*ir));ir*=space
            for at,g in [(.031,.12),(.073,.08),(.113,.05)]:ir[round((at+channel*.007)*RATE)]+=g*space
            ir=np.pad(ir,(0,self.n-length))
            dry[:,channel]+=np.fft.irfft(np.fft.rfft(send[:,channel])*np.fft.rfft(ir),n=self.n)
        melody=self.stems['melody']
        for taps in range(1,5):
            d=round(self.beat*.75*taps*RATE)
            dry+=np.roll(melody[:,::-1] if taps%2 else melody,d,axis=0)*(delay*.58**(taps-1))
        dry=filt(dry,30,'highpass')
        # Remove the IIR startup boundary with a periodic settling pass.
        sos=signal.butter(2,30,btype='highpass',fs=RATE,output='sos')
        _,state=signal.sosfilt(sos,dry[-RATE:],axis=0,zi=np.zeros((len(sos),2,2)))
        dry,_=signal.sosfilt(sos,dry,axis=0,zi=state)
        dry=np.tanh(dry*1.12)/1.12
        row=save(id,dry,loop=True,target=-21,bitrate='160k')
        row.update(bpm=self.bpm,bars=self.bars,method='original offline composition and synthesis',licence='CC0-1.0')
        print(id,row['seconds'],flush=True)
        return row

def river():
    s=Song(96,48)
    chords=[[55,62,66,69],[52,59,62,66],[48,55,59,62],[50,57,60,64],
            [55,59,62,69],[52,59,64,67],[48,55,59,64],[50,57,61,67]]
    roots=[43,40,36,38,43,40,36,38]
    motifs=[[74,71,69,66,67,69],[71,69,66,64,62,66],[67,71,74,76,74,71],[69,66,64,62,61,62]]
    for bar in range(48):
        b=bar*4;c=chords[bar%8];root=roots[bar%8]
        section=bar//8;drums=section not in [0,3] or bar%8>=4
        for offset in [.0,1.75,3.25]:s.chord(b+offset,c,.8,'ep',.052 if section!=3 else .033)
        if section in [1,2,4,5]:
            for offset,interval,dur in [(0,0,.75),(1.5,7,.35),(2.25,12,.4),(3.5,7,.3)]:s.note('bass',b+offset,root-12+interval,dur,'bass',.27)
        if drums:
            for off in [0,1.75,2.5]:s.hit(b+off,'kick',.21)
            for off in [1,3]:s.hit(b+off,'snare',.13)
            if bar%4==3:s.hit(b+3.75,'rim',.09)
            for i in range(8):s.hit(b+i*.5+(.045 if i%2 else 0),'hat',.095 if i%2 else .06)
            s.hit(b+2.75,'shaker',.12)
        if section in [1,2,4] and bar%2==0:
            melody=motifs[(bar//2)%4]
            for off,note in zip([.5,1.25,2.5,3.25,4.5,6],melody):s.note('melody',b+off,note,.5,'ep',.10,pan=.18,human=True)
        if section in [0,3,5]:s.note('melody',b+.5,c[-1]+12,2.8,'glass',.05,pan=-.25)
    return s.finish('m01-river-afterhours',duck=.10,space=.12,delay=.18)

def harbour():
    s=Song(72,32)
    chords=[[50,57,61,64,69],[47,54,57,62,66],[43,50,54,57,62],[45,52,57,59,64]]
    for bar in range(32):
        b=bar*4;c=chords[(bar//2)%4]
        if bar%2==0:s.chord(b,c,7.8,'pad',.025)
        if bar%2==0:s.note('bass',b,c[0]-12,6,'glass',.045)
        # Long, sparse phrases; motifs answer rather than repeat every bar.
        if bar%4 in [0,1]:
            notes=[c[2]+12,c[3]+12,c[4]+12] if bar%4==0 else [c[4]+12,c[2]+12]
            for off,note in zip([.5,2.25,3.5],notes):s.note('melody',b+off,note,2.2,'glass',.042,pan=(-.4 if bar%2 else .4))
        if 8<=bar<24 and bar%2:s.chord(b+2.5,[x+12 for x in c[1:4]],2.5,'ep',.02)
        if 12<=bar<20:
            for i in range(4):s.hit(b+i,'shaker',.028)
    return s.finish('m02-harbour-light',duck=0,space=.26,delay=.3)

def pursuit():
    s=Song(124,64)
    chords=[[57,60,64,71],[53,60,64,69],[50,57,60,64],[52,59,62,68]]
    roots=[33,29,26,28]
    melody=[[76,74,72,71,72,76,79,76],[76,72,69,72,76,77,76,72],[77,76,74,72,69,72,74,77],[76,74,71,68,71,74,76,80]]
    for bar in range(64):
        b=bar*4;c=chords[(bar//2)%4];r=roots[(bar//2)%4];section=bar//8
        s.chord(b,c,3.9,'pad',.024 if section not in [3,7] else .017)
        if section in [0,3,7]:
            for off,note in zip([.0,1.5,2.75],[c[0]+12,c[2]+12,c[3]+12]):s.note('melody',b+off,note,.5,'pluck',.10,pan=.3)
        else:
            for i in range(8):s.note('bass',b+i*.5,r+(12 if i in [3,6] else 0),.31,'bass',.23)
            if section in [2,4,6]:
                for i,note in enumerate(melody[(bar//2)%4]):s.note('melody',b+i*.5,note,.31,'lead',.057,pan=.08)
            elif bar%2==0:
                for i,note in enumerate([c[2]+12,c[3]+12,c[1]+12]):s.note('melody',b+.5+i, note,.7,'lead',.063,pan=.08)
        if section not in [3,7] or bar%8>=4:
            kicks=[0,2] if section==0 else [0,1.5,2,2.75]
            for off in kicks:s.hit(b+off,'kick',.29)
            for off in [1,3]:s.hit(b+off,'snare',.21);s.hit(b+off+.012,'clap',.08)
            for i in range(8):s.hit(b+i*.5,'hat',.09 if i%2 else .06)
            if section in [2,4,6]:s.hit(b+3.5,'openhat',.10)
            if bar%8==7:
                for off in [3.25,3.5,3.75]:s.hit(b+off,'snare',.08)
    return s.finish('m03-ringroad-pursuit',duck=.24,space=.09,delay=.14)

def daytime():
    s=Song(108,48)
    chords=[[53,57,60,64],[50,57,60,65],[55,59,62,65],[48,55,58,64]]
    roots=[29,26,31,24]
    for bar in range(48):
        b=bar*4;c=chords[bar%4];r=roots[bar%4];section=bar//8
        for off in [.5,1.5,2.5,3.5]:s.chord(b+off,c,.22,'pluck',.10)
        if section!=3:s.chord(b+.75,[n+12 for n in c[1:]],1.5,'ep',.024)
        for off,interval in [(0,0),(.75,12),(1.75,7),(2.5,0),(3.5,10)]:s.note('bass',b+off,r+interval,.35,'bass',.24)
        if section not in [0,3] or bar%8>=4:
            for off in [0,1.75,2.5]:s.hit(b+off,'kick',.24)
            for off in [1,3]:s.hit(b+off,'rim' if section==3 else 'snare',.16)
            for i in range(8):s.hit(b+i*.5+(.022 if i%2 else 0),'shaker',.19 if i%2 else .10)
        if section in [1,2,4] and bar%2==0:
            motif=[c[2]+12,c[3]+12,c[1]+12,c[0]+12,c[2]+12]
            for off,note in zip([.25,1.0,2.5,3.25,5.5],motif):s.note('melody',b+off,note,.7,'ep',.09,pan=-.18,human=True)
    return s.finish('m04-market-day',duck=.10,space=.13,delay=.18)

functions={'river':river,'harbour':harbour,'pursuit':pursuit,'daytime':daytime}
selected=sys.argv[1:] or list(functions)
rows=[]
for name in selected:
    rows.append(functions[name]())
write_manifest('music',rows)
