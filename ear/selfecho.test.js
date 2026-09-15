/* Regression harness for the self-echo loop.
 *
 * The app used to announce the lesson, and a lesson name is a navigation
 * command. On a phone the speaker reaches the microphone, so it heard itself,
 * jumped, re-announced and looped: 85 utterances in six seconds.
 *
 * The announcement is gone, so this provokes speech with "help" instead — the
 * sharpest case available, since that sentence contains repeat, skip, score
 * and pause. The stubs close the loop: the fake synthesiser feeds whatever it
 * says back into the fake recogniser.
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
  await pg.waitForTimeout(1500);

  // Nothing is announced on start any more, so provoke speech instead. "help"
  // is the sharpest case there is: the sentence it speaks literally contains
  // repeat, skip, score and pause, so an app that hears itself would fire all
  // four and never stop.
  await pg.evaluate(() => window.__recs[window.__recs.length-1].feed('help'));
  await pg.waitForTimeout(150);

  const openWhileSpeaking = await pg.evaluate(live);
  console.log('recogniser running while speaking:', openWhileSpeaking, '(want false)');
  if (openWhileSpeaking) fails.push('mic open while the app speaks');

  await pg.waitForTimeout(1500);
  const prompt = await pg.evaluate(() => window.__spoken[window.__spoken.length-1]);
  console.log('prompt spoken:', JSON.stringify(prompt));
  if (!prompt || !/repeat/.test(prompt)) fails.push('help prompt not spoken');

  // The loop is self-sustaining if the bug is present: nothing more is
  // injected, the app simply hears whatever it says.
  const snap = () => pg.evaluate(() => ({
    n: State.lesson.n,
    said: window.__spoken.length,
    // The real harm is the app obeying its own words: "repeat" replays,
    // "skip" scores a miss. Neither necessarily speaks, so count them.
    replays: State.current ? State.current.replays : 0,
    scored: State.stats.total
  }));
  const before = await snap();
  await pg.waitForTimeout(6000);
  const after = await snap();
  console.log(`commands obeyed from our own speech: replays +${after.replays-before.replays}, scored +${after.scored-before.scored} (want 0, 0)`);
  if (after.replays > before.replays) fails.push('obeyed "repeat" from its own prompt');
  if (after.scored > before.scored) fails.push('obeyed "skip" from its own prompt');

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
