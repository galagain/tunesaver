import WaveSurfer from './vendor/wavesurfer/wavesurfer.esm.js';
import { FLX4Decoder, clamp, crossfade, isFLX4Port } from './flx4.js?v=20260908-8';
import { Timeline, cueColors, cueTextColor, overviewRenderer, phaseAlignedTime, detectedGrid, usesVariableGrid } from './mix-waveforms.js?v=20260910-14';
import { makeKnob, refreshKnobs } from './mix-knobs.js?v=20260910-10';
import { createElement, Play, Pause, Headphones, Flag, Repeat2, LogIn, LogOut, Usb, Settings2, Square, RefreshCw, SkipBack, Gauge, Unplug, X, Minus, Plus } from './vendor/mixer-icons.js';
import { SkipForward, Volume2, VolumeX } from './vendor/player-icons.js';

const $ = (selector, root = document) => root.querySelector(selector);
const host = () => window.TuneSaverMixerHost;
const dockObserver = new ResizeObserver(() => document.documentElement.style.setProperty('--playback-height', `${$('#playback-dock').getBoundingClientRect().height}px`));
dockObserver.observe($('#playback-dock'));
const message = (text = '') => {
  const target = $('#mix-message');
  target.replaceChildren();
  if (!text) return;
  const label = document.createElement('span');
  label.textContent = text;
  target.append(label, button('dismiss', 'Fermer le message', X, () => message()));
  if (host()?.signedIn) $('#mix-controls').showPopover();
};
const icon = (shape) => createElement(shape, { 'aria-hidden': 'true' });
const time = (seconds) => {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  return `${Math.floor(safe / 60)}:${String(Math.floor(safe % 60)).padStart(2, '0')}`;
};
const run = (callback) => { Promise.resolve().then(callback).catch(e => message(e.message || 'Action impossible.')); };
let active = false;
let connectionOpen = false;
let bus = null;
let decks = [];
let midi = null;
let midiInput = null;
let midiOpening = null;
let midiFailedInput = null;
let midiOutput = null;
let midiWanted = false;
let midiEpoch = 0;
let midiSelection = '';
let midiBinding = Promise.resolve();
let midiClosing = Promise.resolve();
let midiTimer = null;
let midiPermission = null;
let decoder = new FLX4Decoder();
const cueMemory = new Map();
const saves = new Map();
let mixerEpoch = 0;
let animation = null;
let analysisTimer = null;
const sideControlsQuery = matchMedia('(min-width: 1360px)');
let browserFocus = 'tracks';
let collectionCursor = '';

function animate() {
  if (!document.hidden) decks.filter(d => !d.index || active).forEach(d => d.timeline.draw(clamp(Number($('#mix-wave-zoom').value) || Number($('#mix-wave-zoom').defaultValue), 1, 24)));
  animation = requestAnimationFrame(animate);
}

async function saveSettings(job, patch) {
  const epoch = mixerEpoch;
  const next = (saves.get(job) || Promise.resolve()).catch(() => {}).then(async () => {
    if (epoch !== mixerEpoch || !host()?.signedIn) return;
    return host().requestJson(`/api/mixer/tracks/${encodeURIComponent(job)}`, { method: 'PATCH', body: JSON.stringify(patch) });
  });
  saves.set(job, next);
  try {
    const data = await next;
    if (epoch === mixerEpoch && saves.get(job) === next && data) decks.filter(d => d.track?.job_id === job).forEach(d => d.applyAnalysis(data));
  } finally { if (saves.get(job) === next) saves.delete(job); }
}

function button(action, label, shape, handler) {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'mix-icon';
  el.dataset.action = action;
  el.title = label;
  el.setAttribute('aria-label', label);
  el.append(icon(shape));
  el.addEventListener('click', () => run(handler));
  return el;
}

function cover(track) {
  const el = document.createElement('div');
  el.className = 'mix-cover';
  el.textContent = (track?.title || '?').slice(0, 1).toUpperCase();
  if (track?.cover_url) {
    const img = document.createElement('img');
    img.alt = '';
    img.addEventListener('error', () => img.remove());
    el.append(img);
    host().loadPrivateImage(img, track.cover_url);
  }
  return el;
}

class AudioBus {
  constructor() {
    this.context = new AudioContext({ latencyHint: 'interactive' });
    const ctx = this.context;
    this.master = ctx.createGain();
    this.master.gain.value = 0.7;
    this.guard = ctx.createDynamicsCompressor();
    this.guard.threshold.value = -2;
    this.guard.knee.value = 0;
    this.guard.ratio.value = 20;
    this.guard.attack.value = 0.003;
    this.guard.release.value = 0.1;
    this.master.connect(this.guard);
    this.head = ctx.createGain();
    this.head.gain.value = 0.5;
    this.monitor = ctx.createGain();
    this.monitor.gain.value = 0;
    this.guard.connect(this.monitor);
    this.monitor.connect(this.head);
    this.masterSplit = ctx.createChannelSplitter(2);
    this.headSplit = ctx.createChannelSplitter(2);
    this.merger = ctx.createChannelMerger(4);
    this.masterSplit.connect(this.merger, 0, 0);
    this.masterSplit.connect(this.merger, 1, 1);
    this.headSplit.connect(this.merger, 0, 2);
    this.headSplit.connect(this.merger, 1, 3);
    this.split = false;
    this.route(false);
  }

  route(split) {
    const ctx = this.context;
    const available = ctx.destination.maxChannelCount >= 4;
    this.split = Boolean(split && available);
    this.guard.disconnect();
    this.guard.connect(this.monitor);
    this.head.disconnect();
    this.merger.disconnect();
    // Firefox can expose an unknown (zero) maximum. Keep its default routing.
    if (ctx.destination.maxChannelCount > 0) ctx.destination.channelCount = this.split ? 4 : Math.min(2, ctx.destination.maxChannelCount);
    ctx.destination.channelInterpretation = this.split ? 'discrete' : 'speakers';
    if (this.split) {
      this.guard.connect(this.masterSplit);
      this.head.connect(this.headSplit);
      this.merger.connect(ctx.destination);
    } else this.guard.connect(ctx.destination);
    this.updateRoutingControls();
  }

  updateRoutingControls() {
    const available = this.context.destination.maxChannelCount >= 4;
    $('#mix-routing option[value="split"]').disabled = !available;
    $('#mix-routing').value = this.split ? 'split' : 'stereo';
    $('#mix-head-level').disabled = !this.split;
    $('#mix-head-mix').disabled = !this.split;
    $('#mix-routing').title = this.split
      ? 'Master : canaux 1/2. Casque : canaux 3/4.'
      : available ? 'Sortie stéréo. Quatre canaux disponibles.' : 'Sortie stéréo. Préécoute séparée indisponible sur cette sortie.';
    decks.forEach(d => d.update());
  }

  set(param, value) { param.setTargetAtTime(value, this.context.currentTime, 0.015); }
}

async function ensureAudio() {
  if (!bus) bus = new AudioBus();
  let timer;
  try {
    await Promise.race([bus.context.resume(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('La sortie audio ne démarre pas. Vérifie la sortie système et l’autorisation de lecture audio du site, puis réessaie.')), 6000);
    })]);
  } finally { clearTimeout(timer); }
}

function waitForAudio(media, signal) {
  return new Promise((resolve, reject) => {
    const finish = error => {
      clearTimeout(timer);
      media.removeEventListener('canplay', playable);
      media.removeEventListener('error', failed);
      signal.removeEventListener('abort', aborted);
      error ? reject(error) : resolve();
    };
    const playable = () => finish();
    const failed = () => finish(new Error('Fichier audio illisible ou lien de lecture indisponible.'));
    const aborted = () => finish(new DOMException('Chargement annulé', 'AbortError'));
    const timer = setTimeout(() => finish(new Error('Le serveur audio ne répond pas. Réessaie le chargement.')), 30000);
    media.addEventListener('canplay', playable);
    media.addEventListener('error', failed);
    signal.addEventListener('abort', aborted, { once: true });
    if (signal.aborted) aborted();
    else if (media.error) failed();
    else if (media.readyState >= 3) playable();
  });
}

class Deck {
  constructor(index) {
    this.index = index;
    this.letter = index ? 'B' : 'A';
    this.version = 0;
    this.ready = false;
    this.track = null;
    this.wave = null;
    this.nodes = [];
    this.abort = null;
    this.rate = 1;
    this.bendTimer = null;
    this.shift = false;
    this.cue = 0;
    this.hotcues = Array(8).fill(null);
    this.hotcueDetails = Array.from({ length: 8 }, () => ({}));
    this.loopStart = null;
    this.loopEnd = null;
    this.looping = false;
    this.headphone = false;
    this.analysisData = null;
    this.analysisLoading = false;
    this.el = document.createElement('section');
    this.el.className = 'mix-deck';
    this.el.dataset.deck = index;
    this.el.setAttribute('aria-label', `Platine ${this.letter}`);
    this.el.innerHTML = `
      <div class="mix-deck-heading"><span class="mix-deck-letter">${this.letter}</span><div class="mix-cover">?</div><div class="mix-track-info"><strong>Aucun morceau</strong><span></span></div></div>
      <div class="mix-deck-status" role="status">Vide</div>
      <div class="mix-overview"><div class="mix-wave"></div><div class="mix-cue-markers"></div></div>
      <div class="mix-time"><span data-time>0:00</span><span data-duration>0:00</span></div>
      <div class="mix-transport"></div>
      <div class="mix-eq"></div><div class="mix-levels"></div>
      <div class="mix-tempo"><label>Tempo <output class="mix-value">+0.00 %</output><input data-control="tempo" type="range" min="-16" max="16" step="0.01" value="0" aria-label="Tempo ${this.letter}"></label><label>BPM<input data-control="bpm" type="number" min="40" max="300" step="0.001" placeholder="BPM" aria-label="BPM original ${this.letter}"></label></div>
      <div class="mix-options"><label><input data-control="keylock" type="checkbox" checked> Master tempo</label><label><input data-control="quantize" type="checkbox" checked> Quantize</label><span class="mix-key"></span></div>
      <div class="mix-grid-editor" popover="auto" role="dialog" id="deck-analysis-${index}" aria-labelledby="deck-analysis-title-${index}"><header class="mix-grid-heading"><span class="mix-deck-letter">${this.letter}</span><strong id="deck-analysis-title-${index}">Grille et analyse</strong></header><div class="mix-grid-track"></div><div class="mix-grid-tools"><label>Premier temps (s)<input data-control="grid" type="number" min="0" step="0.001" value="0"></label><label>Clé<select data-control="key"><option value="">Automatique</option></select></label></div><div class="mix-analysis-info" role="status"></div></div>
      <div class="mix-pads" aria-label="Hot cues ${this.letter}"></div>
      <div class="mix-cue-details" id="deck-cue-details-${index}"><div class="mix-cue-comments" aria-label="Commentaires des hot cues ${this.letter}"></div></div>`;
    $('#mix-decks').append(this.el);
    this.buttons = {};
    const controls = [
      ['play', 'Lire / Pause', Play, () => this.toggle()],
      ['cue', 'Cue : poser à l’arrêt, revenir en lecture', Flag, () => this.cueAction()],
      ['start', 'Revenir au début', SkipBack, () => this.seek(0)],
      ['headphone', 'Préécoute casque', Headphones, () => this.toggleHeadphone()],
      ['loopIn', 'Début de boucle', LogIn, () => this.setLoopIn()],
      ['loopOut', 'Fin de boucle', LogOut, () => this.setLoopOut()],
      ['loop', 'Activer / désactiver la boucle', Repeat2, () => this.toggleLoop()],
    ];
    for (const [name, label, shape, handler] of controls) {
      const el = button(name, `${label} (${this.letter})`, shape, handler);
      this.buttons[name] = el;
      $('.mix-transport', this.el).append(el);
    }
    $('.mix-deck-heading', this.el).append(button('eject', `Vider la platine ${this.letter}`, X, () => this.eject()));
    const bpmLabel = document.createElement('span'); bpmLabel.className = 'mix-current-bpm';
    $('.mix-deck-heading', this.el).insertBefore(bpmLabel, $('[data-action="eject"]', this.el));
    for (const [name, label, min, max, value, group] of [
      ['low', 'Graves', -24, 12, 0, '.mix-eq'], ['mid', 'Médiums', -24, 12, 0, '.mix-eq'], ['high', 'Aigus', -24, 12, 0, '.mix-eq'],
      ['trim', 'Gain', 0, 2, 1, '.mix-levels'], ['volume', 'Volume', 0, 1, 1, '.mix-levels'], ['filter', 'Filtre', -1, 1, 0, '.mix-levels'],
    ]) {
      const labelEl = document.createElement('label');
      labelEl.textContent = label;
      const range = document.createElement('input');
      Object.assign(range, { type: 'range', min, max, step: '0.01', value });
      range.dataset.control = name;
      range.setAttribute('aria-label', `${label} ${this.letter}`);
      labelEl.append(range);
      $(group, this.el).append(labelEl);
      range.addEventListener('input', () => this.applySound());
      range.addEventListener('dblclick', () => { range.value = value; this.applySound(); });
    }
    this.control('tempo').addEventListener('input', () => this.setTempo(Number(this.control('tempo').value)));
    this.control('tempo').addEventListener('dblclick', () => this.setTempo(0));
    this.control('keylock').addEventListener('change', () => this.applyRate());
    $('.mix-tempo', this.el).append(button('matchTempo', `Synchroniser tempo et phase sur ${index ? 'A' : 'B'}`, Gauge, () => this.matchTempo()));
    this.control('bpm').addEventListener('change', () => run(() => saveSettings(this.track.job_id, { bpm: Number(this.control('bpm').value) || null })));
    this.control('grid').addEventListener('change', () => run(() => saveSettings(this.track.job_id, { grid_offset: Number(this.control('grid').value) })));
    const notes = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B'];
    for (const key of [...notes, ...notes.map(n => n + 'm')]) this.control('key').append(new Option(key, key));
    this.control('key').addEventListener('change', () => run(() => saveSettings(this.track.job_id, { key: this.control('key').value || null })));
    const tools = $('.mix-grid-tools', this.el);
    tools.prepend(this.control('bpm').parentElement);
    const modeLabel = document.createElement('label'); modeLabel.textContent = 'Type d’analyse';
    const mode = document.createElement('select'); mode.dataset.control = 'tempoMode';
    mode.setAttribute('aria-label', `Type d’analyse ${this.letter}`);
    mode.append(new Option('Tempo fixe', 'fixed'), new Option('Tempo variable', 'variable'));
    mode.addEventListener('change', () => run(async () => {
      const job = this.track?.job_id;
      if (!job) return;
      try { await saveSettings(job, { tempo_mode: mode.value }); }
      catch (error) {
        if (this.track?.job_id === job) mode.value = this.analysisData?.settings?.tempo_mode || 'fixed';
        throw error;
      }
    }));
    modeLabel.append(mode); tools.prepend(modeLabel);
    tools.append(button('anchor', 'Aligner un début de mesure sur la tête de lecture', Flag, () => saveSettings(this.track.job_id, { grid_offset: this.position() })),
      button('gridBack', 'Reculer la grille de 10 ms', Minus, () => this.shiftGrid(-.01)),
      button('gridForward', 'Avancer la grille de 10 ms', Plus, () => this.shiftGrid(.01)),
      button('analyze', 'Relancer l’analyse du morceau', RefreshCw, () => this.reanalyze()));
    for (const [label, factor] of [['BPM / 2', .5], ['BPM × 2', 2]]) {
      const el = document.createElement('button'); el.type = 'button'; el.className = 'secondary-button'; el.textContent = label;
      el.dataset.fixedTempo = 'true';
      el.addEventListener('click', () => run(() => saveSettings(this.track.job_id, { bpm: this.grid().bpm * factor })));
      tools.append(el);
    }
    const bpmTools = document.createElement('div'); bpmTools.className = 'mix-grid-adjust';
    bpmTools.append(this.control('bpm').parentElement, ...$$('[data-fixed-tempo]', tools));
    const offsetTools = document.createElement('div'); offsetTools.className = 'mix-grid-adjust';
    offsetTools.append(this.control('grid').parentElement, ...$$('[data-action="gridBack"], [data-action="gridForward"]', tools));
    const anchor = $('[data-action="anchor"]', tools);
    anchor.classList.add('mix-grid-anchor');
    anchor.append(document.createTextNode('Fixer la grille ici'));
    const analyze = $('[data-action="analyze"]', tools);
    analyze.classList.add('mix-analyze-button');
    analyze.append(document.createTextNode('Relancer l’analyse'));
    tools.append(bpmTools, anchor, offsetTools, analyze);
    for (let i = 0; i < 8; i++) {
      const pad = document.createElement('button');
      pad.type = 'button';
      pad.className = 'mix-pad';
      pad.textContent = 'ABCDEFGH'[i];
      pad.dataset.pad = i;
      pad.title = `Hot cue ${i + 1} : poser / rappeler. Maj + clic : effacer.`;
      pad.setAttribute('aria-label', `Hot cue ${i + 1}, platine ${this.letter}`);
      pad.addEventListener('click', e => run(() => this.hotcue(i, e.shiftKey)));
      const cell = document.createElement('div'); cell.className = 'mix-pad-cell';
      cell.style.setProperty('--cue-color', cueColors[i]);
      const remove = button('clearCue', `Supprimer le hot cue ${'ABCDEFGH'[i]} de ${this.letter}`, X, () => this.hotcue(i, true));
      remove.className = 'mix-pad-remove'; remove.dataset.clearPad = i;
      cell.append(pad, remove);
      $('.mix-pads', this.el).append(cell);
      const row = document.createElement('div'); row.className = 'mix-cue-row';
      const label = document.createElement('span'); label.textContent = 'ABCDEFGH'[i];
      row.append(label);
      for (const field of ['comment', 'color']) {
        const input = document.createElement('input');
        input.type = field === 'color' ? 'color' : 'text';
        input.dataset.cueField = field; input.dataset.cueIndex = i;
        input.setAttribute('aria-label', `${field === 'color' ? 'Couleur' : 'Commentaire'} du hot cue ${label.textContent}, platine ${this.letter}`);
        if (field === 'comment') { input.maxLength = 160; input.placeholder = 'Commentaire'; input.autocomplete = 'off'; }
        else { input.value = cueColors[i]; input.title = `Changer la couleur du hot cue ${label.textContent}`; }
        let saving;
        input.addEventListener('input', () => { input.dataset.dirty = 'true'; });
        input.addEventListener('change', () => {
          const job = input.dataset.job, value = input.value;
          if (!job || (saving?.job === job && saving.value === value)) return;
          const edit = { job, value }; saving = edit;
          run(async () => {
            try {
              await saveSettings(job, { hotcue: { index: i, [field]: value } });
              if (input.dataset.job === job && input.value === value) {
                delete input.dataset.dirty; input.removeAttribute('aria-invalid');
              }
            } catch (error) {
              if (input.dataset.job === job) input.setAttribute('aria-invalid', 'true');
              throw error;
            } finally { if (saving === edit) saving = null; }
          });
        });
        if (field === 'comment') input.addEventListener('keydown', event => {
          if (event.key === 'Enter') { event.preventDefault(); input.dispatchEvent(new Event('change')); input.blur(); }
        });
        row.append(input);
      }
      $('.mix-cue-comments', this.el).append(row);
    }
    this.el.addEventListener('dragover', e => {
      if (!Array.from(e.dataTransfer.types).includes('application/x-tunesaver-job')) return;
      e.preventDefault();
      this.el.dataset.drop = 'true';
    });
    this.el.addEventListener('dragleave', () => { this.el.dataset.drop = 'false'; });
    this.el.addEventListener('drop', e => {
      this.el.dataset.drop = 'false';
      const track = host().library.tracks.find(t => t.job_id === e.dataTransfer.getData('application/x-tunesaver-job'));
      if (!track?.streamable) return;
      e.preventDefault();
      run(() => this.load(track));
    });
    this.timeline = new Timeline($('#mix-timelines'), this);
    this.compactLayout();
    $$('input[type="range"]', this.el).forEach(makeKnob);
    this.update();
  }

  compactLayout() {
    const heading = $('.mix-deck-heading', this.el);
    const overview = $('.mix-overview', this.el);
    this.overview = overview;
    overview.dataset.deck = this.index;
    overview.tabIndex = 0;
    overview.setAttribute('role', 'slider');
    overview.setAttribute('aria-label', `Aperçu complet du morceau (${this.letter})`);
    overview.setAttribute('aria-valuemin', '0');
    overview.addEventListener('keydown', event => {
      if (!['Home', 'End', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      this.seek(event.key === 'Home' ? 0 : event.key === 'End' ? this.wave?.getDuration() || 0
        : this.position() + (event.key === 'ArrowRight' ? 5 : -5));
    });
    overview.append($('.mix-time', this.el));
    const label = document.createElement('div'); label.className = 'mix-overview-label';
    const letter = document.createElement('strong'); letter.textContent = this.letter;
    this.overviewTitle = document.createElement('span'); this.overviewTitle.textContent = 'Aucun morceau';
    label.append(letter, this.overviewTitle); overview.prepend(label);
    const transport = document.createElement('div'); transport.className = 'deck-playback';
    if (!this.index) transport.append(button('previous', 'Morceau précédent', SkipBack, () => host().playPreviousTrack()));
    transport.append(this.buttons.play);
    if (!this.index) transport.append(button('next', 'Morceau suivant', SkipForward, () => host().playNextTrack()));
    else transport.append(this.buttons.cue);
    const panel = document.createElement('div'); panel.className = 'deck-controls';
    panel.id = `deck-controls-${this.index}`; panel.setAttribute('popover', 'auto');
    panel.setAttribute('aria-label', `Réglages de la platine ${this.letter}`);
    const panelHeading = document.createElement('header'); panelHeading.className = 'deck-controls-heading';
    const panelTitle = document.createElement('strong'); panelTitle.textContent = `Platine ${this.letter}`;
    const side = document.createElement('span'); side.textContent = this.index ? 'Droite' : 'Gauche';
    panelHeading.append(panelTitle, side);
    this.controlsTitle = document.createElement('div'); this.controlsTitle.className = 'deck-controls-track';
    panel.append(panelHeading, this.controlsTitle);
    const analysis = $('.mix-grid-editor', this.el);
    const settings = button('settings', `Grille et analyse (${this.letter})`, Settings2, () => {});
    settings.setAttribute('popovertarget', analysis.id);
    settings.setAttribute('aria-controls', analysis.id);
    settings.setAttribute('aria-expanded', 'false');
    analysis.addEventListener('toggle', () => settings.setAttribute('aria-expanded', String(analysis.matches(':popover-open'))));
    const commands = button('deckCommands', `Commandes de la platine ${this.letter}`, Gauge, () => analysis.hidePopover());
    commands.setAttribute('popovertarget', panel.id);
    const dismiss = button('closeAnalysis', 'Fermer les réglages', X, () => analysis.hidePopover());
    $('.mix-grid-heading', analysis).append(commands, dismiss);
    const mute = button('mute', `Couper le son (${this.letter})`, Volume2, () => {
      const media = this.wave?.getMediaElement();
      if (media) { media.muted = !media.muted; this.update(); }
    });
    this.buttons.mute = mute;
    const tools = document.createElement('div'); tools.className = 'deck-tools'; tools.append(mute, settings);
    const key = $('.mix-key', this.el), bpm = $('.mix-current-bpm', this.el);
    const metadata = document.createElement('div'); metadata.className = 'deck-metadata'; metadata.append(bpm, key);
    $('.mix-track-info', this.el).append(metadata);
    const eject = $('[data-action="eject"]', this.el);
    panelHeading.append(eject);
    const status = $('.mix-deck-status', this.el);
    for (const child of Array.from(this.el.children)) if (![heading, overview, status, analysis].includes(child)) panel.append(child);
    const lane = this.timeline.lane;
    lane.append(status);
    this.el.append(heading, transport, lane, tools, panel, analysis);
    $('#mix-overviews').append(overview);
  }

  control(name) { return $(`[data-control="${name}"]`, this.el); }
  position() { return this.wave?.getCurrentTime() || 0; }
  playing() { return this.wave?.isPlaying() || false; }

  clearAudio() {
    this.version++;
    this.abort?.abort();
    clearTimeout(this.bendTimer);
    clearTimeout(this.analysisDelay);
    const media = this.wave?.getMediaElement();
    this.wave?.pause();
    this.wave?.destroy();
    // WaveSurfer does not reset an externally supplied media element on destroy.
    if (media) { media.removeAttribute('src'); media.load(); }
    this.wave = null;
    this.nodes.forEach(node => node.disconnect());
    this.nodes = [];
    this.ready = false;
    this.loopStart = this.loopEnd = null;
    this.looping = false;
    this.headphone = false;
    this.analysisData = null;
    this.analysisLoading = false;
  }

  async load(track) {
    if (!host().signedIn || !track.streamable) return;
    if (this.ready && this.track?.job_id === track.job_id) return true;
    const previousVersion = this.version;
    if ((active || this.index) && this.playing() && !await window.TuneSaverDialogs.confirm(`Remplacer le morceau en lecture sur ${this.letter} ?`, { confirmText: 'Remplacer' })) return;
    if (!host().signedIn || previousVersion !== this.version) return;
    this.clearAudio();
    const version = this.version;
    this.track = track;
    if (!this.index) host().setCurrentTrack(track);
    this.abort = new AbortController();
    const signal = this.abort.signal;
    this.hotcues = [...(cueMemory.get(track.job_id)?.hotcues || Array(8).fill(null))];
    this.hotcueDetails = structuredClone(cueMemory.get(track.job_id)?.hotcue_details || Array.from({ length: 8 }, () => ({})));
    this.cue = cueMemory.get(track.job_id)?.cue || 0;
    $('.mix-cover', this.el).replaceWith(cover(track));
    $('.mix-track-info strong', this.el).textContent = track.title || track.file_name || 'Sans titre';
    this.overviewTitle.textContent = track.title || track.file_name || 'Sans titre';
    this.overviewTitle.title = [this.overviewTitle.textContent, track.artist].filter(Boolean).join(' · ');
    $('.mix-track-info span', this.el).textContent = track.artist || '';
    $('.mix-deck-status', this.el).textContent = 'Chargement…';
    const summary = { bpm: track.bpm, ...track.mix_analysis };
    this.applyAnalysis({ status: 'pending', analysis: summary, settings: {
      tempo_mode: summary.tempo_mode || 'fixed', cue: this.cue, hotcues: this.hotcues, hotcue_details: this.hotcueDetails,
    } });
    try {
      await ensureAudio();
      if (version !== this.version) return;
      const { url } = await host().requestJson('/api/stream-ticket', {
        method: 'POST', body: JSON.stringify({ job_id: track.job_id }), signal,
      });
      if (version !== this.version) return;
      $('.mix-deck-status', this.el).textContent = 'Préparation audio…';
      const media = new Audio();
      media.preload = 'auto';
      media.addEventListener('durationchange', () => {
        if (version === this.version) $('[data-duration]', this.overview).textContent = time(this.wave?.getDuration() || 0);
      }, { signal });
      this.wave = WaveSurfer.create({
        container: $('.mix-wave', this.overview), media, height: 32,
        waveColor: '#626262', progressColor: this.index ? '#ef8eb3' : '#00d79d', cursorColor: '#ffffff',
        dragToSeek: true, normalize: true,
        cspNonce: $('meta[name="mixer-style-nonce"]').content,
        renderFunction: overviewRenderer(this.analysisData?.analysis),
      });
      const ctx = bus.context;
      this.source = ctx.createMediaElementSource(media);
      this.trim = ctx.createGain();
      this.low = new BiquadFilterNode(ctx, { type: 'lowshelf', frequency: 250 });
      this.mid = new BiquadFilterNode(ctx, { type: 'peaking', frequency: 1000, Q: 0.7 });
      this.high = new BiquadFilterNode(ctx, { type: 'highshelf', frequency: 4000 });
      this.highpass = new BiquadFilterNode(ctx, { type: 'highpass', frequency: 10, Q: 0.5 });
      this.lowpass = new BiquadFilterNode(ctx, { type: 'lowpass', frequency: 22000, Q: 0.5 });
      this.fader = ctx.createGain();
      this.cross = ctx.createGain();
      this.cueGain = ctx.createGain();
      const chain = [this.source, this.trim, this.low, this.mid, this.high, this.highpass, this.lowpass, this.fader, this.cross];
      chain.forEach((node, i) => { if (i) chain[i - 1].connect(node); });
      this.cross.connect(bus.master);
      this.lowpass.connect(this.cueGain);
      this.cueGain.connect(bus.head);
      this.nodes = [...chain, this.cueGain];
      this.wave.on('play', () => this.update());
      this.wave.on('pause', () => this.update());
      this.wave.on('timeupdate', () => this.tick());
      this.wave.on('finish', () => {
        if (this.ready && this.looping) run(() => this.wave?.play(this.loopStart));
        else if (!this.index && !active) run(() => host().playNextTrack());
        this.update();
      });
      this.wave.on('error', () => {
        if (version !== this.version) return;
        this.ready = false;
        this.wave.pause();
        $('.mix-deck-status', this.el).textContent = 'Fichier audio illisible.';
        this.update();
      });
      this.applySound();
      // Blank peaks bypass WaveSurfer's whole-file fetch/decode. Real waveform
      // data arrives separately; the media element streams and seeks by range.
      const duration = Number(summary.duration);
      await Promise.all([
        this.wave.load(url, [new Float32Array(2)], duration > 0 && Number.isFinite(duration) ? duration : undefined),
        waitForAudio(media, signal),
      ]);
      if (version !== this.version) return;
      this.ready = true;
      this.applySound();
      this.applyRate();
      $('.mix-deck-status', this.el).textContent = 'Prêt';
      $('[data-duration]', this.overview).textContent = time(this.wave.getDuration());
      this.update();
      this.analysisDelay = setTimeout(() => {
        if (version === this.version) run(() => this.fetchAnalysis());
      }, 0);
      return true;
    } catch (e) {
      if (version !== this.version) return;
      this.clearAudio();
      $('.mix-deck-status', this.el).textContent = e.name === 'AbortError' ? 'Chargement annulé' : e.message || 'Chargement impossible.';
      this.update();
    }
  }

  grid(position = this.position()) {
    const analysis = this.analysisData?.analysis || {};
    const settings = this.analysisData?.settings || {};
    if (usesVariableGrid(analysis, settings)) {
      const detected = detectedGrid(analysis.beats, position);
      if (detected) return detected;
    }
    const value = Number(this.control('bpm').value);
    const bpm = Number.isFinite(value) && value >= 40 && value <= 300 ? value : null;
    return { bpm, offset: settings.grid_offset ?? analysis.grid_offset ?? 0 };
  }

  async fetchAnalysis() {
    if (!this.track || this.analysisLoading || saves.has(this.track.job_id)) return;
    const version = this.version;
    this.analysisLoading = true;
    try {
      const data = await host().requestJson(`/api/mixer/tracks/${encodeURIComponent(this.track.job_id)}`, { method: 'GET', signal: this.abort?.signal });
      if (version === this.version && !saves.has(this.track.job_id)) this.applyAnalysis(data);
    } catch (error) {
      if (version === this.version && error.name !== 'AbortError') {
        $('.mix-analysis-info', this.el).textContent = 'Analyse indisponible. Réessaie depuis les réglages.';
      }
    } finally { if (version === this.version) this.analysisLoading = false; }
  }

  applyAnalysis(data) {
    const previousWaveform = this.analysisData?.analysis?.waveform;
    this.analysisData = data;
    const a = data.analysis || {}, s = data.settings || {};
    this.control('bpm').value = s.bpm ?? a.bpm ?? '';
    this.control('tempoMode').value = s.tempo_mode || 'fixed';
    this.control('grid').value = (s.grid_offset ?? a.grid_offset ?? 0).toFixed(3);
    this.control('key').value = s.key || '';
    this.cue = s.cue ?? 0;
    this.hotcues = [...(s.hotcues || Array(8).fill(null))];
    this.hotcueDetails = structuredClone(s.hotcue_details || Array.from({ length: 8 }, () => ({})));
    if (this.track) cueMemory.set(this.track.job_id, { cue: this.cue, hotcues: [...this.hotcues], hotcue_details: structuredClone(this.hotcueDetails) });
    const key = s.key || a.key;
    $('.mix-key', this.el).textContent = key ? `${key}${!s.key && a.camelot ? ` · ${a.camelot}` : ''}` : 'Clé —';
    const mode = s.tempo_mode === 'variable' ? 'Tempo variable' : 'Tempo fixe';
    const quality = `${mode} · ${a.grid_quality === 'uncertain' || !a.bpm ? 'rythme incertain' : a.grid_quality === 'variable' ? 'grille à vérifier' : 'grille estimée'}`;
    $('.mix-analysis-info', this.el).textContent = data.status === 'done'
      ? [a.engine, quality, a.grid_error_ms != null ? `Écart de grille P90 : ${a.grid_error_ms} ms` : '', key ? s.key ? 'Clé corrigée' : 'Clé estimée' : 'Clé indéterminée', a.codec?.toUpperCase(), a.sample_rate ? `${(a.sample_rate / 1000).toFixed(1)} kHz` : '', a.bit_rate ? `${Math.round(a.bit_rate / 1000)} kb/s` : ''].filter(Boolean).join(' · ')
      : data.status === 'failed' ? data.error || 'Analyse échouée' : data.status === 'running' ? 'Analyse en cours…' : 'Analyse en attente';
    if (a.waveform?.data && a.waveform.data !== previousWaveform?.data && this.wave) this.wave.setOptions({ renderFunction: overviewRenderer(a) });
    this.update();
  }

  async reanalyze() {
    if (!this.track) return;
    const version = this.version;
    const data = await host().requestJson(`/api/mixer/tracks/${encodeURIComponent(this.track.job_id)}`, { method: 'POST', body: '{}' });
    if (version === this.version) this.applyAnalysis(data);
  }

  shiftGrid(delta) {
    return saveSettings(this.track.job_id, { grid_offset: Math.max(0, this.grid().offset + delta) });
  }

  quantize(position) {
    const grid = this.grid(position);
    if (!this.control('quantize').checked || !grid.bpm) return position;
    const step = 60 / grid.bpm;
    return clamp(grid.offset + Math.round((position - grid.offset) / step) * step, 0, this.wave?.getDuration() || position);
  }

  applySound() {
    refreshKnobs();
    if (!this.nodes.length) return;
    for (const name of ['low', 'mid', 'high', 'trim']) bus.set(this[name].gain, Number(this.control(name).value));
    bus.set(this.fader.gain, Number(this.control('volume').value));
    bus.set(this.cross.gain, $('#mix-cross-enabled').checked ? crossfade(Number($('#mix-cross').value))[this.index] : 1);
    bus.set(this.cueGain.gain, this.headphone && bus.split ? 1 - Number($('#mix-head-mix').value) : 0);
    const filter = Number(this.control('filter').value);
    bus.set(this.lowpass.frequency, filter < 0 ? 22000 * Math.pow(0.002, -filter) : 22000);
    bus.set(this.highpass.frequency, filter > 0 ? 10 * Math.pow(1500, filter) : 10);
  }

  setTempo(percent) {
    this.rate = 1 + clamp(percent, -16, 16) / 100;
    this.control('tempo').value = ((this.rate - 1) * 100).toFixed(2);
    this.updateTempoLabel();
    this.applyRate();
  }

  updateTempoLabel() {
    const bpm = this.grid().bpm;
    $('.mix-value', this.el).textContent = `${this.rate >= 1 ? '+' : ''}${((this.rate - 1) * 100).toFixed(2)} %${bpm ? ` · ${(bpm * this.rate).toFixed(2)} BPM` : ''}`;
    $('.mix-current-bpm', this.el).textContent = bpm ? `${(bpm * this.rate).toFixed(2)}` : '—';
    $('.deck-metadata', this.el)?.setAttribute('title', $('.mix-analysis-info', this.el).textContent);
    refreshKnobs();
  }

  applyRate(bend = 0) {
    this.wave?.setPlaybackRate(clamp(this.rate + bend, 0.5, 2), this.control('keylock').checked);
  }

  async toggle() {
    if (!this.ready) return;
    if (this.playing()) this.wave.pause();
    else { await ensureAudio(); if (this.ready) await this.wave.play(); }
  }

  seek(seconds) { if (this.ready) this.wave.setTime(clamp(seconds, 0, this.wave.getDuration())); }

  cueAction() {
    if (!this.ready) return;
    if (this.playing()) { this.wave.pause(); this.seek(this.cue); }
    else if (this.shift) this.seek(this.cue);
    else { this.cue = this.quantize(this.position()); this.remember({ cue: this.cue }); }
    this.update();
  }

  toggleHeadphone() {
    if (!bus?.split) { message('Préécoute indisponible : sélectionne une sortie à quatre canaux dans les réglages audio.'); return; }
    this.headphone = !this.headphone;
    this.applySound();
    this.update();
  }

  remember(patch) {
    if (!this.track) return;
    cueMemory.set(this.track.job_id, { cue: this.cue, hotcues: [...this.hotcues], hotcue_details: structuredClone(this.hotcueDetails) });
    const job = this.track.job_id;
    run(() => saveSettings(job, patch));
  }

  async hotcue(pad, clear = false) {
    if (!this.ready) return;
    if (clear) { this.hotcues[pad] = null; this.hotcueDetails[pad] = {}; }
    else if (this.hotcues[pad] === null) this.hotcues[pad] = this.quantize(this.position());
    else this.seek(this.hotcues[pad]);
    this.remember({ hotcue: { index: pad, time: this.hotcues[pad] } });
    this.update();
  }

  setLoopIn() {
    if (!this.ready) return;
    this.loopStart = this.quantize(this.position()); this.loopEnd = null; this.looping = false; this.update();
  }
  setLoopOut() {
    if (!this.ready || this.loopStart === null || this.position() - this.loopStart < 0.25) {
      message('Place le début de boucle avant sa fin (au moins 0,25 seconde).'); return;
    }
    this.loopEnd = this.quantize(this.position());
    if (this.loopEnd <= this.loopStart) { this.loopEnd = null; message('La fin de boucle doit suivre son début.'); return; }
    this.looping = true; this.seek(this.loopStart); this.update();
  }
  toggleLoop() {
    if (this.loopEnd === null) { message('Place les points de début et de fin de boucle.'); return; }
    this.looping = !this.looping;
    if (this.looping) this.seek(this.loopStart);
    this.update();
  }

  matchTempo() {
    const other = decks[1 - this.index];
    if (!this.ready || !other.ready) return;
    const bpm = this.grid().bpm;
    const target = other.grid().bpm * other.rate;
    if (!(bpm >= 40 && bpm <= 300 && target > 0)) { message('Renseigne le BPM original des deux morceaux pour aligner leur tempo.'); return; }
    const percent = (target / bpm - 1) * 100;
    if (Math.abs(percent) > 16) { message('Écart de tempo supérieur à la plage de ±16 %.'); return; }
    this.setTempo(percent);
    this.seek(phaseAlignedTime(this.position(), this.grid(), other.position(), other.grid(), this.wave.getDuration()));
  }

  jog(value, search = false) {
    if (!this.ready || !value) return;
    if (!this.playing() || search) this.seek(this.position() + value * (search ? 0.25 : 0.02));
    else {
      this.applyRate(clamp(value * 0.004, -0.12, 0.12));
      clearTimeout(this.bendTimer);
      this.bendTimer = setTimeout(() => this.applyRate(), 120);
    }
  }

  tick() {
    if (this.ready && this.looping && this.position() >= this.loopEnd) this.seek(this.loopStart);
    $('[data-time]', this.overview).textContent = time(this.position());
    const overview = this.overview;
    overview.setAttribute('aria-valuemax', String(this.wave?.getDuration() || 0));
    overview.setAttribute('aria-valuenow', String(this.position()));
    overview.setAttribute('aria-valuetext', `${time(this.position())} sur ${time(this.wave?.getDuration() || 0)}`);
    overview.setAttribute('aria-disabled', String(!this.ready));
    this.updateNavigation();
    if (usesVariableGrid(this.analysisData?.analysis, this.analysisData?.settings)) this.updateTempoLabel();
  }

  update() {
    this.el.dataset.ready = String(this.ready);
    this.controlsTitle.textContent = this.track
      ? [this.track.title || this.track.file_name, this.track.artist].filter(Boolean).join(' · ') : 'Aucun morceau';
    this.controlsTitle.title = this.controlsTitle.textContent;
    const analysisTitle = $('.mix-grid-track', this.el);
    analysisTitle.textContent = this.controlsTitle.textContent;
    analysisTitle.title = this.controlsTitle.textContent;
    this.updateTempoLabel();
    for (const el of Object.values(this.buttons)) el.disabled = !this.ready;
    this.buttons.headphone.disabled = !this.ready || !bus?.split;
    this.buttons.play.replaceChildren(icon(this.playing() ? Pause : Play));
    this.buttons.play.setAttribute('aria-pressed', String(this.playing()));
    this.buttons.mute.replaceChildren(icon(this.wave?.getMediaElement().muted ? VolumeX : Volume2));
    this.buttons.mute.setAttribute('aria-pressed', String(Boolean(this.wave?.getMediaElement().muted)));
    this.buttons.headphone.setAttribute('aria-pressed', String(this.headphone && Boolean(bus?.split)));
    this.buttons.loop.setAttribute('aria-pressed', String(this.looping));
    this.buttons.loopIn.setAttribute('aria-pressed', String(this.loopStart !== null));
    this.buttons.loopOut.setAttribute('aria-pressed', String(this.loopEnd !== null));
    $$('.mix-pad', this.el).forEach((pad, i) => {
      pad.disabled = !this.ready; pad.dataset.set = String(this.hotcues[i] !== null);
      pad.textContent = 'ABCDEFGH'[i] + (this.hotcues[i] !== null ? ` ${time(this.hotcues[i])}` : '');
      pad.parentElement.style.setProperty('--cue-color', this.cueColor(i));
      pad.parentElement.style.setProperty('--cue-text', cueTextColor(this.cueColor(i)));
      pad.title = [`Hot cue ${'ABCDEFGH'[i]}`, this.hotcueDetails[i]?.comment].filter(Boolean).join(' · ');
      $(`[data-clear-pad="${i}"]`, this.el).disabled = !this.ready || this.hotcues[i] === null;
    });
    $$('[data-cue-field]', this.el).forEach(input => {
      const job = this.track?.job_id || '', index = Number(input.dataset.cueIndex);
      if (input.dataset.job !== job || (!input.dataset.dirty && document.activeElement !== input)) {
        input.value = input.dataset.cueField === 'color' ? this.cueColor(index) : this.hotcueDetails[index]?.comment || '';
        delete input.dataset.dirty; input.removeAttribute('aria-invalid');
      }
      input.dataset.job = job; input.disabled = !this.ready;
      if (input.dataset.cueField === 'comment') input.title = input.value;
    });
    this.renderMarkers();
    this.control('bpm').disabled = !this.track;
    $$('.mix-grid-tools input, .mix-grid-tools select, .mix-grid-tools button', this.el).forEach(el => { el.disabled = !this.track; });
    $('[data-action="anchor"]', this.el).disabled = !this.ready;
    if (this.analysisData?.settings?.tempo_mode === 'variable') {
      $$('[data-control="bpm"], [data-control="grid"], [data-action="anchor"], [data-action="gridBack"], [data-action="gridForward"], [data-fixed-tempo]', this.el).forEach(el => { el.disabled = true; });
    }
    if (this.ready) $('.mix-deck-status', this.el).textContent = this.looping ? 'Boucle active' : this.playing() ? 'En lecture' : 'Prêt';
    this.tick();
    sendLEDs(this);
  }

  cueColor(index) { return this.hotcueDetails[index]?.color || cueColors[index]; }

  renderMarkers() {
    const container = $('.mix-cue-markers', this.overview);
    container.replaceChildren();
    const duration = this.wave?.getDuration();
    if (!duration) return;
    // Nearby cues share a label so coincident markers remain legible.
    const groups = [];
    this.hotcues.forEach((t, i) => {
      if (t === null) return;
      const left = Math.min(94, t / duration * 100);
      const group = groups.find(g => Math.abs(g.left - left) < 3);
      if (group) group.indices.push(i);
      else groups.push({ left, t, indices: [i] });
    });
    for (const group of groups) {
      const marker = document.createElement('button'); marker.type = 'button'; marker.className = 'mix-cue-marker';
      marker.textContent = group.indices.map(i => 'ABCDEFGH'[i]).join('');
      marker.style.left = `${group.left}%`; marker.style.setProperty('--cue-color', this.cueColor(group.indices[0]));
      marker.style.setProperty('--cue-text', cueTextColor(this.cueColor(group.indices[0])));
      marker.title = group.indices.map(i => [`Hot cue ${'ABCDEFGH'[i]}`, time(this.hotcues[i]), this.hotcueDetails[i]?.comment].filter(Boolean).join(' · ')).join('\n');
      marker.setAttribute('aria-label', marker.title);
      marker.addEventListener('click', () => this.seek(group.t));
      container.append(marker);
    }
  }

  updateNavigation() {
    if (this.index) return;
    const tracks = host()?.visibleTracks || [], index = tracks.findIndex(t => t.job_id === this.track?.job_id);
    $('[data-action="next"]', this.el).disabled = !tracks.slice(index + 1).some(t => t.streamable);
    $('[data-action="previous"]', this.el).disabled = this.position() <= 3 && !tracks.slice(0, Math.max(0, index)).some(t => t.streamable);
  }

  async eject(force = false) {
    const version = this.version;
    if (!force && this.playing() && !await window.TuneSaverDialogs.confirm(`Vider la platine ${this.letter} en lecture ?`, { confirmText: 'Vider' })) return;
    if (version !== this.version) return;
    this.clearAudio();
    this.track = null;
    if (!this.index) host()?.setCurrentTrack(null);
    this.control('bpm').value = '';
    this.control('tempoMode').value = 'fixed';
    this.hotcues = Array(8).fill(null);
    this.hotcueDetails = Array.from({ length: 8 }, () => ({}));
    this.cue = 0;
    $('.mix-cover', this.el).replaceWith(cover(null));
    $('.mix-track-info strong', this.el).textContent = 'Aucun morceau';
    this.overviewTitle.textContent = 'Aucun morceau';
    this.overviewTitle.removeAttribute('title');
    $('.mix-track-info span', this.el).textContent = '';
    $('.mix-deck-status', this.el).textContent = 'Vide';
    $('.mix-analysis-info', this.el).textContent = '';
    $('.mix-key', this.el).textContent = '';
    $('[data-duration]', this.overview).textContent = '0:00';
    this.update();
  }
}

function $$(selector, root = document) { return Array.from(root.querySelectorAll(selector)); }

function selectTrack(id) {
  host().selectTrack(id);
  setBrowserFocus('tracks');
}

function collectionButtons() {
  return $$('.library-nav-content [data-collection]').filter(el => !el.closest('[hidden]'));
}

function browse(delta, focus = false) {
  if (!delta) return;
  if (browserFocus === 'collections' && !focus) {
    const buttons = collectionButtons();
    if (!buttons.length) return;
    const current = buttons.findIndex(el => el.dataset.collection === collectionCursor);
    const target = buttons[clamp(current + Math.sign(delta), 0, buttons.length - 1)];
    collectionCursor = target.dataset.collection;
    updateBrowserFocus();
    host().ensureVisible(target);
    return;
  }
  host().showLibrary();
  const tracks = host().visibleTracks.filter(track => track.streamable);
  if (!tracks.length) return;
  const index = tracks.findIndex(t => t.job_id === host().selectedTrackId);
  selectTrack(tracks[clamp(index + Math.sign(delta), 0, tracks.length - 1)].job_id);
  const row = $('.library-track[data-selected="true"]');
  host().ensureVisible(row);
  if (focus) row?.focus({ preventScroll: true });
}

function updateBrowserFocus() {
  $('.library-layout').dataset.focus = browserFocus;
  collectionButtons().forEach(el => {
    el.dataset.cursor = String(active && browserFocus === 'collections' && el.dataset.collection === collectionCursor);
  });
}

function setBrowserFocus(focus) {
  browserFocus = focus;
  updateBrowserFocus();
}

function browseFocus(back = false) {
  if (back || browserFocus === 'tracks') {
    collectionCursor = host().collectionValue;
    setBrowserFocus('collections');
    host().ensureVisible(collectionButtons().find(el => el.dataset.collection === collectionCursor));
  } else {
    collectionButtons().find(el => el.dataset.collection === collectionCursor)?.click();
    setBrowserFocus('tracks');
    host().ensureVisible($('.library-track[data-selected="true"]'));
  }
}

function sync() {
  decks.forEach(d => d.updateNavigation());
  if (!active) return;
  updateBrowserFocus();
}
function initialize() {
  if (decks.length) return;
  decks = [new Deck(0), new Deck(1)];
  layoutOverviews();
  cancelAnimationFrame(animation);
  animate();
  clearInterval(analysisTimer);
  analysisTimer = setInterval(() => decks.filter(d => d.ready && d.track && !['done', 'failed'].includes(d.analysisData?.status)).forEach(d => run(() => d.fetchAnalysis())), 4000);
}

function layoutOverviews() {
  const primary = decks[0];
  if (!primary) return;
  const target = active ? $('#mix-overviews') : primary.timeline.lane;
  if (primary.overview.parentElement !== target) {
    target.prepend(primary.overview);
    // Redraw at the new width immediately, without touching the media element.
    primary.wave?.setOptions({});
  }
}

function open() {
  if (!host()?.signedIn) return;
  initialize();
  active = true;
  document.body.dataset.mixer = 'true';
  layoutOverviews();
  layoutDeckControls();
  $('#mixer-nav').setAttribute('aria-pressed', 'true');
  $('#mixer-nav').setAttribute('aria-expanded', 'true');
  sync();
}

function close() {
  setConnectionOpen(false);
  active = false;
  $('#mix-controls').hidePopover();
  $('#mixer-nav').setAttribute('aria-pressed', 'false');
  delete document.body.dataset.mixer;
  layoutOverviews();
  $('#mixer-nav').setAttribute('aria-expanded', 'false');
  updateBrowserFocus();
  $$('.deck-controls, .mix-grid-editor').forEach(panel => panel.hidePopover());
  layoutDeckControls();
}

function layoutDeckControls() {
  const connection = $('#mix-connection');
  // Native popover toggle events can arrive after a viewport resize.
  if (connection.hasAttribute('popover')) connectionOpen = connection.matches(':popover-open');
  const docked = active && sideControlsQuery.matches;
  document.body.toggleAttribute('data-side-controls', docked);
  decks.forEach(deck => {
    const panel = $(`#deck-controls-${deck.index}`);
    const mode = docked ? 'manual' : 'auto';
    if (panel.getAttribute('popover') !== mode) {
      panel.hidePopover();
      panel.setAttribute('popover', mode);
    }
    panel.dataset.docked = String(docked);
    if (docked) panel.showPopover();
  });
  layoutConnection();
}

function layoutConnection() {
  const panel = $('#mix-connection');
  const outputs = $('#mix-outputs');
  const docked = active && sideControlsQuery.matches;
  const parent = docked ? $('#deck-cue-details-0') : $('#mix-performance');
  if (panel.parentElement !== parent) {
    if (panel.hasAttribute('popover')) panel.hidePopover();
    parent.append(panel);
  }
  const outputParent = docked ? $('#deck-cue-details-1') : panel;
  if (outputs.parentElement !== outputParent) outputParent.append(outputs);
  outputs.dataset.docked = String(docked);
  outputs.hidden = docked && !connectionOpen;
  $$('.mix-cue-comments').forEach(comments => { comments.hidden = docked && connectionOpen; });
  panel.dataset.docked = String(docked);
  if (docked) {
    $('#mix-connect').removeAttribute('popovertarget');
    panel.removeAttribute('popover');
    panel.hidden = !connectionOpen;
  } else {
    $('#mix-connect').setAttribute('popovertarget', panel.id);
    panel.hidden = false;
    panel.setAttribute('popover', 'auto');
    if (connectionOpen) panel.showPopover();
    else panel.hidePopover();
  }
  $('#mix-connect').setAttribute('aria-expanded', String(connectionOpen));
}

function setConnectionOpen(open) {
  const changed = connectionOpen !== open;
  connectionOpen = open;
  if (!open) $('#mix-controls').hidePopover();
  layoutConnection();
  if (open && changed) void refreshOutputs().catch(error => connectionMessage(error.message));
}

function connectionMessage(text = '') {
  const target = $('#mix-connection-message');
  target.replaceChildren();
  if (!text) return;
  const label = document.createElement('span'); label.textContent = text;
  target.append(label, button('dismissConnection', 'Fermer le message', X, () => connectionMessage()));
  if (host()?.signedIn) setConnectionOpen(true);
}

sideControlsQuery.addEventListener('change', layoutDeckControls);

function pause() { decks.forEach(d => d.wave?.pause()); }

function sendLEDs(deck) {
  if (!midiOutput || midiOutput.state !== 'connected') return;
  const send = (status, key, on) => midiOutput.send([status, key, on ? 127 : 0]);
  try {
    send(0x90 + deck.index, 0x0b, deck.playing());
    send(0x90 + deck.index, 0x0c, deck.ready);
    send(0x90 + deck.index, 0x54, deck.headphone && bus?.split);
    send(0x90 + deck.index, 0x4d, deck.looping);
    send(0x90 + deck.index, 0x1b, true);
    deck.hotcues.forEach((value, i) => send(deck.index ? 0x99 : 0x97, i, value !== null));
  } catch { /* The device may disappear between its state check and send(). */ }
}

function handleMIDI(event) {
  if (!host()?.signedIn || !active) return;
  const cmd = decoder.decode(Array.from(event.data));
  if (!cmd) return;
  const d = decks[cmd.deck];
  if (cmd.action === 'browse') return browse(cmd.value);
  if (cmd.action === 'browseFocus') return browseFocus(cmd.back);
  if (cmd.action === 'load') {
    const track = host().visibleTracks.find(t => t.job_id === host().selectedTrackId && t.streamable);
    if (track) run(() => d.load(track));
    return;
  }
  const global = { cross: '#mix-cross', headMix: '#mix-head-mix' }[cmd.action];
  if (global) { $(global).value = cmd.value; updateMaster(); return; }
  if (cmd.action.startsWith('filter')) {
    const deck = decks[cmd.action === 'filterA' ? 0 : 1];
    deck.control('filter').value = clamp((cmd.raw - 8192) / 8191, -1, 1);
    deck.applySound(); return;
  }
  if (!d) return;
  if (cmd.action === 'shift') { d.shift = cmd.value; return; }
  if (cmd.action === 'hotcue') return run(() => d.hotcue(cmd.pad, cmd.clear));
  if (cmd.action === 'tempo') return d.setTempo((cmd.raw - 8192) / 8192 * 16);
  if (['jog', 'seek'].includes(cmd.action)) return d.jog(cmd.value, cmd.action === 'seek');
  if (['trim', 'volume', 'low', 'mid', 'high'].includes(cmd.action)) {
    let value = cmd.value;
    if (cmd.action === 'trim') value *= 2;
    else if (cmd.action !== 'volume') value = cmd.raw < 8192 ? (cmd.raw / 8192 - 1) * 24 : (cmd.raw - 8192) / 8191 * 12;
    d.control(cmd.action).value = value;
    d.applySound(); return;
  }
  const actions = { play: () => d.toggle(), cue: () => d.cueAction(), start: () => d.seek(0), headphone: () => d.toggleHeadphone(), loopIn: () => d.setLoopIn(), loopOut: () => d.setLoopOut(), loop: () => d.toggleLoop(), matchTempo: () => d.matchTempo() };
  if (actions[cmd.action]) run(actions[cmd.action]);
}

function bindMIDI() {
  const epoch = midiEpoch;
  midiBinding = midiBinding.catch(() => {}).then(() => { if (epoch === midiEpoch) return bindMIDINow(); });
  return midiBinding;
}

function midiReady(port) { return port?.state === 'connected' && port.connection === 'open'; }

function midiAction(label, shape) {
  const button = $('#mix-midi-connect');
  button.replaceChildren(icon(shape), document.createTextNode(label));
  button.setAttribute('aria-label', label);
  button.title = [label, $('#mix-midi-status').textContent].filter(Boolean).join(' · ');
}

function midiStatus(text, connected = false) {
  $('#mix-midi-status').textContent = text;
  $('#mix-midi-status').dataset.connected = String(connected);
  $('#mix-connect').dataset.connected = String(connected);
  $('#mix-connect').title = [$('#mix-connect').getAttribute('aria-label'), text].filter(Boolean).join(' · ');
}

function closeMIDIPort(port) {
  if (!port) return Promise.resolve();
  return new Promise(resolve => {
    const timer = setTimeout(resolve, 750);
    Promise.resolve().then(() => port.close()).catch(() => {}).finally(() => { clearTimeout(timer); resolve(); });
  });
}

function openMIDIPort(port, epoch, timeout) {
  return new Promise((resolve, reject) => {
    let expired = false;
    const timer = setTimeout(() => {
      expired = true;
      if (midiReady(port) && epoch === midiEpoch && midiWanted) resolve(port);
      else reject(new DOMException('Le navigateur ne termine pas l’ouverture du port MIDI.', 'TimeoutError'));
    }, timeout);
    Promise.resolve().then(() => port.open()).then(() => {
      clearTimeout(timer);
      if (expired || epoch !== midiEpoch || !midiWanted) {
        if (port !== midiInput && port !== midiOutput && port !== midiOpening) void closeMIDIPort(port);
        reject(new DOMException('Connexion MIDI annulée.', 'AbortError'));
        return;
      }
      if (!midiReady(port)) reject(new DOMException('Le port MIDI est détecté mais pas ouvert.', 'InvalidStateError'));
      else resolve(port);
    }, error => { clearTimeout(timer); reject(error); });
  });
}

async function bindMIDINow() {
  if (!midiWanted || !midi) return;
  const epoch = midiEpoch;
  const inputs = Array.from(midi.inputs.values()).filter(p => p.state === 'connected');
  const select = $('#mix-midi-input');
  select.replaceChildren(new Option('Détection automatique', ''));
  inputs.forEach((port, i) => select.append(new Option(port.name || `Entrée MIDI ${i + 1}`, port.id)));
  select.value = inputs.some(p => p.id === midiSelection) ? midiSelection : '';
  const input = inputs.find(p => p.id === midiSelection) || inputs.find(isFLX4Port);
  if (input && midiInput === input && midiReady(input)) return;
  midiFailedInput = null;
  const previousInput = midiInput;
  const previousOutput = midiOutput;
  if (previousInput) previousInput.onmidimessage = null;
  midiInput = null;
  midiOutput = null;
  $('#mix-midi-output').textContent = 'Non connectée';
  await closeMIDIPort(previousInput);
  await closeMIDIPort(previousOutput);
  if (epoch !== midiEpoch || !midiWanted) return;
  if (input) {
    midiOpening = input;
    midiStatus(`${input.name || 'FLX4'} détecté : ouverture des commandes MIDI…`);
    try {
      await openMIDIPort(input, epoch, 4000);
    } catch (error) {
      if (epoch !== midiEpoch || !midiWanted) return;
      midiOpening = null;
      midiFailedInput = input;
      void closeMIDIPort(input);
      const reason = error.name === 'TimeoutError' ? 'le navigateur ne répond pas' : `port refusé (${error.name || 'erreur'})`;
      midiStatus(`${input.name || 'FLX4'} détecté, connexion impossible : ${reason}.`);
      connectionMessage('Réessaie avec le bouton Réinitialiser le MIDI. Si le port reste bloqué, ferme les autres onglets et logiciels utilisant le contrôleur, puis rebranche son câble USB de données.');
      return;
    }
    if (epoch !== midiEpoch || !midiWanted) { void closeMIDIPort(input); return; }
    midiOpening = null;
    midiInput = input;
    input.onmidimessage = handleMIDI;
    decoder = new FLX4Decoder();
    midiStatus(`${input.name || 'Entrée MIDI'} connecté`, true);
    const output = Array.from(midi.outputs.values()).find(p => p.state === 'connected' && (p.name === input.name || isFLX4Port(p)));
    if (output) {
      try {
        await openMIDIPort(output, epoch, 1500);
        if (epoch !== midiEpoch || !midiWanted || midiInput !== input || !midiReady(input)) { void closeMIDIPort(output); return; }
        midiOutput = output;
        $('#mix-midi-output').textContent = output.name || 'Voyants du contrôleur';
      } catch {
        if (epoch !== midiEpoch || !midiWanted || midiInput !== input || !midiReady(input)) return;
        void closeMIDIPort(output);
        midiStatus(`${input.name || 'Entrée MIDI'} connecté · voyants indisponibles`, true);
        $('#mix-midi-output').textContent = 'Voyants indisponibles';
      }
    } else $('#mix-midi-output').textContent = 'Aucune sortie MIDI détectée';
    decks.forEach(sendLEDs);
    return;
  }
  const browser = /Firefox\//.test(navigator.userAgent) ? 'Firefox' : 'le navigateur';
  midiStatus(inputs.length ? 'Sélectionne l’entrée MIDI de ton FLX4.' : `Aucune entrée MIDI exposée par ${browser}. Vérifie l’autorisation MIDI de ce site et le port USB de données du contrôleur.`);
}

async function disconnectMIDI() {
  midiWanted = false; midiEpoch++;
  clearInterval(midiTimer);
  if (midi) midi.onstatechange = null;
  if (midiPermission) midiPermission.onchange = null;
  midiPermission = null;
  const ports = new Set([midiInput, midiOutput, midiOpening]);
  if (midiInput) midiInput.onmidimessage = null;
  midiInput = midiOutput = midiOpening = midiFailedInput = midi = null;
  midiAction('Connecter le FLX4', Usb);
  $('#mix-midi-connect').disabled = false;
  $('#mix-midi-output').textContent = 'Non connectée';
  $('#mix-midi-input').replaceChildren(new Option('Détection automatique', ''));
  midiStatus('Non connecté');
  connectionMessage();
  $('#mix-midi-help').hidden = true;
  midiClosing = Promise.all([midiClosing, ...Array.from(ports, closeMIDIPort)]);
  return midiClosing;
}

function midiDenied() {
  const firefox = /Firefox\//.test(navigator.userAgent);
  midiStatus('Accès MIDI refusé par le navigateur.');
  $('#mix-midi-help').hidden = false;
  $('#mix-midi-help').href = firefox ? 'https://support.mozilla.org/en-US/kb/site-permissions-panel' : 'https://support.google.com/chrome/answer/114662';
  connectionMessage(firefox
    ? 'Dans Firefox, ouvre les permissions à gauche de l’adresse du site et retire le blocage MIDI, puis clique sur Réinitialiser le MIDI. Si Firefox propose une extension de permission de site, accepte-la. Le site ne peut pas effacer lui-même un refus enregistré dans le navigateur.'
    : 'Ouvre les paramètres du site depuis l’icône à gauche de son adresse et autorise le MIDI, puis clique sur Réinitialiser le MIDI. Le site ne peut pas effacer lui-même un refus enregistré dans le navigateur.');
}

async function watchMIDIPermission(epoch) {
  try {
    const permission = await navigator.permissions?.query({ name: 'midi', sysex: false });
    if (!permission || epoch !== midiEpoch) return;
    midiPermission = permission;
    permission.onchange = () => {
      if (epoch !== midiEpoch) return;
      if (permission.state === 'denied') {
        void disconnectMIDI();
        midiDenied();
        void watchMIDIPermission(midiEpoch);
      } else if (!midiInput) {
        $('#mix-midi-help').hidden = true;
        midiStatus('Autorisation MIDI modifiée. Réinitialise le MIDI pour te connecter.');
        connectionMessage();
      }
    };
  } catch { /* Some browsers expose Web MIDI without its Permissions API descriptor. */ }
}

async function connectMIDI(closing = midiClosing) {
  if (midiWanted) { await disconnectMIDI(); return; }
  if (!navigator.requestMIDIAccess) { midiStatus('Web MIDI indisponible dans ce navigateur.'); connectionMessage('Ouvre le site HTTPS dans un navigateur compatible MIDI sur ordinateur.'); return; }
  const epoch = ++midiEpoch;
  midiWanted = true;
  $('#mix-midi-help').hidden = true;
  connectionMessage();
  midiAction('Annuler la connexion', Unplug);
  midiStatus('Autorisation MIDI en attente…');
  try {
    const access = await navigator.requestMIDIAccess({ sysex: false });
    await closing;
    if (epoch !== midiEpoch || !host()?.signedIn) return;
    midi = access; midiWanted = true;
    void watchMIDIPermission(epoch);
    midiAction('Déconnecter le MIDI', Unplug);
    const hardwareStates = new Map(Array.from(midi.inputs.values(), port => [port.id, port.state]));
    midi.onstatechange = event => {
      if (epoch !== midiEpoch || !midiWanted || event.port.type !== 'input') return;
      const previousState = hardwareStates.get(event.port.id);
      hardwareStates.set(event.port.id, event.port.state);
      if (event.port.state === 'disconnected' && event.port === midiInput) {
        midiInput.onmidimessage = null;
        midiInput = null;
        midiStatus('FLX4 débranché.');
        run(bindMIDI);
      }
      if (event.port === midiInput && !midiReady(event.port)) {
        midiInput.onmidimessage = null;
        midiFailedInput = midiInput;
        midiInput = null;
        midiStatus('Connexion MIDI interrompue. Réinitialise le MIDI ; la lecture continue.');
      }
      // Opening/closing ports also fires statechange; only hardware changes need rebinding.
      if (event.port.state === 'connected' && previousState !== 'connected') run(bindMIDI);
    };
    await bindMIDI();
    if (epoch !== midiEpoch || !midiWanted) return;
    clearInterval(midiTimer);
    midiTimer = setInterval(() => {
      const inputs = Array.from(midi.inputs.values()).filter(port => port.state === 'connected');
      const candidate = inputs.find(port => port.id === midiSelection) || inputs.find(isFLX4Port);
      if (!midiInput && !midiOpening && (!midiFailedInput || midiFailedInput !== candidate)) run(bindMIDI);
    }, 2000);
  } catch (e) {
    if (epoch !== midiEpoch) return;
    void disconnectMIDI();
    if (['NotAllowedError', 'SecurityError'].includes(e.name)) {
      midiDenied();
      void watchMIDIPermission(midiEpoch);
      return;
    }
    const error = `Impossible d’ouvrir le port MIDI (${e.name || 'erreur'}). Le port peut être occupé ou déconnecté.`;
    midiStatus(error);
    connectionMessage(error);
  }
}

function updateMaster() {
  $('#mix-cross').disabled = !$('#mix-cross-enabled').checked;
  refreshKnobs();
  if (!bus) return;
  bus.set(bus.master.gain, Number($('#mix-master').value));
  bus.set(bus.head.gain, Number($('#mix-head-level').value));
  bus.set(bus.monitor.gain, Number($('#mix-head-mix').value));
  decks.forEach(d => d.applySound());
}

async function refreshOutputs() {
  const epoch = mixerEpoch;
  await ensureAudio();
  const audio = bus;
  const devices = await navigator.mediaDevices?.enumerateDevices() || [];
  if (epoch !== mixerEpoch || audio !== bus || !host()?.signedIn) return;
  const select = $('#mix-output');
  const value = bus.context.sinkId || '';
  select.replaceChildren(new Option('Sortie système', ''));
  for (const device of devices.filter(d => d.kind === 'audiooutput' && d.deviceId !== 'default' && d.deviceId)) {
    select.append(new Option(device.label || `Sortie audio ${select.length}`, device.deviceId));
  }
  select.value = value;
  select.disabled = typeof bus.context.setSinkId !== 'function';
  bus.updateRoutingControls();
  refreshKnobs();
  select.title = select.disabled ? 'Sélection de sortie indisponible dans ce navigateur. La sortie système est utilisée.' : 'Sortie audio';
}

function reset() {
  mixerEpoch++;
  saves.clear();
  close();
  cancelAnimationFrame(animation);
  clearInterval(analysisTimer);
  decks.forEach(d => d.eject(true));
  decks.forEach(d => d.timeline.destroy());
  run(disconnectMIDI);
  const context = bus?.context;
  bus = null;
  if (context && context.state !== 'closed') context.close().catch(() => {});
  decks = [];
  $('#mix-decks').replaceChildren();
  $('#mix-overviews').replaceChildren();
  $('#mix-controls').hidePopover();
  refreshKnobs();
  connectionMessage();
  $('#mix-routing').value = 'stereo';
  $('#mix-cross-enabled').checked = false;
  for (const [id, value] of [['mix-cross', '0.5'], ['mix-master', '0.7'], ['mix-head-level', '0.5'], ['mix-head-mix', '0']]) $(`#${id}`).value = value;
  updateMaster();
  browserFocus = 'tracks'; collectionCursor = '';
  cueMemory.clear(); message();
  initialize();
}

$('#mixer-nav').prepend(icon(Gauge));
$('#mixer-nav').addEventListener('click', () => active ? close() : open());
$('#mix-levels-toggle').append(icon(Volume2));
$('#mix-levels-toggle').addEventListener('click', () => {
  const panel = $('#mix-connection');
  if (panel.hasAttribute('popover')) panel.hidePopover();
});
$('.library-sidebar').addEventListener('click', e => {
  if (e.target.closest('[data-collection]')) setBrowserFocus('tracks');
});
$('#library-tracks').addEventListener('keydown', e => {
  if (e.target.matches('.library-track') && ['ArrowUp', 'ArrowDown'].includes(e.key)) {
    e.preventDefault(); browse(e.key === 'ArrowDown' ? 1 : -1, true);
  }
});
midiAction('Connecter le FLX4', Usb);
$('#mix-connect').append(icon(Usb));
$('#mix-connect').addEventListener('click', () => {
  if ($('#mix-connection').dataset.docked === 'true') setConnectionOpen(!connectionOpen);
});
$('#mix-connection-close').append(icon(X));
$('#mix-connection-close').addEventListener('click', () => setConnectionOpen(false));
$('#mix-connection').addEventListener('toggle', () => {
  const panel = $('#mix-connection');
  if (!panel.hasAttribute('popover')) return;
  const previouslyOpen = connectionOpen;
  connectionOpen = panel.matches(':popover-open');
  $('#mix-connect').setAttribute('aria-expanded', String(connectionOpen));
  if (connectionOpen && !previouslyOpen) void refreshOutputs().catch(error => connectionMessage(error.message));
});
const runConnection = callback => Promise.resolve().then(callback).catch(error => connectionMessage(error.message));
$('#mix-midi-connect').addEventListener('click', () => runConnection(connectMIDI));
$('#mix-midi-input').addEventListener('change', () => { midiSelection = $('#mix-midi-input').value; run(bindMIDI); });
$('#mix-midi-refresh').append(icon(RefreshCw));
$('#mix-midi-refresh').addEventListener('click', () => {
  const closing = disconnectMIDI();
  midiBinding = Promise.resolve();
  // Request access in the click gesture, but finish closing old ports before opening new ones.
  void connectMIDI(closing).catch(e => connectionMessage(e.message));
});
$('#mix-stop').append(icon(Square));
$('#mix-stop').addEventListener('click', pause);
$('#mix-master-close').append(icon(X));
$('#mix-master-close').addEventListener('click', () => $('#mix-controls').hidePopover());
let waveWheelDelta = 0, waveWheelTime = 0;
$('#mix-timelines').addEventListener('wheel', event => {
  if (!event.shiftKey || event.ctrlKey || event.metaKey) { waveWheelDelta = 0; return; }
  // Shift can remap a vertical wheel to deltaX; normalize line/page units too.
  const delta = (event.deltaY || event.deltaX) * (event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? 100 : 1);
  if (!delta) return;
  event.preventDefault();
  if (event.timeStamp - waveWheelTime > 200 || Math.sign(delta) !== Math.sign(waveWheelDelta)) waveWheelDelta = 0;
  waveWheelTime = event.timeStamp;
  waveWheelDelta += delta;
  const steps = Math.trunc(waveWheelDelta / 100);
  if (!steps) return;
  waveWheelDelta -= steps * 100;
  const zoom = $('#mix-wave-zoom');
  zoom.value = clamp((Number(zoom.value) || Number(zoom.defaultValue)) - steps, 1, 24);
  if (zoom.value === '1' || zoom.value === '24') waveWheelDelta = 0;
  zoom.dispatchEvent(new Event('input', { bubbles: true }));
}, { passive: false });
$('#mix-output-refresh').append(icon(RefreshCw));
$('#mix-output-refresh').addEventListener('click', () => runConnection(async () => {
  if (navigator.mediaDevices?.selectAudioOutput) await navigator.mediaDevices.selectAudioOutput();
  await refreshOutputs();
}));
$('#mix-output').addEventListener('change', () => runConnection(async () => {
  if (!bus) return;
  pause();
  try { await bus.context.setSinkId($('#mix-output').value); }
  finally { await refreshOutputs(); }
}));
$('#mix-routing').addEventListener('change', () => {
  pause();
  if (bus) { bus.route($('#mix-routing').value === 'split'); updateMaster(); }
});
for (const id of ['mix-cross', 'mix-master', 'mix-head-level', 'mix-head-mix']) $(`#${id}`).addEventListener('input', updateMaster);
$('#mix-cross-enabled').addEventListener('change', updateMaster);
for (const id of ['mix-master', 'mix-head-level', 'mix-head-mix']) makeKnob($(`#${id}`));
$('#mix-cross').addEventListener('dblclick', () => { $('#mix-cross').value = '0.5'; updateMaster(); });
window.addEventListener('pagehide', reset);
window.addEventListener('beforeunload', event => {
  if (decks.some(d => d.playing())) { event.preventDefault(); event.returnValue = ''; }
});
navigator.mediaDevices?.addEventListener('devicechange', () => {
  if (active && bus && connectionOpen) runConnection(refreshOutputs);
});
window.Mixer = { get active() { return active; }, sync, reset, pause,
  get currentPlayback() { const d = decks[0]; return { track: d?.track, position: d?.position() || 0, playing: d?.playing() || false }; },
  clearPrimary() { if (decks[0]?.track) void decks[0].eject(true); },
  seekPrimary(position) { decks[0]?.seek(position); },
  async loadTrack(track, index) {
  if (!active) open();
  await decks[index]?.load(track);
}, removeTrack(jobId) {
  decks.filter(deck => deck.track?.job_id === jobId).forEach(deck => run(() => deck.eject(true)));
}, async playTrack(track) {
  initialize();
  const target = decks[0];
  const loaded = await target.load(track);
  if (loaded && decks[0] === target && target.track?.job_id === track.job_id && target.ready && !target.playing()) await target.toggle();
} };
initialize();
