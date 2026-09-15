/* Session controller: speech in, music out, nothing to look at. */

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

const State = {
  lesson: LESSONS[0],
  running: false,
  mode: 'drill',            // 'drill' | 'listen'
  phase: 'idle',            // idle | playing | listening | confirming | feedback
  current: null,
  pending: null,            // {size, options} while awaiting a yes/no
  lastRoot: null,
  speaking: false,
  stats: { correct: 0, total: 0, firstListen: 0, byInterval: {} },
  history: []
};

const el = (id) => document.getElementById(id);

/* ---------- speech out ------------------------------------------------ */
let voice = null;
function pickVoice() {
  const vs = speechSynthesis.getVoices();
  voice = vs.find(v => /en-(GB|US)/.test(v.lang) && /Google|Natural|Samantha|Daniel/i.test(v.name))
       || vs.find(v => v.lang.startsWith('en'))
       || vs[0] || null;
}
if (typeof speechSynthesis !== 'undefined') {
  pickVoice();
  speechSynthesis.onvoiceschanged = pickVoice;
}

function say(text, rate = 1.05, tone) {
  return new Promise(resolve => {
    if (!text) return resolve();
    setStatus(text, tone);
    const u = new SpeechSynthesisUtterance(text);
    if (voice) u.voice = voice;
    u.rate = rate;
    u.pitch = 1.0;
    State.speaking = true;
    u.onend = u.onerror = () => { State.speaking = false; resolve(); };
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  });
}

const wait = (ms) => new Promise(r => setTimeout(r, ms));

/* ---------- speech in -------------------------------------------------- */
let rec = null, recActive = false, wantListening = false;

function initRecognition() {
  if (!SR) return false;
  rec = new SR();
  rec.continuous = true;
  rec.interimResults = true;
  rec.maxAlternatives = 5;
  rec.lang = 'en-US';

  rec.onstart = () => { recActive = true; setMic(true); };

  rec.onend = () => {
    recActive = false;
    setMic(false);
    // Chrome ends the stream on its own schedule; keep it alive.
    if (wantListening) { try { rec.start(); } catch (e) {} }
  };

  rec.onerror = (e) => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      wantListening = false;
      el('mic').classList.add('hidden');
      showFallback();
    }
  };

  rec.onresult = (ev) => {
    const res = ev.results[ev.results.length - 1];
    const alts = [];
    for (let i = 0; i < res.length; i++) alts.push(res[i].transcript);

    if (!res.isFinal) { el('heard').textContent = alts[0] || ''; return; }
    el('heard').textContent = alts[0] || '';
    if (State.speaking) return;      // don't let our own prompts feed back
    handleUtterance(alts);
  };
  return true;
}

function startListening() {
  wantListening = true;
  if (rec && !recActive) { try { rec.start(); } catch (e) {} }
}
function stopListening() {
  wantListening = false;
  if (rec && recActive) { try { rec.stop(); } catch (e) {} }
}

/* ---------- question generation --------------------------------------- */
function pickInterval() {
  const set = State.lesson.set;
  // Weight toward intervals this session has been getting wrong.
  const weights = set.map(id => {
    const s = State.stats.byInterval[id];
    if (!s || s.total < 2) return 1.2;
    return 1 + 2.2 * (1 - s.correct / s.total);
  });
  const sum = weights.reduce((a, b) => a + b, 0);
  let r = Math.random() * sum;
  for (let i = 0; i < set.length; i++) {
    r -= weights[i];
    if (r <= 0) return set[i];
  }
  return set[set.length - 1];
}

function pickRoot(semis) {
  const lo = 50, hi = 84 - semis;
  let root;
  do { root = lo + Math.floor(Math.random() * (hi - lo + 1)); }
  while (root === State.lastRoot && hi > lo);
  State.lastRoot = root;
  return root;
}

const VOICE_IDS = ['piano', 'nylon', 'steel'];

function newQuestion() {
  const id = pickInterval();
  const iv = INTERVALS[id];
  return {
    id,
    semi: iv.semi,
    root: pickRoot(iv.semi),
    voice: VOICE_IDS[Math.floor(Math.random() * VOICE_IDS.length)],
    descending: State.lesson.mode === 'melodic' && Math.random() < 0.5,
    replays: 0
  };
}

function playCurrent() {
  const q = State.current;
  State.phase = 'playing';
  setStatus('…');
  el('voice').textContent = AudioEngine.VOICES[q.voice].label +
    (State.lesson.mode === 'melodic' ? (q.descending ? ' · descending' : ' · ascending') : '');
  return AudioEngine.playInterval(q.voice, q.root, q.semi, State.lesson.mode, q.descending);
}

/* ---------- main loop -------------------------------------------------- */
let silenceTimer = null, nudged = false;

function armSilence() {
  clearTimeout(silenceTimer);
  nudged = false;
  silenceTimer = setTimeout(async () => {
    if (State.phase !== 'listening') return;
    nudged = true;
    State.current.replays++;
    playCurrent();
    State.phase = 'listening';
    silenceTimer = setTimeout(() => {
      if (State.phase === 'listening') resolveAnswer(null, true);
    }, 20000);
  }, 16000);
}

async function askQuestion() {
  if (!State.running) return;
  State.current = newQuestion();
  State.pending = null;
  const ms = playCurrent();
  await wait(Math.min(ms, 900));       // barge-in: listening opens early
  if (!State.running) return;
  State.phase = 'listening';
  setStatus('listening');
  armSilence();
}

async function runListenMode() {
  if (!State.running) return;
  State.current = newQuestion();
  const ms = playCurrent();
  await wait(ms + 150);
  if (!State.running || State.mode !== 'listen') return;
  await say(INTERVALS[State.current.id].name);
  await wait(400);
  if (State.running && State.mode === 'listen') runListenMode();
}

function handleUtterance(alts) {
  if (!State.running) return;

  if (State.phase === 'confirming') {
    const yn = interpretYesNo(alts);
    if (yn === null) {
      // Maybe they restated the answer in full instead of yes/no.
      const r = interpret(alts, State.lesson);
      if (r && r.kind === 'answer') return resolveAnswer(r.id);
      return;                       // still unclear — stay quiet, keep waiting
    }
    const [first, second] = State.pending.options;
    return resolveAnswer(yn ? first : second);
  }

  const r = interpret(alts, State.lesson);
  if (!r) return;                   // humming, throat-clearing, room noise

  if (r.kind === 'command') return runCommand(r.id);
  if (r.kind === 'jump') return jumpTo(r.lesson);
  if (State.mode === 'listen') return;
  if (State.phase !== 'listening') return;

  if (r.kind === 'ambiguous') return askConfirm(r);
  if (r.kind === 'answer') return resolveAnswer(r.id);
}

/* The closed yes/no fallback. Rather than gamble on the single vowel that
   separates "major" from "minor", ask a question with two answers that
   sound nothing alike. */
async function askConfirm(r) {
  clearTimeout(silenceTimer);
  State.phase = 'confirming';
  State.pending = r;
  const first = INTERVALS[r.options[0]];
  await say(first.qual === 'minor' ? 'Minor? Yes or no.' : first.qual + '? Yes or no.');
  State.phase = 'confirming';
  setStatus('yes or no?');
  silenceTimer = setTimeout(() => {
    if (State.phase === 'confirming') { State.phase = 'listening'; armSilence(); }
  }, 12000);
}

async function resolveAnswer(id, timedOut) {
  clearTimeout(silenceTimer);
  if (State.phase === 'feedback') return;
  State.phase = 'feedback';

  const q = State.current;
  const truth = INTERVALS[q.id];
  const correct = id === q.id;

  State.stats.total++;
  if (correct) {
    State.stats.correct++;
    if (q.replays === 0) State.stats.firstListen++;
  }
  const bi = State.stats.byInterval[q.id] || { correct: 0, total: 0 };
  bi.total++;
  if (correct) bi.correct++;
  State.stats.byInterval[q.id] = bi;

  State.history.unshift({
    truth: truth.name,
    given: id ? INTERVALS[id].name : (timedOut ? '—' : 'passed'),
    correct
  });
  State.history = State.history.slice(0, 8);
  renderStats();

  if (correct) {
    AudioEngine.chime(true);
    setStatus(truth.name, 'ok');
    await wait(900);
  } else {
    AudioEngine.stopAll();
    await say('No. ' + truth.name + '.', 1.05, 'bad');
    await wait(120);
    const ms = AudioEngine.playInterval(q.voice, q.root, q.semi, State.lesson.mode, q.descending);
    await wait(ms + 200);
  }
  if (State.running && State.mode === 'drill') askQuestion();
}

/* ---------- commands --------------------------------------------------- */
async function runCommand(id) {
  switch (id) {
    case 'repeat':
      if (State.current && (State.phase === 'listening' || State.phase === 'confirming')) {
        State.current.replays++;
        playCurrent();
        State.phase = 'listening';
        armSilence();
      }
      return;
    case 'skip':
      if (State.phase === 'listening' || State.phase === 'confirming') resolveAnswer(null);
      return;
    case 'score':
      return speakScore();
    case 'stop':
      return stopSession();
    case 'help':
      return say('Say the interval. Or say repeat, skip, score, or lesson three.');
    case 'listen':
      if (State.mode === 'listen') return;
      State.mode = 'listen';
      clearTimeout(silenceTimer);
      AudioEngine.stopAll();
      await say('Listen mode.');
      return runListenMode();
    case 'drill':
      if (State.mode === 'drill') return;
      State.mode = 'drill';
      AudioEngine.stopAll();
      await say('Drilling.');
      return askQuestion();
  }
}

async function speakScore() {
  const s = State.stats;
  if (!s.total) return say('Nothing scored yet.');
  const pct = Math.round(100 * s.correct / s.total);
  const fl = Math.round(100 * s.firstListen / s.total);
  await say(`${s.correct} of ${s.total}. ${pct} percent. ${fl} percent on first hearing.`);
  if (State.mode === 'drill' && State.running) askQuestion();
}

async function jumpTo(lesson) {
  State.lesson = lesson;
  localStorage.setItem('ear.lesson', lesson.n);
  clearTimeout(silenceTimer);
  AudioEngine.stopAll();
  renderLesson();
  await say('Lesson ' + lesson.n + '. ' + lesson.title.replace(':', ','));
  if (State.mode === 'listen') runListenMode(); else askQuestion();
}

/* ---------- session ---------------------------------------------------- */
async function startSession() {
  AudioEngine.resume();
  const haveVoice = rec || initRecognition();
  State.running = true;
  State.phase = 'idle';
  el('startWrap').classList.add('hidden');
  if (haveVoice) {
    startListening();
  } else {
    // No recogniser (or it is blocked): still run the drill so the audio
    // itself can be judged, just with typed answers.
    el('mic').classList.add('hidden');
    showFallback();
  }
  await say('Lesson ' + State.lesson.n + '. ' + State.lesson.title.replace(':', ',') + '. Name each interval.', 1.05);
  await wait(250);
  askQuestion();
}

async function stopSession() {
  State.running = false;
  clearTimeout(silenceTimer);
  AudioEngine.stopAll();
  stopListening();
  const s = State.stats;
  if (s.total) {
    const pct = Math.round(100 * s.correct / s.total);
    await say(`Done. ${s.correct} of ${s.total}, ${pct} percent.`);
  } else {
    await say('Stopped.');
  }
  el('startWrap').classList.remove('hidden');
  el('start').textContent = 'Resume';
  setStatus('stopped');
}

/* ---------- view ------------------------------------------------------- */
function setStatus(t, tone) {
  const node = el('status');
  node.textContent = t;
  node.classList.toggle('ok', tone === 'ok');
  node.classList.toggle('bad', tone === 'bad');
}
function setMic(on) { el('mic').classList.toggle('on', on); }

function renderLesson() {
  el('lessonTitle').textContent = State.lesson.title;
  el('lessonNum').textContent = 'Lesson ' + State.lesson.n + ' of 13';
  el('answers').textContent = State.lesson.set.map(id => INTERVALS[id].name).join('  ·  ');
}

function renderStats() {
  const s = State.stats;
  el('tally').textContent = s.total
    ? `${s.correct} of ${s.total}  ·  ${Math.round(100 * s.correct / s.total)}%  ·  ` +
      `${Math.round(100 * s.firstListen / s.total)}% on first hearing`
    : 'not started';
  el('history').innerHTML = State.history.map(h =>
    `<li class="${h.correct ? 'ok' : 'bad'}">` +
    `<span class="truth">${h.truth}</span>` +
    `<span class="given">${h.correct ? 'named it' : 'you said ' + h.given}</span></li>`
  ).join('');
}

function boot() {
  const saved = parseInt(localStorage.getItem('ear.lesson') || '1', 10);
  State.lesson = LESSONS.find(l => l.n === saved) || LESSONS[0];
  renderLesson();
  renderStats();
  el('start').addEventListener('click', startSession);
  el('lessonPicker').innerHTML = LESSONS.map(l =>
    `<option value="${l.n}">${l.n}. ${l.title}</option>`).join('');
  el('lessonPicker').value = State.lesson.n;
  el('lessonPicker').addEventListener('change', (e) => {
    const l = LESSONS.find(x => x.n === parseInt(e.target.value, 10));
    if (!l) return;
    if (State.running) jumpTo(l);
    else { State.lesson = l; localStorage.setItem('ear.lesson', l.n); renderLesson(); }
  });
  el('typed').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const text = e.target.value.trim();
    e.target.value = '';
    if (text && State.running) handleUtterance([text]);
  });
  if (!SR) setStatus('No speech recognition here — Chrome or Edge for voice.');
}

function showFallback() {
  el('fallback').classList.remove('hidden');
  el('typed').focus();
}

document.addEventListener('DOMContentLoaded', boot);
