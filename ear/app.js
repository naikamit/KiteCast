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
  mastery: {},              // lifetime counts from the server, by interval
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

let lastSpoken = '', lastSpokenAt = 0, speakGate = null;

function say(text, rate = 1.05, tone) {
  return new Promise(resolve => {
    if (!text) return resolve();
    setStatus(text, tone);

    // Hold the microphone shut for the whole utterance. Without this the app
    // hears its own prompts, and since those prompts name the lesson they
    // read as navigation commands — it tells itself to jump, forever.
    lastSpoken = normalise(text);
    State.speaking = true;
    clearTimeout(speakGate);
    muteRecognition(true);

    const u = new SpeechSynthesisUtterance(text);
    if (voice) u.voice = voice;
    u.rate = rate;
    u.pitch = 1.0;
    let finished = false;
    const done = () => {
      if (finished) return;
      finished = true;
      clearTimeout(bail);
      lastSpokenAt = Date.now();
      // Android delivers a transcript well after speech ends, so the gate has
      // to outlive onend or the tail of our own sentence still gets through.
      speakGate = setTimeout(() => {
        State.speaking = false;
        muteRecognition(false);
      }, 700);
      resolve();
    };
    // A synthesiser that never reports finishing would hold the gate shut and
    // leave the app permanently deaf, so never wait on it indefinitely.
    const bail = setTimeout(done, 2000 + text.length * 90);
    u.onend = u.onerror = done;
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  });
}

/* Anything we just said, coming back at us. A late result can land after the
   gate reopens, so this catches the straggler — but only for a moment, or it
   would swallow a real answer that happens to repeat the interval we named. */
function isSelfEcho(alternatives) {
  if (!lastSpoken || Date.now() - lastSpokenAt > 2500) return false;
  return alternatives.some(a => {
    const words = normalise(a).split(' ').filter(Boolean);
    return words.length && words.every(w => lastSpoken.includes(w));
  });
}

const wait = (ms) => new Promise(r => setTimeout(r, ms));

/* ---------- speech in -------------------------------------------------- */
let rec = null, recActive = false, wantListening = false, restartDelay = 0, recMuted = false;

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
    // Android Chrome ignores `continuous` and ends after every utterance, so
    // the restart is the normal path, not the exception. Back off only on a
    // real error, otherwise restarting slowly would swallow answers.
    if (!wantListening || recMuted) return;
    setTimeout(() => {
      if (wantListening && !recMuted && !recActive) { try { rec.start(); } catch (e) {} }
    }, restartDelay);
  };

  rec.onerror = (e) => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      wantListening = false;
      el('mic').classList.add('hidden');
      showFallback();
    } else if (e.error === 'no-speech' || e.error === 'aborted') {
      restartDelay = 0;
    } else {
      restartDelay = Math.min(restartDelay ? restartDelay * 2 : 400, 4000);
    }
  };

  rec.onresult = (ev) => {
    const res = ev.results[ev.results.length - 1];
    const alts = [];
    for (let i = 0; i < res.length; i++) alts.push(res[i].transcript);

    const shown = (alts[0] || '').slice(-80);
    if (!res.isFinal) { el('heard').textContent = shown; return; }
    el('heard').textContent = shown;
    restartDelay = 0;
    if (State.speaking || recMuted) return;
    if (isSelfEcho(alts)) return;    // our own prompt, arriving late
    handleUtterance(alts);
  };
  return true;
}

function muteRecognition(on) {
  recMuted = on;
  if (!rec) return;
  if (on) {
    try { rec.abort(); } catch (e) {}
  } else if (wantListening && !recActive) {
    try { rec.start(); } catch (e) {}
  }
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
  // Weight toward the intervals you actually miss, counting every session
  // ever drilled rather than just this one — which is the point of the
  // server keeping mastery at all.
  const weights = set.map(id => {
    const life = State.mastery[id] || { seen: 0, correct: 0 };
    const now = State.stats.byInterval[id] || { total: 0, correct: 0 };
    const seen = life.seen + now.total;
    const ok = life.correct + now.correct;
    if (seen < 3) return 1.3;
    return 1 + 2.2 * (1 - ok / seen);
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
  reportAnswer(q.id, correct, correct && q.replays === 0);
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
  // Re-announcing the current lesson would restate its name, which is itself
  // a jump command — the loop this app fell into. Staying put breaks it.
  if (lesson.n === State.lesson.n) return;
  State.lesson = lesson;
  localStorage.setItem('ear.lesson', lesson.n);
  clearTimeout(silenceTimer);
  AudioEngine.stopAll();
  renderLesson();
  await say('Lesson ' + lesson.n + '. ' + lesson.title.replace(':', ','));
  if (State.mode === 'listen') runListenMode(); else askQuestion();
}

/* ---------- mastery, kept server-side ---------------------------------- */

/* Counts live on the server so they outlast a session, a cleared cache or a
   different device. Both calls fail quietly: the drill is playable offline,
   and losing a tally is not worth interrupting it for. */
async function loadMastery() {
  try {
    const r = await fetch('./api/progress', { credentials: 'same-origin' });
    if (!r.ok) return;
    const d = await r.json();
    State.mastery = d.intervals || {};
    renderLifetime(d);
  } catch (e) { /* offline, or served as plain files */ }
}

function reportAnswer(interval, correct, firstListen) {
  fetch('./api/answer', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ interval, correct, first_listen: firstListen })
  }).then(r => r.ok ? r.json() : null)
    .then(row => { if (row) State.mastery[interval] = row; })
    .catch(() => {});
}

function renderLifetime(d) {
  const node = el('lifetime');
  if (!node) return;
  if (!d || !d.seen) { node.textContent = ''; return; }
  const pct = Math.round(100 * d.accuracy);
  const weak = (d.weakest || []).map(i => INTERVALS[i].name);
  node.textContent = `all time  ·  ${d.seen} answers  ·  ${pct}%` +
    (weak.length ? `  ·  weakest ${weak.join(', ')}` : '');
}

/* ---------- screen wake lock ------------------------------------------- */
let wakeLock = null;

async function keepAwake(on) {
  try {
    if (on && 'wakeLock' in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    } else if (!on && wakeLock) {
      await wakeLock.release();
      wakeLock = null;
    }
  } catch (e) { /* unsupported or refused; the drill still runs */ }
}

document.addEventListener('visibilitychange', () => {
  // Android drops the lock whenever the app is backgrounded.
  if (document.visibilityState === 'visible' && State.running) keepAwake(true);
});

/* ---------- session ---------------------------------------------------- */
async function startSession() {
  AudioEngine.resume();
  const haveVoice = rec || initRecognition();
  State.running = true;
  State.phase = 'idle';
  keepAwake(true);
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
  keepAwake(false);
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
  loadMastery();
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
