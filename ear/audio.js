/* Instrument synthesis for ear training.
   Everything is generated in JS — no sample assets, works offline.
   Piano: additive partials with string inharmonicity.
   Nylon / steel guitar: Karplus-Strong with a fractionally-interpolated
   delay line, because integer delay lengths detune high notes by enough
   cents to matter when the whole point is interval identification. */

const AudioEngine = (() => {
  let ctx = null;
  let master = null;
  let wet = null;
  let sink = null;          // <audio> element carrying our output
  let keepalive = null;
  const cache = new Map();

  function init() {
    if (ctx) return ctx;
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    master = ctx.createGain();
    master.gain.value = 0.9;
    master.connect(ctx.destination);

    const conv = ctx.createConvolver();
    conv.buffer = makeImpulse(1.4, 2.6);
    wet = ctx.createGain();
    wet.gain.value = 0.17;
    conv.connect(wet);
    master.connect(conv);

    routeThroughMediaElement();
    return ctx;
  }

  /* Android stops a backgrounded page dead: audio suspended, timers throttled
     to once a minute. A page that is *playing media* is exempt, so the graph
     is routed into an <audio> element rather than straight to the speakers.
     That is also what puts the app on the lock screen, where MediaSession can
     give it controls. */
  function routeThroughMediaElement() {
    try {
      const dest = ctx.createMediaStreamDestination();
      master.connect(dest);
      wet.connect(dest);

      // A media element carrying pure silence can still be judged inaudible,
      // so hold an infrasonic tone well below hearing to keep the tab alive.
      keepalive = ctx.createOscillator();
      const kg = ctx.createGain();
      keepalive.frequency.value = 30;
      kg.gain.value = 0.0002;
      keepalive.connect(kg);
      kg.connect(dest);
      keepalive.start();

      sink = document.createElement('audio');
      sink.srcObject = dest.stream;
      sink.autoplay = true;
      sink.loop = true;
      sink.setAttribute('playsinline', '');
      sink.style.display = 'none';
      document.body.appendChild(sink);
      sink.play().catch(() => {});
    } catch (e) {
      // No media-element route: still works, just not with the screen off.
      master.connect(ctx.destination);
      wet.connect(ctx.destination);
      sink = null;
    }
  }

  const backgroundCapable = () => !!sink;

  function nudge() {
    if (ctx && ctx.state === 'suspended') ctx.resume();
    if (sink && sink.paused) sink.play().catch(() => {});
  }

  function makeImpulse(seconds, decay) {
    const sr = ctx.sampleRate;
    const n = Math.floor(sr * seconds);
    const buf = ctx.createBuffer(2, n, sr);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      for (let i = 0; i < n; i++) {
        d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, decay);
      }
    }
    return buf;
  }

  const midiToFreq = (m) => 440 * Math.pow(2, (m - 69) / 12);

  /* ---- piano ---------------------------------------------------------- */
  function renderPiano(freq, dur, sr) {
    const n = Math.ceil(dur * sr);
    const out = new Float32Array(n);
    const B = 0.00015;   // midrange string stretch; larger values read as sharp
    const nyq = sr * 0.45;

    for (let p = 1; p <= 20; p++) {
      const f = p * freq * Math.sqrt(1 + B * p * p);
      if (f > nyq) break;
      const amp = Math.pow(p, -1.25) * (p % 2 === 0 ? 0.82 : 1.0);
      const tau = 2.4 / (1 + 0.6 * (p - 1));
      const w = 2 * Math.PI * f / sr;
      const c = Math.cos(w), s = Math.sin(w);
      const ph = Math.random() * Math.PI * 2;
      let re = Math.cos(ph), im = Math.sin(ph);
      let env = amp;
      const dec = Math.exp(-1 / (tau * sr));
      for (let i = 0; i < n; i++) {
        out[i] += env * im;
        const nre = re * c - im * s;
        im = re * s + im * c;
        re = nre;
        env *= dec;
      }
    }

    // hammer thump — a brief filtered noise transient at onset
    let lp = 0;
    const hn = Math.floor(sr * 0.018);
    for (let i = 0; i < hn; i++) {
      lp += 0.22 * ((Math.random() * 2 - 1) - lp);
      out[i] += lp * 0.5 * (1 - i / hn);
    }

    applyAttack(out, sr, 0.004);
    return out;
  }

  /* ---- plucked string (Karplus-Strong) -------------------------------- */
  function renderPluck(freq, dur, sr, opt) {
    const { S, t60, bright, pick } = opt;
    const n = Math.ceil(dur * sr);
    const out = new Float32Array(n);

    // Per-round-trip loop gain chosen so the fundamental hits -60 dB at t60,
    // independent of pitch. Raw KS makes high notes die far too quickly.
    const g = Math.exp(-6.9078 / (freq * t60));

    // The one-pole loop filter contributes (1-S) samples of delay.
    const L = sr / freq - (1 - S);
    const D = Math.max(2, Math.floor(L));
    const frac = L - D;
    const M = D + 1;

    const buf = new Float32Array(M);
    let lp = 0;
    for (let i = 0; i < M; i++) {
      lp += bright * ((Math.random() * 2 - 1) - lp);
      buf[i] = lp;
    }
    let mean = 0;
    for (let i = 0; i < M; i++) mean += buf[i];
    mean /= M;
    let peak = 1e-9;
    for (let i = 0; i < M; i++) {
      buf[i] -= mean;
      peak = Math.max(peak, Math.abs(buf[i]));
    }
    for (let i = 0; i < M; i++) buf[i] = (buf[i] / peak) * 0.85;

    let p = 0, last = 0;
    for (let i = 0; i < n; i++) {
      const xd = (1 - frac) * buf[(p + 1) % M] + frac * buf[p];
      const y = g * (S * xd + (1 - S) * last);
      last = xd;
      out[i] = y;
      buf[p] = y;
      p = (p + 1) % M;
    }

    if (pick) {
      const pn = Math.floor(sr * 0.004);
      for (let i = 0; i < pn; i++) out[i] += (Math.random() * 2 - 1) * pick * (1 - i / pn);
    }

    applyAttack(out, sr, 0.002);
    return out;
  }

  function applyAttack(buf, sr, seconds) {
    const a = Math.floor(sr * seconds);
    for (let i = 0; i < a; i++) buf[i] *= i / a;
    const r = Math.floor(sr * 0.05);
    for (let i = 0; i < r; i++) {
      const j = buf.length - 1 - i;
      if (j >= 0) buf[j] *= i / r;
    }
  }

  const VOICES = {
    piano:  { label: 'piano',            render: (f, d, sr) => renderPiano(f, d, sr) },
    nylon:  { label: 'classical guitar', render: (f, d, sr) => renderPluck(f, d, sr, { S: 0.34, t60: 1.9, bright: 0.11, pick: 0 }) },
    steel:  { label: 'acoustic guitar',  render: (f, d, sr) => renderPluck(f, d, sr, { S: 0.56, t60: 3.1, bright: 0.40, pick: 0.06 }) }
  };

  function noteBuffer(voice, midi, dur) {
    const key = voice + ':' + midi + ':' + dur;
    if (cache.has(key)) return cache.get(key);
    const sr = ctx.sampleRate;
    const data = VOICES[voice].render(midiToFreq(midi), dur, sr);
    let peak = 1e-9;
    for (let i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i]));
    const norm = 0.55 / peak;
    const buf = ctx.createBuffer(1, data.length, sr);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) ch[i] = data[i] * norm;
    cache.set(key, buf);
    return buf;
  }

  let live = [];

  function stopAll() {
    live.forEach(s => { try { s.stop(); } catch (e) {} });
    live = [];
  }

  function playNote(voice, midi, when, dur) {
    const buf = noteBuffer(voice, midi, dur);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    const gain = ctx.createGain();
    gain.gain.value = 0.85;
    src.connect(gain);
    gain.connect(master);
    src.start(when);
    live.push(src);
    src.onended = () => { live = live.filter(s => s !== src); };
  }

  /* Plays one interval. Returns ms until the sound has finished. */
  function playInterval(voice, lowMidi, semitones, mode, descending) {
    init();
    stopAll();
    const t = ctx.currentTime + 0.06;
    const hi = lowMidi + semitones;

    if (mode === 'harmonic') {
      playNote(voice, lowMidi, t, 3.0);
      playNote(voice, hi, t, 3.0);
      return 2400;
    }
    const gap = 0.62;
    const first = descending ? hi : lowMidi;
    const second = descending ? lowMidi : hi;
    playNote(voice, first, t, 2.2);
    playNote(voice, second, t + gap, 2.6);
    return (gap + 2.0) * 1000;
  }

  /* Short non-verbal confirmation — faster and less grating than a spoken
     "correct" when you are doing twenty of these in a row. */
  function chime(ok) {
    init();
    const t = ctx.currentTime;
    const freqs = ok ? [880, 1318.5] : [300, 220];
    freqs.forEach((f, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = 'sine';
      o.frequency.value = f;
      const st = t + i * 0.09;
      g.gain.setValueAtTime(0, st);
      g.gain.linearRampToValueAtTime(0.18, st + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, st + 0.22);
      o.connect(g);
      g.connect(master);
      o.start(st);
      o.stop(st + 0.25);
    });
  }

  function resume() {
    init();
    if (ctx.state === 'suspended') ctx.resume();
  }

  return { init, resume, playInterval, chime, stopAll, VOICES, midiToFreq,
           nudge, backgroundCapable,
           _renderPiano: renderPiano, _renderPluck: renderPluck };
})();
