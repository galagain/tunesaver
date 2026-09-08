import WaveSurfer from './vendor/wavesurfer/wavesurfer.esm.js';
import { FLX4Decoder, clamp, crossfade } from './flx4.js?v=20260908-5';
import { Timeline, cueColors, overviewRenderer, phaseAlignedTime } from './mix-waveforms.js?v=20260908-7';
import { createElement, Play, Pause, Headphones, Flag, Repeat2, LogIn, LogOut, Usb, Settings2, Square, RefreshCw, SkipBack, Gauge, Unplug, X, Minus, Plus } from './vendor/mixer-icons.js';

const $ = (selector, root = document) => root.querySelector(selector);
const host = () => window.TuneSaverMixerHost;
const panel = $('#mixer-panel');
const message = (text = '') => {
  const target = $('#mix-message');
  target.replaceChildren();
  if (!text) return;
  const label = document.createElement('span');
  label.textContent = text;
  target.append(label, button('dismiss', 'Fermer le message', X, () => message()));
};
const icon = (shape) => createElement(shape, { 'aria-hidden': 'true' });
const time = (seconds) => {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  return `${Math.floor(safe / 60)}:${String(Math.floor(safe % 60)).padStart(2, '0')}`;
};
const run = (callback) => { Promise.resolve().then(callback).catch(e => message(e.message || 'Action impossible.')); };
let active = false;
let bus = null;
let decks = [];
let visibleTracks = [];
let selectedId = '';
let fingerprint = '';
let midi = null;
let midiInput = null;
let midiOutput = null;
let midiWanted = false;
let midiEpoch = 0;
let decoder = new FLX4Decoder();
let ticketQueue = Promise.resolve();
const cueMemory = new Map();
const saves = new Map();
let mixerEpoch = 0;
let animation = null;
let analysisTimer = null;
let browserFocus = 'tracks';
let collectionCursor = '';
let visibleCollections = [];
const collapsedFolders = new Set();

function animate() {
  if (!active) return;
  if (!document.hidden) decks.forEach(d => d.timeline.draw(Number($('#mix-wave-zoom').value)));
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
    ctx.destination.channelCount = this.split ? 4 : Math.min(2, ctx.destination.maxChannelCount || 2);
    ctx.destination.channelInterpretation = this.split ? 'discrete' : 'speakers';
    if (this.split) {
      this.guard.connect(this.masterSplit);
      this.head.connect(this.headSplit);
      this.merger.connect(ctx.destination);
    } else this.guard.connect(ctx.destination);
    $('#mix-routing option[value="split"]').disabled = !available;
    $('#mix-routing').value = this.split ? 'split' : 'stereo';
    $('#mix-head-level').disabled = !this.split;
    $('#mix-head-mix').disabled = !this.split;
    $('#mix-audio-status').textContent = this.split
      ? 'Master : canaux 1/2. Casque : canaux 3/4.'
      : available ? 'Sortie stéréo. Quatre canaux disponibles.' : 'Sortie stéréo. Préécoute séparée indisponible sur cette sortie.';
    decks.forEach(d => d.update());
  }

  set(param, value) { param.setTargetAtTime(value, this.context.currentTime, 0.015); }
}

function ensureAudio() {
  if (!bus) bus = new AudioBus();
  return bus.context.resume();
}

// Serialize ticket creation through response headers: issuing another ticket for
// the same song invalidates the previous one on the existing streaming endpoint.
async function audioBlob(track, signal) {
  const request = ticketQueue.then(async () => {
    signal.throwIfAborted();
    const { url } = await host().requestJson('/api/stream-ticket', {
      method: 'POST', body: JSON.stringify({ job_id: track.job_id }), signal,
    });
    signal.throwIfAborted();
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`Chargement audio impossible (${response.status}).`);
    return response;
  });
  ticketQueue = request.then(() => undefined, () => undefined);
  const response = await request;
  const limit = 128 * 1024 * 1024;
  if (Number(response.headers.get('Content-Length')) > limit) {
    await response.body.cancel();
    throw new Error('Ce fichier dépasse la limite de 128 Mo par platine.');
  }
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      await reader.cancel();
      throw new Error('Ce fichier dépasse la limite de 128 Mo par platine.');
    }
    chunks.push(value);
  }
  return new Blob(chunks, { type: response.headers.get('Content-Type') || 'audio/mpeg' });
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
      <details class="mix-grid-editor"><summary>Grille et analyse</summary><div class="mix-analysis-info"></div><div class="mix-grid-tools"><label>Premier temps (s)<input data-control="grid" type="number" min="0" step="0.001" value="0"></label><label>Clé<select data-control="key"><option value="">Automatique</option></select></label></div></details>
      <div class="mix-pads" aria-label="Hot cues ${this.letter}"></div>`;
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
    tools.append(button('anchor', 'Premier temps à la tête de lecture', Flag, () => saveSettings(this.track.job_id, { grid_offset: this.position() })),
      button('gridBack', 'Reculer la grille de 10 ms', Minus, () => this.shiftGrid(-.01)),
      button('gridForward', 'Avancer la grille de 10 ms', Plus, () => this.shiftGrid(.01)),
      button('analyze', 'Relancer l’analyse du morceau', RefreshCw, () => this.reanalyze()));
    for (const [label, factor] of [['BPM / 2', .5], ['BPM × 2', 2]]) {
      const el = document.createElement('button'); el.type = 'button'; el.className = 'secondary-button'; el.textContent = label;
      el.addEventListener('click', () => run(() => saveSettings(this.track.job_id, { bpm: this.grid().bpm * factor })));
      tools.append(el);
    }
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
    this.update();
  }

  control(name) { return $(`[data-control="${name}"]`, this.el); }
  position() { return this.wave?.getCurrentTime() || 0; }
  playing() { return this.wave?.isPlaying() || false; }

  clearAudio() {
    this.version++;
    this.abort?.abort();
    clearTimeout(this.bendTimer);
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
    if (this.playing() && !window.confirm(`Remplacer le morceau en lecture sur ${this.letter} ?`)) return;
    this.clearAudio();
    const version = this.version;
    this.track = track;
    this.abort = new AbortController();
    const signal = this.abort.signal;
    this.hotcues = [...(cueMemory.get(track.job_id)?.hotcues || Array(8).fill(null))];
    this.cue = cueMemory.get(track.job_id)?.cue || 0;
    this.control('bpm').value = Number(track.bpm) > 0 ? track.bpm : '';
    $('.mix-cover', this.el).replaceWith(cover(track));
    $('.mix-track-info strong', this.el).textContent = track.title || track.file_name || 'Sans titre';
    $('.mix-track-info span', this.el).textContent = track.artist || '';
    $('.mix-deck-status', this.el).textContent = 'Chargement…';
    this.update();
    run(() => this.fetchAnalysis());
    try {
      await ensureAudio();
      if (version !== this.version) return;
      const blob = await audioBlob(track, signal);
      if (version !== this.version) return;
      $('.mix-deck-status', this.el).textContent = 'Préparation audio…';
      const media = new Audio();
      media.preload = 'auto';
      this.wave = WaveSurfer.create({
        container: $('.mix-wave', this.el), media, height: 52,
        waveColor: '#626262', progressColor: this.index ? '#ef8eb3' : '#00d79d', cursorColor: '#ffffff',
        barWidth: 2, barGap: 1, dragToSeek: true, normalize: true,
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
      this.wave.on('play', () => { host().resetPlayer(); this.update(); });
      this.wave.on('pause', () => this.update());
      this.wave.on('timeupdate', () => this.tick());
      this.wave.on('finish', () => {
        if (this.ready && this.looping) run(() => this.wave?.play(this.loopStart));
        this.update();
      });
      this.wave.on('error', () => {
        if (version !== this.version) return;
        this.ready = false;
        this.wave.pause();
        $('.mix-deck-status', this.el).textContent = 'Fichier audio illisible.';
        this.update();
      });
      await this.wave.loadBlob(blob);
      if (version !== this.version) return;
      this.ready = true;
      this.applySound();
      this.applyRate();
      $('.mix-deck-status', this.el).textContent = 'Prêt';
      $('[data-duration]', this.el).textContent = time(this.wave.getDuration());
      this.update();
    } catch (e) {
      if (version !== this.version) return;
      this.clearAudio();
      $('.mix-deck-status', this.el).textContent = e.name === 'AbortError' ? 'Chargement annulé' : e.message || 'Chargement impossible.';
      this.update();
    }
  }

  grid() {
    const analysis = this.analysisData?.analysis || {};
    const settings = this.analysisData?.settings || {};
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
      if (version === this.version && error.name !== 'AbortError') $('.mix-analysis-info', this.el).textContent = 'Analyse indisponible. Réessaie depuis les réglages.';
    } finally { if (version === this.version) this.analysisLoading = false; }
  }

  applyAnalysis(data) {
    this.analysisData = data;
    const a = data.analysis || {}, s = data.settings || {};
    this.control('bpm').value = s.bpm ?? a.bpm ?? '';
    this.control('grid').value = (s.grid_offset ?? a.grid_offset ?? 0).toFixed(3);
    this.control('key').value = s.key || '';
    this.cue = s.cue ?? 0;
    this.hotcues = [...(s.hotcues || Array(8).fill(null))];
    const key = s.key || a.key;
    $('.mix-key', this.el).textContent = key ? `${key}${!s.key && a.camelot ? ` · ${a.camelot}` : ''}` : 'Clé —';
    const quality = { stable: 'Grille estimée · 4/4', variable: 'Tempo variable · grille à vérifier', uncertain: 'Rythme incertain · grille à renseigner' }[a.grid_quality] || '';
    $('.mix-analysis-info', this.el).textContent = data.status === 'done'
      ? [quality, key ? 'Clé estimée' : 'Clé indéterminée', a.codec?.toUpperCase(), a.sample_rate ? `${(a.sample_rate / 1000).toFixed(1)} kHz` : '', a.bit_rate ? `${Math.round(a.bit_rate / 1000)} kb/s` : ''].filter(Boolean).join(' · ')
      : data.status === 'failed' ? data.error || 'Analyse échouée' : data.status === 'running' ? 'Analyse en cours…' : 'Analyse en attente';
    if (a.waveform && this.wave) this.wave.setOptions({ renderFunction: overviewRenderer(a) });
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
    const grid = this.grid();
    if (!this.control('quantize').checked || !grid.bpm) return position;
    const step = 60 / grid.bpm;
    return clamp(grid.offset + Math.round((position - grid.offset) / step) * step, 0, this.wave?.getDuration() || position);
  }

  applySound() {
    if (!this.nodes.length) return;
    for (const name of ['low', 'mid', 'high', 'trim']) bus.set(this[name].gain, Number(this.control(name).value));
    bus.set(this.fader.gain, Number(this.control('volume').value));
    bus.set(this.cross.gain, crossfade(Number($('#mix-cross').value))[this.index]);
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
    cueMemory.set(this.track.job_id, { cue: this.cue, hotcues: [...this.hotcues] });
    const job = this.track.job_id;
    run(() => saveSettings(job, patch));
  }

  async hotcue(pad, clear = false) {
    if (!this.ready) return;
    if (clear) this.hotcues[pad] = null;
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
    const bpm = Number(this.control('bpm').value);
    const target = Number(other.control('bpm').value) * other.rate;
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
    $('[data-time]', this.el).textContent = time(this.position());
  }

  update() {
    this.updateTempoLabel();
    for (const el of Object.values(this.buttons)) el.disabled = !this.ready;
    this.buttons.headphone.disabled = !this.ready || !bus?.split;
    this.buttons.play.replaceChildren(icon(this.playing() ? Pause : Play));
    this.buttons.play.setAttribute('aria-pressed', String(this.playing()));
    this.buttons.headphone.setAttribute('aria-pressed', String(this.headphone && Boolean(bus?.split)));
    this.buttons.loop.setAttribute('aria-pressed', String(this.looping));
    this.buttons.loopIn.setAttribute('aria-pressed', String(this.loopStart !== null));
    this.buttons.loopOut.setAttribute('aria-pressed', String(this.loopEnd !== null));
    $$('.mix-pad', this.el).forEach((pad, i) => {
      pad.disabled = !this.ready; pad.dataset.set = String(this.hotcues[i] !== null);
      pad.textContent = 'ABCDEFGH'[i] + (this.hotcues[i] !== null ? ` ${time(this.hotcues[i])}` : '');
      $(`[data-clear-pad="${i}"]`, this.el).disabled = !this.ready || this.hotcues[i] === null;
    });
    this.renderMarkers();
    this.control('bpm').disabled = !this.track;
    $$('.mix-grid-editor input, .mix-grid-editor select, .mix-grid-editor button', this.el).forEach(el => { el.disabled = !this.track; });
    if (this.ready) $('.mix-deck-status', this.el).textContent = this.looping ? 'Boucle active' : this.playing() ? 'En lecture' : 'Prêt';
    this.tick();
    sendLEDs(this);
  }

  renderMarkers() {
    const container = $('.mix-cue-markers', this.el);
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
      marker.style.left = `${group.left}%`; marker.style.setProperty('--cue-color', cueColors[group.indices[0]]);
      marker.title = `Hot cue ${marker.textContent} · ${time(group.t)}`;
      marker.setAttribute('aria-label', marker.title);
      marker.addEventListener('click', () => this.seek(group.t));
      container.append(marker);
    }
  }

  eject(force = false) {
    if (!force && this.playing() && !window.confirm(`Vider la platine ${this.letter} en lecture ?`)) return;
    this.clearAudio();
    this.track = null;
    this.hotcues = Array(8).fill(null);
    this.cue = 0;
    $('.mix-cover', this.el).replaceWith(cover(null));
    $('.mix-track-info strong', this.el).textContent = 'Aucun morceau';
    $('.mix-track-info span', this.el).textContent = '';
    $('.mix-deck-status', this.el).textContent = 'Vide';
    $('.mix-analysis-info', this.el).textContent = '';
    $('.mix-key', this.el).textContent = '';
    $('[data-duration]', this.el).textContent = '0:00';
    this.update();
  }
}

function $$(selector, root = document) { return Array.from(root.querySelectorAll(selector)); }

function filteredTracks() {
  const library = host().library;
  const value = $('#mix-collection').value;
  const [kind, id] = value.split(':');
  const collection = library[kind]?.find(item => item.id === id);
  const query = $('#mix-search').value.trim().toLocaleLowerCase();
  return library.tracks.filter(track => track.streamable
    && (!collection || collection.track_ids.includes(track.job_id))
    && `${track.title || track.file_name || ''} ${track.artist || ''}`.toLocaleLowerCase().includes(query));
}

function renderTracks() {
  visibleTracks = filteredTracks();
  if (!visibleTracks.some(t => t.job_id === selectedId)) selectedId = visibleTracks[0]?.job_id || '';
  $('#mix-track-count').textContent = `${visibleTracks.length} sons`;
  const list = $('#mix-tracks');
  list.replaceChildren();
  if (!visibleTracks.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-copy';
    empty.textContent = 'Aucun son disponible.';
    list.append(empty);
  }
  for (const track of visibleTracks) {
    const row = document.createElement('div');
    row.className = 'mix-library-row';
    row.dataset.jobId = track.job_id;
    row.dataset.selected = String(track.job_id === selectedId);
    row.setAttribute('role', 'listitem');
    row.tabIndex = 0;
    row.draggable = true;
    row.addEventListener('dragstart', e => { selectedId = track.job_id; e.dataTransfer.setData('application/x-tunesaver-job', track.job_id); });
    row.addEventListener('click', () => selectTrack(track.job_id));
    row.addEventListener('keydown', e => {
      if (e.target !== row) return;
      if (e.key === 'Enter') { e.preventDefault(); selectTrack(track.job_id); }
      if (['ArrowUp', 'ArrowDown'].includes(e.key)) { e.preventDefault(); browse(e.key === 'ArrowDown' ? 1 : -1, true); }
    });
    const info = document.createElement('div');
    info.className = 'mix-track-info';
    const title = document.createElement('strong');
    title.textContent = track.title || track.file_name || 'Sans titre';
    const artist = document.createElement('span');
    artist.textContent = track.artist || '';
    info.append(title, artist);
    row.append(cover(track), info);
    const analysis = track.mix_analysis || {};
    for (const [className, value] of [['bpm', analysis.bpm ? Number(analysis.bpm).toFixed(2) : analysis.status === 'failed' ? '!' : '—'], ['key', analysis.camelot || analysis.key || '—'], ['duration', analysis.duration ? time(analysis.duration) : '—']]) {
      const cell = document.createElement('span'); cell.className = `mix-meta-${className}`; cell.textContent = value;
      cell.title = className === 'key' ? [analysis.key, analysis.camelot].filter(Boolean).join(' · ') : analysis.error || '';
      row.append(cell);
    }
    decks.forEach(deck => {
      const load = document.createElement('button');
      load.type = 'button'; load.textContent = deck.letter;
      load.title = `Charger sur ${deck.letter}`;
      load.setAttribute('aria-label', `Charger ${title.textContent} sur ${deck.letter}`);
      load.addEventListener('click', () => run(() => deck.load(track)));
      row.append(load);
    });
    list.append(row);
  }
}

function selectTrack(id) {
  selectedId = id;
  setBrowserFocus('tracks');
  $$('.mix-library-row').forEach(row => { row.dataset.selected = String(row.dataset.jobId === id); });
}

function browse(delta, focus = false) {
  if (!delta) return;
  if (browserFocus === 'collections' && !focus) {
    if (!visibleCollections.length) return;
    const current = visibleCollections.findIndex(c => c.id === collectionCursor);
    collectionCursor = visibleCollections[clamp(current + Math.sign(delta), 0, visibleCollections.length - 1)].id;
    renderCollections();
    $('#mix-collections [data-cursor="true"]')?.scrollIntoView({ block: 'nearest' });
    return;
  }
  if (!visibleTracks.length) return;
  const index = visibleTracks.findIndex(t => t.job_id === selectedId);
  selectTrack(visibleTracks[clamp(index + Math.sign(delta), 0, visibleTracks.length - 1)].job_id);
  const row = $('.mix-library-row[data-selected="true"]');
  row?.scrollIntoView({ block: 'nearest' });
  if (focus) row?.focus();
}

function setBrowserFocus(focus) {
  browserFocus = focus;
  $('.mix-browser').dataset.focus = focus;
  renderCollections();
}

function chooseCollection(item) {
  if (item.folder) {
    if (collapsedFolders.has(item.folder)) collapsedFolders.delete(item.folder);
    else collapsedFolders.add(item.folder);
    collectionCursor = item.id;
    renderCollections();
    return;
  }
  $('#mix-collection').value = item.id;
  collectionCursor = item.id;
  renderTracks();
  setBrowserFocus('tracks');
}

function browseFocus(back = false) {
  if (back || browserFocus === 'tracks') {
    collectionCursor = $('#mix-collection').value;
    setBrowserFocus('collections');
  } else {
    const item = visibleCollections.find(c => c.id === collectionCursor);
    if (item) chooseCollection(item);
  }
}

function renderCollections() {
  const library = host().library;
  const all = [{ id: '', name: 'Tous mes sons', depth: 0 }];
  const folders = library.folders || [];
  const folderIds = new Set(folders.map(f => f.id));
  const playlistItem = (p, depth = 0) => ({ id: `playlists:${p.id}`, name: p.name, depth });
  for (const folder of folders) {
    all.push({ id: `folder:${folder.id}`, name: folder.name, folder: folder.id, depth: 0 });
    if (!collapsedFolders.has(folder.id)) all.push(...library.playlists.filter(p => p.folder_id === folder.id).map(p => playlistItem(p, 1)));
  }
  all.push(...library.playlists.filter(p => !folderIds.has(p.folder_id)).map(p => playlistItem(p)));
  all.push(...library.tags.map(t => ({ id: `tags:${t.id}`, name: t.name, tag: true, depth: 0 })));
  visibleCollections = all;
  if (!all.some(c => c.id === collectionCursor)) collectionCursor = '';
  const nav = $('#mix-collections');
  nav.replaceChildren();
  const heading = document.createElement('h3'); heading.textContent = 'Playlists'; nav.append(heading);
  let tagsHeading = false;
  for (const item of all) {
    if (item.tag && !tagsHeading) { const title = document.createElement('h3'); title.textContent = 'Tags'; nav.append(title); tagsHeading = true; }
    const el = document.createElement('button'); el.type = 'button';
    el.dataset.collection = item.id; el.dataset.depth = item.depth;
    el.dataset.selected = String(item.id === $('#mix-collection').value);
    el.dataset.cursor = String(browserFocus === 'collections' && item.id === collectionCursor);
    el.textContent = item.folder ? `${collapsedFolders.has(item.folder) ? '▸' : '▾'} ${item.name}` : item.name;
    if (item.folder) el.setAttribute('aria-expanded', String(!collapsedFolders.has(item.folder)));
    else el.setAttribute('aria-pressed', String(item.id === $('#mix-collection').value));
    el.addEventListener('click', () => chooseCollection(item));
    el.addEventListener('keydown', event => {
      if (['ArrowUp', 'ArrowDown'].includes(event.key)) { event.preventDefault(); browserFocus = 'collections'; collectionCursor = item.id; browse(event.key === 'ArrowUp' ? -1 : 1); $('#mix-collections [data-cursor="true"]')?.focus(); }
    });
    nav.append(el);
  }
}

function sync() {
  if (!active) return;
  $('#library-title').textContent = 'Mixer';
  $('#library-count').textContent = 'Deux platines';
  $$('.playlist-filter, .tag-filter').forEach(el => { el.dataset.selected = String(el.id === 'mixer-nav'); });
  const library = host().library;
  const analyzed = library.tracks.filter(t => t.streamable);
  const done = analyzed.filter(t => t.mix_analysis?.status === 'done').length;
  const failed = analyzed.filter(t => t.mix_analysis?.status === 'failed').length;
  $('#mix-analysis-status').textContent = `Analyse : ${done}/${analyzed.length}${failed ? ` · ${failed} en échec` : ''}`;
  const next = JSON.stringify([library.tracks, library.playlists, library.tags, library.folders]);
  if (next === fingerprint) return;
  fingerprint = next;
  const select = $('#mix-collection');
  const value = select.value;
  select.replaceChildren(new Option('Tous mes sons', ''));
  for (const [kind, name] of [['playlists', 'Playlists'], ['tags', 'Tags']]) {
    const group = document.createElement('optgroup');
    group.label = name;
    for (const item of library[kind]) group.append(new Option(item.name, `${kind}:${item.id}`));
    select.append(group);
  }
  if (Array.from(select.options).some(o => o.value === value)) select.value = value;
  renderCollections();
  renderTracks();
}

function open() {
  if (!host()?.signedIn) return;
  active = true;
  host().resetPlayer();
  document.body.dataset.mixer = 'true';
  $('#library-content').hidden = true;
  $('#results-section').hidden = true;
  panel.hidden = false;
  $('#mixer-nav').setAttribute('aria-expanded', 'true');
  if (!decks.length) decks = [new Deck(0), new Deck(1)];
  sync();
  cancelAnimationFrame(animation);
  animate();
  clearInterval(analysisTimer);
  analysisTimer = setInterval(() => decks.filter(d => d.track && !['done', 'failed'].includes(d.analysisData?.status)).forEach(d => run(() => d.fetchAnalysis())), 4000);
  run(ensureAudio);
}

function close() {
  active = false;
  cancelAnimationFrame(animation);
  clearInterval(analysisTimer);
  pause();
  panel.hidden = true;
  $('#library-content').hidden = false;
  delete document.body.dataset.mixer;
  $('#mixer-nav').setAttribute('aria-expanded', 'false');
}

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
    const track = visibleTracks.find(t => t.job_id === selectedId);
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

async function bindMIDI() {
  if (!midiWanted || !midi) return;
  const epoch = midiEpoch;
  const input = Array.from(midi.inputs.values()).find(p => /DDJ[- ]?FLX4/i.test(p.name || '') && p.state === 'connected');
  if (midiInput && midiInput !== input) { midiInput.onmidimessage = null; await midiInput.close(); }
  midiInput = input || null;
  if (midiOutput) await midiOutput.close();
  midiOutput = null;
  if (input) {
    await input.open();
    if (epoch !== midiEpoch || !midiWanted) { await input.close(); return; }
    input.onmidimessage = handleMIDI;
    const output = Array.from(midi.outputs.values()).find(p => /DDJ[- ]?FLX4/i.test(p.name || '') && p.state === 'connected');
    if (output) {
      await output.open();
      if (epoch !== midiEpoch || !midiWanted) { await output.close(); return; }
      midiOutput = output;
    }
    decoder = new FLX4Decoder();
    decks.forEach(sendLEDs);
  }
  $('#mix-midi-status').textContent = input ? `DDJ-FLX4 connecté` : 'FLX4 introuvable. Vérifie le câble USB et ferme les autres logiciels DJ.';
  $('#mix-midi-status').dataset.connected = String(Boolean(input));
}

async function disconnectMIDI() {
  midiWanted = false; midiEpoch++;
  if (midi) midi.onstatechange = null;
  if (midiInput) { midiInput.onmidimessage = null; await midiInput.close(); }
  if (midiOutput) await midiOutput.close();
  midiInput = midiOutput = midi = null;
  $('#mix-connect').replaceChildren(icon(Usb), document.createTextNode('Connecter le FLX4'));
  $('#mix-midi-status').textContent = 'Non connecté';
  $('#mix-midi-status').dataset.connected = 'false';
}

async function connectMIDI() {
  if (midiWanted) { await disconnectMIDI(); return; }
  if (!navigator.requestMIDIAccess) { message('MIDI indisponible dans ce navigateur. Utilise Chrome ou Edge sur ordinateur.'); return; }
  const epoch = ++midiEpoch;
  $('#mix-connect').disabled = true;
  try {
    await ensureAudio();
    const access = await navigator.requestMIDIAccess({ sysex: false });
    if (epoch !== midiEpoch || !host()?.signedIn) return;
    midi = access; midiWanted = true;
    midi.onstatechange = event => {
      if (event.port.state === 'disconnected' && event.port === midiInput) {
        pause();
        midiInput.onmidimessage = null;
        midiInput = null;
        run(bindMIDI);
      }
      // Opening/closing ports also fires statechange; only hardware changes need rebinding.
      if (event.port.type === 'input' && event.port.state === 'connected' && event.port !== midiInput) run(bindMIDI);
    };
    await bindMIDI();
    $('#mix-connect').replaceChildren(icon(Unplug), document.createTextNode('Déconnecter le MIDI'));
  } catch (e) {
    await disconnectMIDI();
    message(e.name === 'NotAllowedError' ? 'Accès MIDI refusé. Autorise le MIDI dans les permissions du site.' : 'Connexion MIDI impossible. Vérifie le contrôleur et son câble USB.');
  } finally { $('#mix-connect').disabled = false; }
}

function updateMaster() {
  if (!bus) return;
  bus.set(bus.master.gain, Number($('#mix-master').value));
  bus.set(bus.head.gain, Number($('#mix-head-level').value));
  bus.set(bus.monitor.gain, Number($('#mix-head-mix').value));
  decks.forEach(d => d.applySound());
}

async function refreshOutputs() {
  await ensureAudio();
  const select = $('#mix-output');
  const value = bus.context.sinkId || '';
  select.replaceChildren(new Option('Sortie système', ''));
  const devices = await navigator.mediaDevices?.enumerateDevices() || [];
  for (const device of devices.filter(d => d.kind === 'audiooutput' && d.deviceId !== 'default' && d.deviceId)) {
    select.append(new Option(device.label || `Sortie audio ${select.length}`, device.deviceId));
  }
  select.value = value;
  select.disabled = typeof bus.context.setSinkId !== 'function';
  bus.route(bus.split);
  updateMaster();
  if (select.disabled) $('#mix-audio-status').textContent = 'Sélection de sortie indisponible dans ce navigateur. La sortie système est utilisée.';
}

function reset() {
  mixerEpoch++;
  saves.clear();
  close();
  decks.forEach(d => d.eject(true));
  decks.forEach(d => d.timeline.destroy());
  run(disconnectMIDI);
  const context = bus?.context;
  bus = null;
  if (context && context.state !== 'closed') context.close().catch(() => {});
  decks = [];
  $('#mix-decks').replaceChildren();
  $('#mix-tracks').replaceChildren();
  $('#mix-collection').replaceChildren(new Option('Tous mes sons', ''));
  $('#mix-search').value = '';
  $('#mix-settings').hidden = true;
  $('#mix-audio-settings').setAttribute('aria-expanded', 'false');
  $('#mix-routing').value = 'stereo';
  for (const [id, value] of [['mix-cross', '0.5'], ['mix-master', '0.7'], ['mix-head-level', '0.5'], ['mix-head-mix', '0']]) $(`#${id}`).value = value;
  visibleTracks = []; selectedId = ''; fingerprint = '';
  browserFocus = 'tracks'; collectionCursor = ''; visibleCollections = [];
  collapsedFolders.clear();
  $('#mix-collections').replaceChildren();
  $('#mix-analysis-status').textContent = '';
  cueMemory.clear(); message();
}

$('#mixer-nav').addEventListener('click', open);
$('.library-sidebar').addEventListener('click', e => {
  if (active && e.target.closest('.playlist-filter:not(#mixer-nav), .tag-filter')) close();
}, true);
$('#mix-connect').prepend(icon(Usb));
$('#mix-connect').addEventListener('click', () => run(connectMIDI));
$('#mix-stop').append(icon(Square));
$('#mix-stop').addEventListener('click', pause);
$('#mix-audio-settings').append(icon(Settings2));
$('#mix-audio-settings').addEventListener('click', () => {
  const settings = $('#mix-settings');
  settings.hidden = !settings.hidden;
  $('#mix-audio-settings').setAttribute('aria-expanded', String(!settings.hidden));
  if (!settings.hidden) run(refreshOutputs);
});
$('#mix-output-refresh').append(icon(RefreshCw));
$('#mix-output-refresh').addEventListener('click', () => run(async () => {
  if (navigator.mediaDevices?.selectAudioOutput) await navigator.mediaDevices.selectAudioOutput();
  await refreshOutputs();
}));
$('#mix-output').addEventListener('change', () => run(async () => {
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
$('#mix-cross').addEventListener('dblclick', () => { $('#mix-cross').value = '0.5'; updateMaster(); });
$('#mix-search').addEventListener('input', renderTracks);
$('#mix-collection').addEventListener('change', () => { collectionCursor = $('#mix-collection').value; setBrowserFocus('tracks'); renderTracks(); });
window.addEventListener('pagehide', reset);
window.addEventListener('beforeunload', event => {
  if (decks.some(d => d.playing())) { event.preventDefault(); event.returnValue = ''; }
});
navigator.mediaDevices?.addEventListener('devicechange', () => {
  if (active && bus && !$('#mix-settings').hidden) run(refreshOutputs);
});
window.Mixer = { get active() { return active; }, sync, reset, pause };
