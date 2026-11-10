// Standalone soundboard, no external requests, storage remains local to this browser.
const items=Object.fromEntries(CATALOGUE.map(item=>[item.id,item]));
const storageKey='turku3d-sounds-astra-selection-v1';
let chosen=new Set();
try{const saved=JSON.parse(localStorage.getItem(storageKey)||'[]');if(Array.isArray(saved))chosen=new Set(saved.filter(id=>items[id]||id==='t08-live'));}catch{}
let context,master,voice,activeId=null,startTime=0,startOffset=0,generation=0,workletReady=false,liveNode=null;
const cache=new Map();
const status=document.querySelector('#status');
function setStatus(message){status.textContent=message;}
function refreshSelection(){
 document.querySelectorAll('[data-pick]').forEach(input=>{input.checked=chosen.has(input.dataset.pick);input.closest('.card')?.classList.toggle('picked',input.checked);});
 document.querySelector('#selectedCount').textContent=`${chosen.size} valittua`;
 document.querySelector('#selectionHint').textContent=chosen.size?'Valinnat tallentuvat tähän selaimeen.':'Valitse kiinnostavat äänet kuuntelun aikana.';
 try{localStorage.setItem(storageKey,JSON.stringify([...chosen]));}catch{document.querySelector('#selectionHint').textContent='Selain estää tallennuksen. Kopioi valinnat ennen sulkemista.';}
}
document.querySelectorAll('[data-pick]').forEach(input=>input.addEventListener('change',()=>{input.checked?chosen.add(input.dataset.pick):chosen.delete(input.dataset.pick);refreshSelection();}));
refreshSelection();
document.querySelector('#showSelected').onclick=event=>{const active=document.body.classList.toggle('selected-only');event.currentTarget.textContent=active?'Näytä kaikki':'Näytä valitut';event.currentTarget.setAttribute('aria-pressed',String(active));};
document.querySelector('#copy').onclick=async()=>{
 const text=[...chosen].sort().join('\n');
 if(!text){setStatus('Valitse ensin vähintään yksi ääni.');return;}
 try{await navigator.clipboard.writeText(text);document.querySelector('#copy').textContent='Kopioitu ✓';setTimeout(()=>document.querySelector('#copy').textContent='Kopioi valinnat',1800);}
 catch{const box=document.querySelector('#copyFallback');box.hidden=false;const area=box.querySelector('textarea');area.value=text;area.focus();area.select();setStatus('Kopioi alla oleva valittu teksti näppäimistöllä.');box.scrollIntoView({block:'center'});}
};
function audioContext(){
 if(!context){context=new AudioContext();master=context.createGain();master.gain.value=Number(document.querySelector('#volume').value)/100;master.connect(context.destination);}
 void context.resume();return context;
}
document.querySelector('#volume').oninput=event=>{const value=Number(event.target.value)/100;document.querySelector('#volumeLabel').textContent=`${Math.round(value*100)} %`;if(master)master.gain.setTargetAtTime(value,context.currentTime,.02);};
function resetButtons(){document.querySelectorAll('.card').forEach(card=>{card.classList.remove('playing');const button=card.querySelector('.play');if(button){button.textContent='▶';button.setAttribute('aria-label',`Toista: ${items[card.dataset.id]?.title||''}`);}});document.querySelector('#liveStart').textContent='Käynnistä rengasmalli';}
function stop(invalidate=true){
 if(invalidate)generation++;
 if(voice){const old=voice;old.gain.gain.cancelScheduledValues(context.currentTime);old.gain.gain.setTargetAtTime(0,context.currentTime,.008);setTimeout(()=>{try{old.source.stop();}catch{}old.source.disconnect();old.gain.disconnect();},50);voice=null;}
 if(liveNode){liveNode.disconnect();liveNode.port.postMessage({stop:true});liveNode=null;}
 activeId=null;resetButtons();
}
document.querySelector('#stop').onclick=()=>{stop();setStatus('Toisto pysäytetty.');};
async function loadBuffer(item){
 if(cache.has(item.id)){const b=cache.get(item.id);cache.delete(item.id);cache.set(item.id,b);return b;}
 const response=await fetch(item.file);if(!response.ok)throw new Error(`Äänitiedoston lataus epäonnistui (${response.status}).`);
 const buffer=await context.decodeAudioData(await response.arrayBuffer());
 cache.set(item.id,buffer);
 let bytes=[...cache.values()].reduce((sum,b)=>sum+b.length*b.numberOfChannels*4,0);
 for(const [id,b]of cache){if(bytes<=110*1024*1024)break;if(id!==item.id&&id!==activeId){cache.delete(id);bytes-=b.length*b.numberOfChannels*4;}}
 return buffer;
}
async function play(id,offset=0){
 const item=items[id];if(!item)return;
 audioContext();stop();const token=++generation;setStatus(`Ladataan: ${item.title}…`);
 try{
  const buffer=await loadBuffer(item);if(token!==generation)return;
  const source=context.createBufferSource(),gain=context.createGain();source.buffer=buffer;source.loop=item.loop;
  gain.gain.setValueAtTime(0,context.currentTime);gain.gain.linearRampToValueAtTime(item.gain||1,context.currentTime+.012);
  source.connect(gain);gain.connect(master);startOffset=Math.max(0,Math.min(offset,buffer.duration-.01));startTime=context.currentTime;
  activeId=id;voice={source,gain};source.start(0,startOffset);
  const card=document.querySelector(`.card[data-id="${id}"]`);card.classList.add('playing');card.querySelector('.play').textContent='Ⅱ';card.querySelector('.play').setAttribute('aria-label',`Pysäytä: ${item.title}`);
  source.onended=()=>{if(voice?.source===source){voice=null;activeId=null;source.disconnect();gain.disconnect();resetButtons();setStatus('Valmis.');}};
  setStatus(`${item.title}${item.loop?' · saumaton toisto':''}`);
 }catch(error){if(token===generation){stop();setStatus(error.message);}}
}
document.querySelectorAll('[data-play]').forEach(button=>button.onclick=()=>{const id=button.dataset.play;if(id===activeId){stop();setStatus('Toisto pysäytetty.');}else void play(id);});
document.querySelectorAll('[data-seam]').forEach(button=>button.onclick=()=>{const id=button.dataset.seam;void play(id,Math.max(0,items[id].seconds-1.5));});
document.querySelectorAll('[data-seek]').forEach(input=>input.addEventListener('change',()=>void play(input.dataset.seek,Number(input.value))));
function timeLabel(seconds){return `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;}
setInterval(()=>{
 if(!activeId||activeId==='t08-live'||!context)return;
 const item=items[activeId],duration=voice?.source.buffer.duration||item.seconds;
 const elapsed=context.currentTime-startTime+startOffset,position=item.loop?elapsed%duration:Math.min(elapsed,duration);
 const card=document.querySelector(`.card[data-id="${activeId}"]`);if(document.activeElement!==card.querySelector('.seek'))card.querySelector('.seek').value=position;
 card.querySelector('.elapsed').textContent=timeLabel(position);
},150);
function liveParameters(){return {slip:Number(document.querySelector('#slip').value)/100,load:Number(document.querySelector('#load').value)/100,speed:Number(document.querySelector('#speed').value),surface:Number(document.querySelector('#surface').value)};}
function updateLive(){const p=liveParameters();document.querySelector('#slipValue').textContent=`${Math.round(p.slip*100)} %`;document.querySelector('#loadValue').textContent=p.load.toFixed(2).replace('.',',');document.querySelector('#speedValue').textContent=`${p.speed} km/h`;if(liveNode)liveNode.port.postMessage(p);}
for(const id of ['slip','load','speed','surface'])document.querySelector(`#${id}`).oninput=updateLive;
document.querySelectorAll('[data-preset]').forEach(button=>button.onclick=()=>{const presets={grip:[0,100,55,0],light:[22,70,45,0],drift:[67,135,70,0],gravel:[55,100,55,2]};presets[button.dataset.preset].forEach((value,i)=>document.querySelector(`#${['slip','load','speed','surface'][i]}`).value=value);updateLive();});
document.querySelector('#liveStart').onclick=async()=>{
 if(activeId==='t08-live'){stop();setStatus('Rengasmalli pysäytetty.');return;}
 audioContext();stop();const token=++generation;
 try{
  if(!workletReady){
   const body=TYRE_CORE.replace('export class TyreModel','class TyreModel')+`\nclass TyreProcessor extends AudioWorkletProcessor {constructor(){super();this.synth=new TyreModel(sampleRate);this.running=true;this.port.onmessage=e=>{if(e.data.stop)this.running=false;else this.synth.params=e.data;};} process(inputs,outputs){if(!this.running)return false;this.synth.render(outputs[0][0]);return true;}} registerProcessor('astra-tyre',TyreProcessor);`;
   const url=URL.createObjectURL(new Blob([body],{type:'application/javascript'}));try{await context.audioWorklet.addModule(url);workletReady=true;}finally{URL.revokeObjectURL(url);}
  }
  if(token!==generation)return;
  liveNode=new AudioWorkletNode(context,'astra-tyre',{numberOfInputs:0,numberOfOutputs:1,outputChannelCount:[1]});liveNode.connect(master);activeId='t08-live';updateLive();document.querySelector('#liveStart').textContent='Pysäytä rengasmalli';setStatus('Rengasmalli soi · säädä luistoa, kuormaa ja pintaa.');
 }catch(error){stop();setStatus(`Rengasmallin käynnistys epäonnistui: ${error.message}`);}
};
document.addEventListener('keydown',event=>{if(event.key==='Escape'){stop();setStatus('Toisto pysäytetty.');}});
window.addEventListener('pagehide',()=>{stop();context?.close();});
updateLive();
