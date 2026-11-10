"""A sample-derived alternative to the modal prototype; original code CC0."""
from audio_common import *

source=filt(decode(WORK/'inputs/2368.mp3'),260,'highpass')
seconds=12;n=round(seconds*RATE);out=np.zeros(n);weights=np.zeros(n)
length=round(.19*RATE);window=np.hanning(length)
for at in np.arange(0,seconds,.071):
    # Do not replay the recognisable original attack. Use only the sustained part.
    start=round(RNG.uniform(.35,1.45)*RATE)
    grain=source[start:start+length]*window
    add(out,grain,at,wrap=True);add(weights,window**2,at,wrap=True)
out/=np.sqrt(np.maximum(weights,.001))
t=np.arange(n)/RATE
out*=.80+.11*np.sin(2*np.pi*t/seconds*3)+.09*np.sin(2*np.pi*t/seconds*7)
row=save('t09-granular-drift',out,loop=True,target=-22)
rows=json.loads((WORK/'work/effects.json').read_text());rows=[r for r in rows if r['id']!=row['id']];rows.append(row);write_manifest('effects',rows)
print(row)
