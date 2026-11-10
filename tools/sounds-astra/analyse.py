"""Measure decoded delivered files, not just the pre-encode buffers."""
from audio_common import *
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def metrics(path):
    raw=decode(path,channels=0)
    x=raw.mean(axis=1) if raw.ndim==2 else raw
    f,p=signal.welch(x,fs=RATE,nperseg=4096)
    centroid=float(np.sum(f*p)/max(np.sum(p),1e-20))
    frame=480;n=len(x)//frame
    rms=np.sqrt(np.mean(x[:n*frame].reshape(n,frame)**2,axis=1))
    threshold=max(1e-6,float(rms.max())*.1)
    diffs=np.abs(np.diff(x));jump=float(abs(x[0]-x[-1]));p99=float(np.quantile(diffs[::4],.999))
    active=active_rms(x)
    peaks,_=signal.find_peaks(p,distance=8)
    peaks=peaks[np.argsort(p[peaks])[-5:][::-1]]
    return dict(seconds=round(len(x)/RATE,5),peak_dbfs=round(20*np.log10(max(abs(raw).max(),1e-12)),2),
                active_rms_dbfs=round(20*np.log10(max(active,1e-12)),2),centroid_hz=round(centroid),
                energy_over_3khz_pct=round(float(p[f>3000].sum()/p.sum()*100),2),
                active_pct=round(float(np.mean(rms>threshold))*100,1),
                peak_bins_hz=[round(float(f[i])) for i in peaks],
                seam_jump_dbfs=round(20*np.log10(max(jump,1e-12)),2),seam_vs_p999=round(jump/max(p99,1e-12),4),
                first_20ms_rms_dbfs=round(20*np.log10(max(np.sqrt(np.mean(x[:960]**2)),1e-12)),2),
                last_20ms_rms_dbfs=round(20*np.log10(max(np.sqrt(np.mean(x[-960:]**2)),1e-12)),2))

rows={p.stem:metrics(p) for p in sorted((BOARD/'audio').glob('*.ogg'))}
for id,row in rows.items():
    if id.startswith('m0') or id.startswith('current-music'):
        measured=subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',str(BOARD/'audio'/f'{id}.ogg'),'-af','ebur128=peak=true','-f','null','-'],capture_output=True,text=True,check=True)
        values=re.findall(r'I:\s+(-?[\d.]+) LUFS',measured.stderr)
        row['integrated_lufs']=float(values[-1])
(WORK/'evidence/measurements.json').write_text(json.dumps(rows,indent=2,default=float))
(BOARD/'measurements.json').write_text(json.dumps(rows,indent=2,default=float))

plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
comparisons=[('gulls','current-gulls','g02-herring-series','Nykyinen lokki','Äänitetty harmaalokki'),
             ('tyres','current-screech','t01-recorded-chirp','Nykyinen rengasääni','Äänitetty renkaan kirskahdus'),
             ('music','current-music-aura-neon','m01-river-afterhours','Nykyinen Aura Neon','Uusi: Aurajoen jälkeen')]
for name,a,b,an,bn in comparisons:
    pairs=[(a,an),(b,bn)]
    if name=='music' and 'm05-auransilta-fm' in rows:pairs.append(('m05-auransilta-fm','Paikallinen malli: Auransilta FM'))
    fig,axs=plt.subplots(len(pairs),3,figsize=(13,2.6*len(pairs)),gridspec_kw={'width_ratios':[1.2,1.2,1]})
    fig.patch.set_facecolor('#f5f3e9')
    for i,(id,label) in enumerate(pairs):
        x=decode(BOARD/'audio'/f'{id}.ogg');limit=min(len(x),round((20 if name=='music' else 8)*RATE));clip=x[:limit]
        t=np.arange(limit)/RATE
        axs[i,0].plot(t[::120],clip[::120],lw=.5,color='#18635a');axs[i,0].set_title(label);axs[i,0].set_xlabel('Aika (s)');axs[i,0].set_ylabel('Amplitudi')
        f,tt,z=signal.stft(clip,fs=RATE,nperseg=2048,noverlap=1536)
        axs[i,1].pcolormesh(tt,f,20*np.log10(np.maximum(abs(z),1e-7)),vmin=-75,vmax=-20,cmap='magma',shading='auto');axs[i,1].set_ylim(0,6000);axs[i,1].set_xlabel('Aika (s)');axs[i,1].set_ylabel('Taajuus (Hz)')
        f,p=signal.welch(x,fs=RATE,nperseg=4096)
        axs[i,2].plot(f,10*np.log10(np.maximum(p,1e-15)),color='#bf6030',lw=1);axs[i,2].set_xlim(0,8000);axs[i,2].set_ylim(-115,-35);axs[i,2].set_xlabel('Taajuus (Hz)');axs[i,2].set_ylabel('Tehotiheys (dB/Hz)')
    fig.tight_layout();fig.savefig(BOARD/f'analysis-{name}.png',dpi=125,facecolor=fig.get_facecolor());plt.close(fig)

# Useful assertions: decode success, sensible peaks, exact song lengths, no huge loop jumps.
loops=[r for name in ['effects','music'] for r in json.loads((WORK/'work'/f'{name}.json').read_text()) if r['loop'] and not r['id'].startswith('current-')]
for row in loops:
    m=rows[row['id']]
    assert m['peak_dbfs'] < -.5, (row['id'],'peak',m)
    assert m['seam_vs_p999'] < 1.5, (row['id'],'seam',m)
assert sum(p.stat().st_size for p in BOARD.rglob('*') if p.is_file())<100_000_000
for id,row in rows.items():
    assert row['peak_dbfs']<0,(id,'decoded clipping')
    if id.startswith('m0'):assert 60<=row['seconds']<=180,(id,'music length')
print(json.dumps({id:rows[id] for id in ['current-gulls','g02-herring-series','current-screech','t01-recorded-chirp','current-music-aura-neon','m01-river-afterhours']},indent=2,default=float))
print('PASS: decoded peaks, candidate loop boundaries, board size',len(rows),'files')
