/* Regression harness for the self-echo loop.
 *
 * The app's spoken prompts name the lesson ("Lesson 1. Harmonic, Seconds."),
 * and a lesson name is a navigation command. On a phone the speaker reaches
 * the microphone, so the app heard itself, jumped, re-announced, and looped:
 * 85 utterances in 6 seconds.
 *
 * The stubs below close that same loop — the fake synthesiser feeds whatever
 * it says back into the fake recogniser — so the bug reproduces here if the
 * mute gate is ever removed.
 *
 *   cd ear && python3 -m http.server 8811 &
 *   npm i playwright && node selfecho.test.js
 */
const { chromium } = require('playwright');

const STUB = () => {
  window.__spoken = [];
  window.__recs = [];
  class FakeRec {
    constructor(){ this.continuous=false; this.interimResults=false; this.maxAlternatives=1;
                   this.lang='en-US'; this.running=false; window.__recs.push(this); }
    start(){ if(this.running) throw new Error('already started');
             this.running=true; this.onstart && this.onstart(); }
    stop(){ if(!this.running) return; this.running=false; this.onend && this.onend(); }
    abort(){ this.stop(); }
    // Pretend the microphone picked this up.
    feed(text){
      if(!this.running) return false;            // muted: the mic hears nothing
      const res=[{transcript:text,confidence:0.9}];
      res.isFinal=true; res.length=1;
      this.onresult && this.onresult({results:{length:1,0:res,[0]:res}});
      return true;
    }
  }
  window.SpeechRecognition = FakeRec;
  window.webkitSpeechRecognition = FakeRec;

  // window.speechSynthesis is read-only; plain assignment is silently dropped.
  // The speaker is heard by the microphone — the phone's actual behaviour,
  // and the thing that turned one prompt into an endless loop.
  Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
    speak(u){
      window.__spoken.push(u.text);
      setTimeout(() => {
        u.onend && u.onend();
        setTimeout(() => {
          const r = window.__recs[window.__recs.length - 1];
          if (r) r.feed(u.text);
        }, 60);
      }, 10);
    },
    cancel(){}, getVoices(){ return []; }, onvoiceschanged:null
  }});
  window.SpeechSynthesisUtterance = function(t){ this.text=t; };
};

const live = () => { const r = window.__recs[window.__recs.length-1]; return r && r.running; };

(async () => {
  const b = await chromium.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    args:['--no-sandbox','--autoplay-policy=no-user-gesture-required']});
  const pg = await b.newPage();
  await pg.addInitScript(STUB);
  await pg.goto('http://127.0.0.1:8811/index.html', {waitUntil:'domcontentloaded'});
  const fails = [];

  await pg.click('#start');
  await pg.waitForTimeout(120);

  // While the app is talking, the microphone must be shut.
  const openWhileSpeaking = await pg.evaluate(live);
  console.log('recogniser running while speaking:', openWhileSpeaking, '(want false)');
  if (openWhileSpeaking) fails.push('mic open while the app speaks');

  await pg.waitForTimeout(1200);           // gate reopens ~700ms after onend
  const prompt = await pg.evaluate(() => window.__spoken[0]);
  console.log('first prompt spoken:', JSON.stringify(prompt));

  // The loop is now self-sustaining if the bug is present: nothing more is
  // injected, the app simply hears whatever it says.
  const before = await pg.evaluate(() => ({ n: State.lesson.n, said: window.__spoken.length }));
  await pg.waitForTimeout(6000);
  const after = await pg.evaluate(() => ({ n: State.lesson.n, said: window.__spoken.length }));

  console.log(`lesson ${before.n} -> ${after.n}  (want unchanged)`);
  console.log(`utterances over 6s: ${before.said} -> ${after.said}  (want no growth)`);
  if (after.n !== before.n) fails.push('own prompt changed the lesson');
  if (after.said - before.said > 2) fails.push('RUNAWAY: '+(after.said-before.said)+' utterances in 6s');

  // Echoing a stale prompt much later must also not steer the app.
  await pg.evaluate(() => { State.lesson = LESSONS.find(l => l.n === 3); });
  await pg.evaluate((t) => {
    const r = window.__recs[window.__recs.length - 1];
    r.feed(t);
  }, 'lesson 1 seconds lesson 1 seconds');
  await pg.waitForTimeout(800);
  const drifted = await pg.evaluate(() => State.lesson.n);
  console.log('a genuine spoken "lesson 1" still navigates:', drifted, '(want 1)');
  if (drifted !== 1) fails.push('real navigation broke');

  await b.close();
  console.log(fails.length ? '\nPROBLEMS:\n'+fails.join('\n') : '\nno self-echo loop');
  process.exit(fails.length?1:0);
})();
