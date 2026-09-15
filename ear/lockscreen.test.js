/* Screen-off behaviour.
 *
 * Recognition dies when the screen locks — the OS takes the microphone back
 * and no web API gets it returned. Audio survives because the graph is routed
 * through a media element, so a session that goes into a pocket becomes
 * listen mode instead of sitting there deaf.
 *
 *   cd ear && python3 -m http.server 8811 &
 *   npm i playwright && node lockscreen.test.js
 */
const { chromium } = require('playwright');
const STUB = () => {
  window.__spoken=[]; window.__recs=[];
  class FakeRec {
    constructor(){ this.running=false; window.__recs.push(this); }
    start(){ if(this.running) throw new Error('x'); this.running=true; this.onstart&&this.onstart(); }
    stop(){ if(!this.running) return; this.running=false; this.onend&&this.onend(); }
    abort(){ this.stop(); }
    feed(t,f=true){ if(!this.running) return false; const r=[{transcript:t,confidence:.9}];
      r.isFinal=f; r.length=1; this.onresult&&this.onresult({results:{length:1,0:r}}); return true; }
  }
  window.SpeechRecognition=FakeRec; window.webkitSpeechRecognition=FakeRec;
  Object.defineProperty(window,'speechSynthesis',{configurable:true,value:{
    speak(u){window.__spoken.push(u.text);setTimeout(()=>u.onend&&u.onend(),10);},
    cancel(){},getVoices(){return[];},onvoiceschanged:null}});
  window.SpeechSynthesisUtterance=function(t){this.text=t;};
  window.__setVisible = (v) => {
    Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>v});
    Object.defineProperty(document,'hidden',{configurable:true,get:()=>v==='hidden'});
    document.dispatchEvent(new Event('visibilitychange'));
  };
};
(async()=>{
  const b=await chromium.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    args:['--no-sandbox','--autoplay-policy=no-user-gesture-required']});
  const pg=await b.newPage();
  await pg.addInitScript(STUB);
  await pg.goto('http://127.0.0.1:8811/index.html',{waitUntil:'domcontentloaded'});
  const fails=[];

  await pg.click('#start'); await pg.waitForTimeout(1500);

  const route = await pg.evaluate(() => ({
    backgroundCapable: AudioEngine.backgroundCapable(),
    audioEls: document.querySelectorAll('audio').length,
    hasSession: 'mediaSession' in navigator,
    playbackState: navigator.mediaSession ? navigator.mediaSession.playbackState : null,
    title: navigator.mediaSession && navigator.mediaSession.metadata
         ? navigator.mediaSession.metadata.title : null
  }));
  console.log('audio route:', JSON.stringify(route));
  if (!route.backgroundCapable) fails.push('no media-element route — audio will die when backgrounded');
  if (route.audioEls < 1) fails.push('no audio element attached');
  if (route.hasSession && route.playbackState !== 'playing') fails.push('mediaSession not marked playing');
  if (route.hasSession && !route.title) fails.push('no lock-screen metadata');

  // screen off
  const before = await pg.evaluate(() => State.mode);
  await pg.evaluate(() => window.__setVisible('hidden'));
  await pg.waitForTimeout(900);
  const off = await pg.evaluate(() => ({
    mode: State.mode,
    micRunning: (() => { const r=window.__recs[window.__recs.length-1]; return !!(r&&r.running); })(),
    spoken: window.__spoken.length
  }));
  console.log(`screen off: mode ${before} -> ${off.mode} (want listen), mic ${off.micRunning} (want false)`);
  if (off.mode !== 'listen') fails.push('did not fall back to listen mode when the screen went off');
  if (off.micRunning) fails.push('kept retrying the microphone with the screen off');

  // listen mode must keep naming intervals while hidden
  const s1 = await pg.evaluate(() => window.__spoken.length);
  await pg.waitForTimeout(5000);
  const s2 = await pg.evaluate(() => window.__spoken.length);
  console.log(`intervals named while hidden: ${s2-s1} (want > 0)`);
  if (s2 <= s1) fails.push('listen mode stalled with the screen off');

  // screen back on
  await pg.evaluate(() => window.__setVisible('visible'));
  await pg.waitForTimeout(1200);
  const on = await pg.evaluate(() => ({
    mode: State.mode,
    micRunning: (() => { const r=window.__recs[window.__recs.length-1]; return !!(r&&r.running); })()
  }));
  console.log(`screen on: mode -> ${on.mode} (want drill), mic ${on.micRunning} (want true)`);
  if (on.mode !== 'drill') fails.push('did not return to drilling');
  if (!on.micRunning) fails.push('microphone did not come back');

  await b.close();
  console.log(fails.length ? '\nPROBLEMS:\n'+fails.join('\n') : '\nlock-screen behaviour ok');
  process.exit(fails.length?1:0);
})();
