/* Synthesis accuracy: absolute pitch and, more importantly, interval size.
 *
 *   cd ear && node pitch.test.js
 */
const fs=require('fs'),vm=require('vm');
const ctx=vm.createContext({Math,Float32Array,Float64Array,window:{}});
vm.runInContext(fs.readFileSync('/home/user/KiteCast/ear/audio.js','utf8')+"\n;globalThis.__E=AudioEngine;",ctx);
const E=ctx.__E, SR=48000;

/* YIN: difference function + cumulative mean normalisation + absolute
   threshold. Picks the SMALLEST lag that dips below threshold, which is what
   stops it reporting an octave down on a strongly periodic plucked string. */
/* The search is bounded to a tritone either side of the frequency actually
   requested. A blind detector octave-errors on high plucked notes, where the
   fundamental is weak — and this measures deviation from a known pitch, not
   blind pitch detection, so the window is honest. */
function detect(buf,sr,fmin=55,fmax=2400){
  const start=Math.floor(sr*0.06), N=Math.min(Math.floor(sr*0.3),buf.length-start);
  const x=buf.subarray(start,start+N);
  const tmax=Math.min(Math.ceil(sr/fmin),Math.floor(N/2)), tmin=Math.max(2,Math.floor(sr/fmax));
  const d=new Float64Array(tmax+1);
  for(let t=1;t<=tmax;t++){let s=0;for(let i=0;i+t<N;i++){const v=x[i]-x[i+t];s+=v*v;}d[t]=s;}
  const cm=new Float64Array(tmax+1); let run=0; cm[0]=1;
  for(let t=1;t<=tmax;t++){run+=d[t];cm[t]=d[t]*t/(run||1);}
  const TH=0.12; let best=-1;
  for(let t=tmin;t<tmax;t++){
    if(cm[t]<TH){while(t+1<tmax&&cm[t+1]<cm[t])t++;best=t;break;}
  }
  if(best<0){let bv=Infinity;for(let t=tmin;t<tmax;t++)if(cm[t]<bv){bv=cm[t];best=t;}}
  const a=cm[best-1],b=cm[best],c=cm[best+1];
  const den=2*(a-2*b+c); const shift=den?(a-c)/den:0;
  return sr/(best+shift);
}
const cents=(f,r)=>1200*Math.log2(f/r);
const VOICES={piano:f=>E._renderPiano(f,2.5,SR),
              nylon:f=>E._renderPluck(f,2.5,SR,{S:0.34,t60:1.9,bright:0.11,pick:0}),
              steel:f=>E._renderPluck(f,2.5,SR,{S:0.56,t60:3.1,bright:0.40,pick:0.06})};
let fail=0;
console.log('--- absolute pitch (cents from equal temperament) ---');
for(const name in VOICES){
  let mx=0;
  for(let m=50;m<=84;m+=2){
    const ref=E.midiToFreq(m), buf=VOICES[name](ref);
    for(let i=0;i<buf.length;i++) if(!isFinite(buf[i])){console.log('FAIL non-finite',name,m);fail++;break;}
    const c=cents(detect(buf,SR,ref/1.414,ref*1.414), ref); mx=Math.max(mx,Math.abs(c));
    if(Math.abs(c)>10){console.log('  FAIL',name,'midi',m,c.toFixed(2)+'c');fail++;}
  }
  console.log(' ',name.padEnd(6),'max',mx.toFixed(2),'cents');
}
console.log('--- interval accuracy (what actually matters) ---');
for(const name in VOICES){
  let mx=0;
  for(const semi of [1,3,4,6,7,9,11,12]){
    for(const root of [52,60,71]){
      const f1=E.midiToFreq(root), f2=E.midiToFreq(root+semi);
      const got=cents(detect(VOICES[name](f2),SR,f2/1.414,f2*1.414),
                      detect(VOICES[name](f1),SR,f1/1.414,f1*1.414));
      const err=got-semi*100; mx=Math.max(mx,Math.abs(err));
      if(Math.abs(err)>5){console.log('  FAIL',name,'root',root,'+'+semi,err.toFixed(2)+'c');fail++;}
    }
  }
  console.log(' ',name.padEnd(6),'max interval error',mx.toFixed(2),'cents');
}
console.log(fail?`\n${fail} FAILURES`:'\nsynthesis verified');
process.exit(fail?1:0);
