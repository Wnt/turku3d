// Original perceptual/modal tyre prototype, CC0-1.0. Not a calibrated physics solver.
// Shared by the offline candidate renders and the soundboard AudioWorklet.
export class TyreModel {
  constructor(rate=48000,seed=2601110) {
    this.rate=rate;this.seed=seed;this.phases=[0,0,0,0];this.flutter=0;
    this.low=0;this.mid=0;this.grit=0;this.wander=0;this.level=0;
    this.params={slip:0.3,load:1,speed:50,surface:0};this.current={...this.params};
  }
  noise(){this.seed=(Math.imul(this.seed,1664525)+1013904223)>>>0;return this.seed/2147483648-1;}
  render(out){
    const sr=this.rate,c=this.current,p=this.params,tau=2*Math.PI;
    const smooth=1-Math.exp(-1/(sr*.035));
    for(let n=0;n<out.length;n++){
      for(const k of ['slip','load','speed','surface'])c[k]+=(p[k]-c[k])*smooth;
      const slip=Math.max(0,Math.min(1,c.slip)),load=Math.max(0,c.load),speed=Math.max(0,c.speed);
      const dry=1-Math.min(1,c.surface),wet=Math.max(0,1-Math.abs(c.surface-1)),gravel=Math.max(0,c.surface-1);
      const onset=Math.min(1,Math.max(0,(slip-.055)/.28));
      const motion=Math.min(1,speed/14),energy=onset*Math.sqrt(load)*motion;
      // Local vibration modes change modestly; no implausible siren from speed alone.
      const fundamental=(690+180*Math.sqrt(slip)+50*(load-1))*(1+.06*wet);
      const white=this.noise();this.low+=(white-this.low)*.025;this.mid+=(white-this.mid)*.22;
      this.wander+=(this.noise()-this.wander)*.00025;
      this.grit+=(this.noise()-this.grit)*.004;
      this.flutter+=(17+22*slip+7*this.wander)/sr;
      let tone=0;
      const modes=[1,1.84,2.67,3.53],weights=[.48,.28,.16,.07];
      for(let i=0;i<4;i++){
        const jitter=1+.018*Math.sin(tau*this.flutter*(.61+i*.127)+i)+.08*this.wander;
        this.phases[i]+=(fundamental*modes[i]*jitter)/sr;
        this.phases[i]-=Math.floor(this.phases[i]);
        const roughness=.10+.18*slip;
        tone+=weights[i]*Math.sin(tau*this.phases[i]+roughness*Math.sin(tau*this.phases[(i+1)%4]));
      }
      const flutter=.79+.11*Math.sin(tau*this.flutter)+.1*Math.sin(tau*this.flutter*.71+2);
      const scrape=(this.mid-this.low)*2.0;
      const noiseMix=.22+.42*slip+.3*wet+.65*gravel;
      const toneMix=(.85-.23*slip)*dry+.20*wet;
      const slide=energy*(tone*toneMix*flutter+scrape*noiseMix*(.9+this.grit));
      const rolling=(this.low*.20+(white-this.mid)*.016)*Math.min(1,speed/100)*Math.sqrt(load);
      out[n]=Math.tanh((slide+rolling)*.7)*.22;
    }
  }
}
