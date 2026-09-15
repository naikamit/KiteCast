/* Interval definitions, the lesson ladder, and speech-to-answer matching. */

const INTERVALS = {
  m2: { semi: 1,  name: 'minor second',   size: 2, qual: 'minor' },
  M2: { semi: 2,  name: 'major second',   size: 2, qual: 'major' },
  m3: { semi: 3,  name: 'minor third',    size: 3, qual: 'minor' },
  M3: { semi: 4,  name: 'major third',    size: 3, qual: 'major' },
  P4: { semi: 5,  name: 'perfect fourth', size: 4, qual: 'perfect' },
  TT: { semi: 6,  name: 'tritone',        size: 0, qual: 'tritone' },
  P5: { semi: 7,  name: 'perfect fifth',  size: 5, qual: 'perfect' },
  m6: { semi: 8,  name: 'minor sixth',    size: 6, qual: 'minor' },
  M6: { semi: 9,  name: 'major sixth',    size: 6, qual: 'major' },
  m7: { semi: 10, name: 'minor seventh',  size: 7, qual: 'minor' },
  M7: { semi: 11, name: 'major seventh',  size: 7, qual: 'major' },
  P8: { semi: 12, name: 'octave',         size: 8, qual: 'perfect' }
};

const ALL = Object.keys(INTERVALS);

const LESSONS = [
  { n: 1,  title: 'Harmonic: Seconds',                    mode: 'harmonic', set: ['m2', 'M2'] },
  { n: 2,  title: 'Melodic: Seconds',                     mode: 'melodic',  set: ['m2', 'M2'] },
  { n: 3,  title: 'Harmonic: Thirds',                     mode: 'harmonic', set: ['m3', 'M3'] },
  { n: 4,  title: 'Melodic: Thirds',                      mode: 'melodic',  set: ['m3', 'M3'] },
  { n: 5,  title: 'Harmonic: Fourths and Fifths',         mode: 'harmonic', set: ['P4', 'TT', 'P5'] },
  { n: 6,  title: 'Melodic: Fourths and Fifths',          mode: 'melodic',  set: ['P4', 'TT', 'P5'] },
  { n: 7,  title: 'Harmonic: Sixths',                     mode: 'harmonic', set: ['m6', 'M6'] },
  { n: 8,  title: 'Melodic: Sixths',                      mode: 'melodic',  set: ['m6', 'M6'] },
  { n: 9,  title: 'Harmonic: Sevenths',                   mode: 'harmonic', set: ['m7', 'M7'] },
  { n: 10, title: 'Melodic: Sevenths',                    mode: 'melodic',  set: ['m7', 'M7'] },
  { n: 11, title: 'Harmonic: Tritones and Major Sevenths',mode: 'harmonic', set: ['TT', 'M7'] },
  { n: 12, title: 'All Intervals: Harmonic',              mode: 'harmonic', set: ALL },
  { n: 13, title: 'All Intervals: Melodic',               mode: 'melodic',  set: ALL }
];

/* Spoken forms. Bare size words ("third", "sixth") are deliberately absent —
   they are what triggers the yes/no disambiguation instead of a guess. */
const ALIASES = {
  m2: ['minor second', 'minor 2nd', 'flat two', 'flat second', 'half step', 'halfstep', 'semitone', 'b2'],
  M2: ['major second', 'major 2nd', 'whole step', 'wholestep', 'whole tone'],
  m3: ['minor third', 'minor 3rd', 'flat three', 'flat third', 'b3'],
  M3: ['major third', 'major 3rd', 'natural third'],
  P4: ['perfect fourth', 'perfect 4th', 'fourth', '4th', 'perfect four'],
  TT: ['tritone', 'tri tone', 'try tone', 'augmented fourth', 'diminished fifth', 'flat five', 'sharp four', 'flat 5', 'sharp 4'],
  P5: ['perfect fifth', 'perfect 5th', 'fifth', '5th', 'perfect five'],
  m6: ['minor sixth', 'minor 6th', 'flat six', 'flat sixth', 'augmented fifth', 'sharp five', 'b6'],
  M6: ['major sixth', 'major 6th', 'natural sixth'],
  m7: ['minor seventh', 'minor 7th', 'flat seven', 'flat seventh', 'dominant seventh', 'b7'],
  M7: ['major seventh', 'major 7th', 'natural seventh'],
  P8: ['octave', 'perfect octave', 'eighth', 'octav']
};

const SIZE_WORDS = {
  second: 2, seconds: 2, '2nd': 2,
  third: 3, thirds: 3, '3rd': 3,
  fourth: 4, fourths: 4, '4th': 4,
  fifth: 5, fifths: 5, '5th': 5,
  sixth: 6, sixths: 6, '6th': 6,
  seventh: 7, sevenths: 7, '7th': 7,
  octave: 8, eighth: 8
};

/* Words that carry no answer content. Direction is accepted and discarded:
   the stimulus randomises it, but naming it is optional and not scored. */
const FILLER = /\b(its|it is|thats|that is|i think|maybe|sounds like|sounds|like|a|an|the|um|uh|er|ah|hmm|up|down|upward|downward|ascending|descending|rising|falling|higher|lower|going)\b/g;

function normalise(text) {
  return (' ' + text.toLowerCase() + ' ')
    .replace(/[^a-z0-9 ]+/g, ' ')
    .replace(/\bmin\b/g, 'minor')
    .replace(/\bmaj\b/g, 'major')
    .replace(/\bperf\b/g, 'perfect')
    .replace(FILLER, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/* Longest alias wins, so "minor seventh" is never shortened to "seventh". */
function findInterval(norm, set) {
  let best = null, bestLen = 0;
  for (const id of set) {
    for (const alias of ALIASES[id]) {
      if (alias.length <= bestLen) continue;
      const re = new RegExp('(^| )' + alias.replace(/\s+/g, ' ') + '($| )');
      if (re.test(norm)) { best = id; bestLen = alias.length; }
    }
  }
  return best;
}

function findSize(norm) {
  for (const w in SIZE_WORDS) {
    if (new RegExp('(^| )' + w + '($| )').test(norm)) return SIZE_WORDS[w];
  }
  return null;
}

const YES = /(^| )(yes|yeah|yep|yup|ya|correct|right|affirmative|true|si)($| )/;
const NO  = /(^| )(no|nope|nah|negative|not|false|wrong)($| )/;

const COMMANDS = [
  { id: 'repeat',  re: /(^| )(repeat|replay|again|play again|once more|say again)($| )/ },
  { id: 'skip',    re: /(^| )(skip|pass|next|dont know|do not know|no idea|give up)($| )/ },
  { id: 'score',   re: /(^| )(score|how am i doing|my score|stats|progress)($| )/ },
  { id: 'stop',    re: /(^| )(stop|quit|exit|end session|finish|pause)($| )/ },
  { id: 'help',    re: /(^| )(help|what can i say|commands)($| )/ },
  { id: 'slower',  re: /(^| )(slower|slow down)($| )/ },
  { id: 'listen',  re: /(^| )(listen mode|listen|teach me|demo)($| )/ },
  { id: 'drill',   re: /(^| )(drill|practice|quiz me|test me|start drilling)($| )/ }
];

function findCommand(norm) {
  for (const c of COMMANDS) if (c.re.test(norm)) return c.id;
  return null;
}

function findLessonJump(norm) {
  const num = norm.match(/(^| )lesson (\d+)($| )/);
  if (num) {
    const n = parseInt(num[2], 10);
    return LESSONS.find(l => l.n === n) || null;
  }
  const words = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7,
                  eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13 };
  const wm = norm.match(/(^| )lesson (\w+)($| )/);
  if (wm && words[wm[2]]) return LESSONS.find(l => l.n === words[wm[2]]) || null;

  // "harmonic thirds", "all intervals melodic"
  const wantsHarm = /\bharmonic\b/.test(norm);
  const wantsMel = /\bmelodic\b/.test(norm);
  if (!wantsHarm && !wantsMel) return null;
  const mode = wantsHarm ? 'harmonic' : 'melodic';
  const size = findSize(norm);
  if (/\ball\b/.test(norm)) return LESSONS.find(l => l.mode === mode && l.set.length === ALL.length) || null;
  if (size) {
    return LESSONS.find(l => l.mode === mode && l.set.some(id => INTERVALS[id].size === size)) || null;
  }
  return null;
}

/* The core of the voice interaction.
   Returns one of:
     {kind:'command'|'jump'|'answer'}      — act on it
     {kind:'ambiguous', size, options}     — ask a closed yes/no question
     null                                  — unrecognised; stay silent.
   That last case is what makes humming usable: singing to work out what you
   are hearing produces no grammar match, so it is simply ignored rather than
   scored or met with "I didn't catch that". */
function interpret(alternatives, lesson) {
  const norms = alternatives.map(normalise).filter(Boolean);
  if (!norms.length) return null;

  for (const norm of norms) {
    const cmd = findCommand(norm);
    if (cmd) return { kind: 'command', id: cmd, heard: norm };
  }
  for (const norm of norms) {
    const jump = findLessonJump(norm);
    if (jump) return { kind: 'jump', lesson: jump, heard: norm };
  }

  // Do the alternatives disagree about quality within one size? If the
  // recogniser itself cannot decide between major and minor, do not guess.
  const hits = [];
  for (const norm of norms) {
    const id = findInterval(norm, lesson.set);
    if (id && !hits.includes(id)) hits.push(id);
  }
  if (hits.length > 1) {
    const sizes = new Set(hits.map(id => INTERVALS[id].size));
    if (sizes.size === 1) {
      const size = INTERVALS[hits[0]].size;
      const options = lesson.set.filter(id => INTERVALS[id].size === size);
      return { kind: 'ambiguous', size, options, heard: norms[0] };
    }
  }
  if (hits.length === 1) return { kind: 'answer', id: hits[0], heard: norms[0] };

  // Named a size but not a quality: "a third". Ambiguous only when this
  // lesson actually contains more than one interval of that size.
  for (const norm of norms) {
    const size = findSize(norm);
    if (size === null) continue;
    const options = lesson.set.filter(id => INTERVALS[id].size === size);
    if (options.length === 1) return { kind: 'answer', id: options[0], heard: norm };
    if (options.length > 1) return { kind: 'ambiguous', size, options, heard: norm };
  }

  return null;
}

function interpretYesNo(alternatives) {
  for (const raw of alternatives) {
    const norm = normalise(raw);
    if (YES.test(' ' + norm + ' ')) return true;
    if (NO.test(' ' + norm + ' ')) return false;
  }
  return null;
}
