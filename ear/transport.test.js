/* Transport (start / pause / resume), the log panel, and interim answers.
 *
 *   cd ear && python3 -m http.server 8811 &
 *   npm i playwright && node transport.test.js
 */
const { chromium } = require('playwright');
const STUB = () => {
  window.__spoken=[]; window.__recs=[];
  class FakeRec {
    constructor(){ this.running=false; window.__recs.push(this); }
    start(){ if(this.running) throw new Error('started'); this.running=true; this.onstart&&this.onstart(); }
    stop(){ if(!this.running) return; this.running=false; this.onend&&this.onend(); }
    abort(){ this.stop(); }
    feed(text, isFinal=true){
      if(!this.running) return false;
      const res=[{transcript:text,confidence:0.9}]; res.isFinal=isFinal; res.length=1;
      this.onresult&&this.onresult({results:{length:1,0:res}}); return true;
    }
  }
  window.SpeechRecognition=FakeRec; window.webkitSpeechRecognition=FakeRec;
  Object.defineProperty(window,'speechSynthesis',{configurable:true,value:{
    speak(u){ window.__spoken.push(u.text); setTimeout(()=>u.onend&&u.onend(),10); },
    cancel(){}, getVoices(){return[];}, onvoiceschanged:null }});
  window.SpeechSynthesisUtterance=function(t){this.text=t;};
};
const latest = () => window.__recs[window.__recs.length-1];

(async () => {
  const b = await chromium.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    args:['--no-sandbox','--autoplay-policy=no-user-gesture-required']});
  const pg = await b.newPage({viewport:{width:420,height:900}});
  await pg.addInitScript(STUB);
  await pg.goto('http://127.0.0.1:8811/index.html',{waitUntil:'domcontentloaded'});
  const fails=[];

  const label = () => pg.textContent('#start');
  console.log('button at rest:', await label());
  if (await label() !== 'Start') fails.push('initial label');

  await pg.click('#start'); await pg.waitForTimeout(1600);
  console.log('button while running:', await label());
  if (await label() !== 'Pause') fails.push('should read Pause while running');

  const logged = await pg.evaluate(() => document.querySelectorAll('#log li').length);
  console.log('log entries after start:', logged);
  if (logged < 3) fails.push('log not populating');

  // pause
  await pg.click('#start'); await pg.waitForTimeout(300);
  console.log('button while paused:', await label());
  const noteShown = await pg.evaluate(() => !document.getElementById('pausedNote').hidden);
  console.log('paused note visible:', noteShown);
  if (await label() !== 'Resume') fails.push('should read Resume while paused');
  if (!noteShown) fails.push('paused note hidden');

  // paused must not keep asking questions
  const q1 = await pg.evaluate(() => logLines.filter(l=>l.includes('play ')).length);
  await pg.waitForTimeout(2500);
  const q2 = await pg.evaluate(() => logLines.filter(l=>l.includes('play ')).length);
  console.log(`questions played while paused: ${q2-q1} (want 0)`);
  if (q2 !== q1) fails.push('still drilling while paused');

  // an answer while paused must be ignored, a command must not
  const micLive = await pg.evaluate(() => { const r=latestRec(); return !!(r&&r.running); },
    ).catch(()=>null);
  await pg.evaluate(() => { window.latestRec = () => window.__recs[window.__recs.length-1]; });
  const stillListening = await pg.evaluate(() => { const r=window.__recs[window.__recs.length-1]; return !!(r&&r.running); });
  console.log('microphone still open while paused:', stillListening, '(want true)');
  if (!stillListening) fails.push('cannot say resume: mic closed while paused');

  await pg.evaluate(() => window.__recs[window.__recs.length-1].feed('minor second'));
  await pg.waitForTimeout(400);
  const scored = await pg.evaluate(() => State.stats.total);
  console.log('answers scored while paused:', scored, '(want 0)');
  if (scored !== 0) fails.push('scored an answer while paused');

  // resume by voice
  await pg.evaluate(() => window.__recs[window.__recs.length-1].feed('resume'));
  await pg.waitForTimeout(1200);
  console.log('button after voice resume:', await label());
  if (await label() !== 'Pause') fails.push('voice resume did not resume');

  // interim results should be acted on without waiting for the final
  await pg.waitForTimeout(800);
  await pg.evaluate(() => { State.phase='listening'; State.listenSince=performance.now(); });
  const t0 = Date.now();
  await pg.evaluate(() => window.__recs[window.__recs.length-1].feed('minor second', false));
  await pg.waitForTimeout(300);
  const tookInterim = await pg.evaluate(() => logLines.some(l => l.includes('took interim answer')));
  console.log('acted on interim result:', tookInterim, '(want true)');
  if (!tookInterim) fails.push('interim answers not used — adds a second of lag');

  const copyable = await pg.evaluate(() => logLines.length > 0);
  if (!copyable) fails.push('nothing to copy');

  // The kind label is an inline-block, so it inherits the row's hanging
  // indent and vanishes unless that is reset. It also must not pick up the
  // microphone indicator's .mic rule.
  const lbl = await pg.evaluate(() => {
    const k = document.querySelector('#log li .k');
    const cs = getComputedStyle(k);
    return { text: k.textContent.trim(), indent: cs.textIndent,
             transform: cs.textTransform, width: k.getBoundingClientRect().width };
  });
  console.log('log label:', JSON.stringify(lbl));
  if (!lbl.text) fails.push('log label empty');
  if (lbl.indent !== '0px') fails.push('label inherits hanging indent — renders blank');
  if (lbl.transform === 'uppercase') fails.push('label picked up the .mic indicator rule');
  if (lbl.width < 10) fails.push('label has no width');

  await b.close();
  console.log(fails.length ? '\nPROBLEMS:\n'+fails.join('\n') : '\ntransport + log ok');
  process.exit(fails.length?1:0);
})();
