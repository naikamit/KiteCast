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
  listenSince: 0,
  paused: false,
  stats: { correct: 0, total: 0, firstListen: 0, byInterval: {} },
  mastery: {},              // lifetime counts from the server, by interval
  history: []
};

const el = (id) => document.getElementById(id);

/* ---------- log --------------------------------------------------------
   Timings, not prose: "sometimes it lags" is only diagnosable with numbers
   against each step. Deliberately never prints the interval being asked —
   that would spoil the drill you are logging. */
const T0 = performance.now();
const LOG_MAX = 140;
const logLines = [];

const stamp = () => ((performance.now() - T0) / 1000).toFixed(1).padStart(6) + 's';
const esc = (t) => String(t).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

function log(kind, text) {
  const line = `${stamp()} ${kind.padEnd(5)} ${text}`;
  logLines.push(line);
  if (logLines.length > LOG_MAX) logLines.shift();
  const node = el('log');
  if (!node) return;
  const li = document.createElement('li');
  li.className = 'k-' + kind;   // namespaced: a bare .mic collides with the indicator
  li.innerHTML = `<span class="t">${stamp()}</span> <span class="k">${kind}</span> ${esc(text)}`;
  node.appendChild(li);
  while (node.children.length > LOG_MAX) node.removeChild(node.firstChild);
  node.scrollTop = node.scrollHeight;
}

const since = (t) => t ? '+' + Math.round(performance.now() - t) + 'ms' : '';

/* ---------- speech out ------------------------------------------------ */
let voice = null;

// Unhurried. The default 1.0 reads as brisk, which is the wrong register for
// something you are concentrating hard on.
const VOICE_RATE = 0.88;
const VOICE_PITCH = 0.96;

function pickVoice() {
  const vs = speechSynthesis.getVoices();
  if (!vs.length) return;
  // No accent, in practice, means General American — so en-US first and let
  // the regional voices (en-GB, en-AU, en-IN…) fall to the back. Within that,
  // a neural voice is markedly calmer than the old formant synths.
  const pick = (list) => list.find(v => /natural|neural/i.test(v.name))
                      || list.find(v => /google/i.test(v.name))
                      || list.find(v => v.default)
                      || list[0];
  const us = vs.filter(v => /^en[-_]us$/i.test(v.lang));
  const en = vs.filter(v => /^en[-_]/i.test(v.lang));
  voice = pick(us) || pick(en) || vs[0] || null;
}
if (typeof speechSynthesis !== 'undefined') {
  pickVoice();
  speechSynthesis.onvoiceschanged = pickVoice;
}

let lastSpoken = '', lastSpokenAt = 0, speakGate = null;

function say(text, rate = VOICE_RATE, tone) {
  return new Promise(resolve => {
    if (!text) return resolve();
    setStatus(text, tone);

    // Hold the microphone shut for the whole utterance. Without this the app
    // hears its own prompts, and since those prompts name the lesson they
    // read as navigation commands — it tells itself to jump, forever.
    lastSpoken = normalise(text);
    State.speaking = true;
    clearTimeout(speakGate);
    log('say', '"' + text + '"');
    muteRecognition(true);

    const u = new SpeechSynthesisUtterance(text);
    if (voice) u.voice = voice;
    u.lang = 'en-US';          // steers the fallback voice away from a regional one
    u.rate = rate;
    u.pitch = VOICE_PITCH;
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
        log('mic', 'gate open (700ms after speech)');
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
let recOpenedAt = 0, interimLogged = false;

function initRecognition() {
  if (!SR) return false;
  rec = new SR();
  rec.continuous = true;
  rec.interimResults = true;
  rec.maxAlternatives = 5;
  rec.lang = 'en-US';

  rec.onstart = () => {
    recActive = true;
    setMic(true);
    log('mic', 'open' + (recOpenedAt ? ' (gap ' + Math.round(performance.now() - recOpenedAt) + 'ms)' : ''));
  };

  rec.onend = () => {
    recActive = false;
    setMic(false);
    // Android Chrome ignores `continuous` and ends after every utterance, so
    // the restart is the normal path, not the exception. Back off only on a
    // real error, otherwise restarting slowly would swallow answers.
    recOpenedAt = performance.now();
    if (!wantListening || recMuted) { log('mic', 'closed'); return; }
    // Android ends the stream after every utterance, so this gap is the main
    // suspect whenever an answer seems to have been swallowed.
    log('mic', 'closed, reopening in ' + restartDelay + 'ms');
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
      if (e.error !== 'aborted') log('warn', e.error);
    } else {
      restartDelay = Math.min(restartDelay ? restartDelay * 2 : 400, 4000);
      log('warn', e.error + ' — backing off to ' + restartDelay + 'ms');
    }
  };

  rec.onresult = (ev) => {
    const res = ev.results[ev.results.length - 1];
    const alts = [];
    for (let i = 0; i < res.length; i++) alts.push(res[i].transcript);

    const shown = (alts[0] || '').slice(-80);
    el('heard').textContent = shown;

    if (State.speaking || recMuted) return;
    if (isSelfEcho(alts)) { if (res.isFinal) log('hear', 'ignored our own prompt'); return; }

    if (!res.isFinal) {
      if (!interimLogged && shown.trim()) {
        interimLogged = true;
        log('hear', 'interim ' + since(State.listenSince) + ' "' + shown + '"');
      }
      // Acting on a confident interim is what removes most of the wait: the
      // final transcript often lands a second later saying the same thing.
      if (State.phase === 'listening') {
        const early = interpret(alts, State.lesson);
        if (early && early.kind === 'answer') {
          log('hear', 'took interim answer ' + since(State.listenSince));
          handleUtterance(alts);
        }
      }
      return;
    }

    restartDelay = 0;
    log('hear', 'final ' + since(State.listenSince) + ' "' + shown + '"');
    interimLogged = false;
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
  // Never logs which interval — that would spoil the drill being logged.
  log('play', AudioEngine.VOICES[q.voice].label + ' · ' + State.lesson.mode +
      (State.lesson.mode === 'melodic' ? (q.descending ? ' down' : ' up') : '') +
      (q.replays ? ' · replay ' + q.replays : ''));
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
    if (State.phase !== 'listening' || State.paused) return;
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
  if (!State.running || State.paused) return;
  State.current = newQuestion();
  State.pending = null;
  const ms = playCurrent();
  await wait(Math.min(ms, 900));       // barge-in: listening opens early
  if (!State.running) return;
  State.phase = 'listening';
  State.listenSince = performance.now();
  interimLogged = false;
  setStatus('listening');
  log('mic', 'awaiting answer' + (recActive ? '' : ' (recogniser not open yet)'));
  armSilence();
}

async function runListenMode() {
  if (!State.running || State.paused) return;
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

  // While paused the microphone stays open — muting it would leave no way to
  // say "resume" in an app with nothing to tap. Only commands get through.
  if (State.paused) {
    const c = interpret(alts, State.lesson);
    if (c && c.kind === 'command') runCommand(c.id);
    return;
  }

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
  await say('Was it ' + first.qual + '?');
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
  log(correct ? 'ok' : 'bad',
      truth.name + (id && !correct ? ' — you said ' + INTERVALS[id].name : '') +
      (timedOut ? ' — timed out' : (!id && !timedOut ? ' — passed' : '')) +
      '  ' + since(State.listenSince));
  reportAnswer(q.id, correct, correct && q.replays === 0);
  renderStats();

  if (correct) {
    AudioEngine.chime(true);
    setStatus(truth.name, 'ok');
    await wait(1150);
  } else {
    AudioEngine.stopAll();
    await say('Not quite. ' + truth.name + '.', VOICE_RATE, 'bad');
    await wait(120);
    const ms = AudioEngine.playInterval(q.voice, q.root, q.semi, State.lesson.mode, q.descending);
    await wait(ms + 550);
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
    case 'pause':
      return pauseSession();
    case 'resume':
      return resumeSession();
    case 'stop':
      return stopSession();
    case 'help':
      return say('Just name what you hear. You can also say repeat, skip, score, or pause.');
    case 'listen':
      if (State.mode === 'listen') return;
      State.mode = 'listen';
      clearTimeout(silenceTimer);
      AudioEngine.stopAll();
      await say('Just listening.');
      return runListenMode();
    case 'drill':
      if (State.mode === 'drill') return;
      State.mode = 'drill';
      AudioEngine.stopAll();
      await say('Back to it.');
      return askQuestion();
  }
}

async function speakScore() {
  const s = State.stats;
  if (!s.total) return say('Nothing scored yet.');
  const pct = Math.round(100 * s.correct / s.total);
  const fl = Math.round(100 * s.firstListen / s.total);
  await say(`${s.correct} of ${s.total}. That's ${pct} percent, and ${fl} percent on first hearing.`);
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
  await wait(250);
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

/* ---------- transport --------------------------------------------------- */
function renderTransport() {
  const b = el('start');
  b.textContent = !State.running ? (State.stats.total ? 'Resume' : 'Start')
                : State.paused ? 'Resume' : 'Pause';
  el('pausedNote').hidden = !(State.running && State.paused);
}

function pauseSession() {
  if (!State.running || State.paused) return;
  State.paused = true;
  State.phase = 'idle';
  clearTimeout(silenceTimer);
  AudioEngine.stopAll();
  try { speechSynthesis.cancel(); } catch (e) {}
  keepAwake(false);
  setStatus('paused');
  log('mic', 'paused — still listening for commands');
  renderTransport();
}

function resumeSession() {
  if (!State.running || !State.paused) return;
  State.paused = false;
  keepAwake(true);
  startListening();
  log('mic', 'resumed');
  renderTransport();
  if (State.mode === 'listen') runListenMode(); else askQuestion();
}

function onTransport() {
  if (!State.running) return startSession();
  return State.paused ? resumeSession() : pauseSession();
}

/* ---------- session ---------------------------------------------------- */
async function startSession() {
  AudioEngine.resume();
  const haveVoice = rec || initRecognition();
  State.running = true;
  State.paused = false;
  State.phase = 'idle';
  keepAwake(true);
  renderTransport();
  log('mic', 'session started');
  if (haveVoice) {
    startListening();
  } else {
    // No recogniser (or it is blocked): still run the drill so the audio
    // itself can be judged, just with typed answers.
    el('mic').classList.add('hidden');
    showFallback();
  }
  // No preamble: the lesson is on screen, and announcing it was both
  // redundant and the sentence the app used to hear itself say and loop on.
  await wait(350);
  askQuestion();
}

async function stopSession() {
  State.running = false;
  clearTimeout(silenceTimer);
  AudioEngine.stopAll();
  stopListening();
  keepAwake(false);
  State.paused = false;
  renderTransport();
  log('mic', 'session stopped');
  const s = State.stats;
  if (s.total) {
    const pct = Math.round(100 * s.correct / s.total);
    await say(`That's ${s.correct} of ${s.total}. ${pct} percent.`);
  } else {
    await say('Stopped.');
  }
  setStatus('stopped');
  renderTransport();
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
  el('start').addEventListener('click', onTransport);
  renderTransport();
  el('logclear').addEventListener('click', () => {
    logLines.length = 0;
    el('log').innerHTML = '';
  });
  el('logcopy').addEventListener('click', async (e) => {
    const text = logLines.join('\n');
    try {
      await navigator.clipboard.writeText(text);
      e.target.textContent = 'Copied';
    } catch (err) {
      // Clipboard access is refused in some embeddings; select it instead so
      // a long-press copy still works.
      const r = document.createRange();
      r.selectNodeContents(el('log'));
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
      e.target.textContent = 'Selected';
    }
    setTimeout(() => { e.target.textContent = 'Copy'; }, 1600);
  });
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
