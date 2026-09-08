import { overviewRenderer } from './mix-waveforms.js?v=20260908-17';
import { createElement, Play, Pause, SkipBack, RefreshCw } from './vendor/mixer-icons.js';
import { Volume2, VolumeX, SkipForward } from './vendor/player-icons.js';

const $ = selector => document.querySelector(selector);
const media = $('#audio-player'), canvas = $('#player-waveform');
const host = () => window.TuneSaverMixerHost;
const base = document.createElement('canvas');
let track = null, analysis = null, settings = {}, generation = 0, abort = null, timer = null;
let frame = null, baseKey = '', render = null, error = '', dragging = null;
const time = seconds => {
  const value = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
};
const duration = () => Number.isFinite(media.duration) ? media.duration : analysis?.duration || track?.mix_analysis?.duration || 0;
const icon = (selector, shape, label) => {
  const button = $(selector);
  button.replaceChildren(createElement(shape, { 'aria-hidden': 'true' }));
  button.title = label; button.setAttribute('aria-label', label);
};

function metadata() {
  const a = { ...track?.mix_analysis, ...analysis }, bpm = settings.bpm ?? a.bpm;
  const fields = [
    bpm ? `${Number(bpm).toFixed(2)} BPM` : 'BPM —',
    settings.key || [a.key, a.camelot].filter(Boolean).join(' / ') || 'Clé —',
    a.codec?.toUpperCase(), a.sample_rate ? `${(a.sample_rate / 1000).toFixed(1)} kHz` : '',
    a.bit_rate ? `${Math.round(a.bit_rate / 1000)} kb/s` : '',
  ].filter(Boolean);
  const target = $('#player-metadata'), label = fields.join(' · ');
  if (target.dataset.value !== label) {
    target.dataset.value = label; target.title = label; target.setAttribute('aria-label', label);
    target.replaceChildren(...fields.map(value => { const span = document.createElement('span'); span.textContent = `${value} `; return span; }));
  }
  const tracks = host()?.visibleTracks || [], index = tracks.findIndex(t => t.job_id === track?.job_id);
  $('#player-next').disabled = !tracks.slice(index + 1).some(t => t.streamable);
  $('#player-previous').disabled = media.currentTime <= 3 && !tracks.slice(0, Math.max(0, index)).some(t => t.streamable);
}

function draw() {
  if (!track || $('#player-bar').hidden || document.hidden) return;
  const rect = canvas.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
  const width = Math.round(rect.width * dpr), height = Math.round(rect.height * dpr);
  if (!width || !height) return;
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  const key = `${width}:${height}`;
  if (key !== baseKey) {
    base.width = width; base.height = height;
    const ctx = base.getContext('2d');
    render?.([], ctx);
    baseKey = key;
  }
  const ctx = canvas.getContext('2d'); ctx.clearRect(0, 0, width, height); ctx.drawImage(base, 0, 0);
  const total = duration(), position = Math.min(total, Math.max(0, media.currentTime || 0));
  if (render && total) {
    const x = position / total * width;
    ctx.fillStyle = '#fff'; ctx.fillRect(Math.min(width - dpr, x), 0, dpr, height);
  }
  canvas.setAttribute('aria-valuemax', total.toFixed(1));
  canvas.setAttribute('aria-valuenow', position.toFixed(1));
  canvas.setAttribute('aria-valuetext', `${time(position)} sur ${time(total)}`);
  canvas.setAttribute('aria-disabled', String(!Number.isFinite(media.duration)));
  $('#player-elapsed').textContent = time(position);
  $('#player-duration').textContent = time(total);
}

function tick() {
  cancelAnimationFrame(frame);
  draw();
  if (!media.paused && track && !document.hidden) frame = requestAnimationFrame(tick);
}

function update() {
  icon('#player-toggle', media.paused ? Play : Pause, media.paused ? 'Lire' : 'Pause');
  $('#player-toggle').setAttribute('aria-pressed', String(!media.paused));
  icon('#player-mute', media.muted ? VolumeX : Volume2, media.muted ? 'Rétablir le son' : 'Couper le son');
  $('#player-mute').setAttribute('aria-pressed', String(media.muted));
  if (track) metadata();
  tick();
}

async function fetchAnalysis(retry = false) {
  if (!track) return;
  const version = generation;
  clearTimeout(timer);
  abort?.abort(); abort = new AbortController();
  $('#player-retry').hidden = true;
  try {
    const data = await host().requestJson(`/api/mixer/tracks/${encodeURIComponent(track.job_id)}`, {
      method: retry ? 'POST' : 'GET', ...(retry ? { body: '{}' } : {}), signal: abort.signal,
    });
    if (version !== generation) return;
    analysis = data.analysis || null; settings = data.settings || {};
    render = overviewRenderer(analysis); baseKey = '';
    if (data.status === 'failed' || (data.status === 'done' && !render)) throw Error('Forme d’onde indisponible');
    error = '';
    $('#player-status').textContent = render ? '' : 'Analyse en cours…';
    if (!render) timer = setTimeout(() => fetchAnalysis(), 4000);
    metadata(); draw();
  } catch (e) {
    if (version !== generation || e.name === 'AbortError') return;
    error = 'Forme d’onde indisponible';
    $('#player-status').textContent = error;
    $('#player-retry').hidden = false;
  }
}

function reset() {
  generation++; abort?.abort(); clearTimeout(timer); cancelAnimationFrame(frame);
  track = analysis = render = null; settings = {}; baseKey = ''; error = ''; dragging = null;
  canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
  base.width = base.height = 0;
  $('#player-status').textContent = ''; $('#player-metadata').textContent = '';
  delete $('#player-metadata').dataset.value;
  $('#player-metadata').removeAttribute('aria-label');
  $('#player-metadata').removeAttribute('title');
  $('#player-retry').hidden = true;
}

async function toggle() {
  if (!track) return;
  try { if (media.paused) await media.play(); else media.pause(); }
  catch { $('#player-status').textContent = 'Lecture impossible. Réessaie depuis la liste.'; }
}

function seek(event) {
  if (!Number.isFinite(media.duration)) return;
  const box = canvas.getBoundingClientRect();
  media.currentTime = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) * media.duration;
  draw();
}
canvas.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  dragging = event.pointerId; canvas.setPointerCapture(dragging); canvas.focus(); seek(event);
});
canvas.addEventListener('pointermove', event => { if (event.pointerId === dragging) seek(event); });
canvas.addEventListener('pointerup', event => { if (event.pointerId === dragging) { seek(event); dragging = null; } });
canvas.addEventListener('lostpointercapture', () => { dragging = null; });
canvas.addEventListener('keydown', event => {
  if (event.key === ' ') { event.preventDefault(); void toggle(); return; }
  if (!Number.isFinite(media.duration) || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  media.currentTime = event.key === 'Home' ? 0 : event.key === 'End' ? media.duration
    : Math.max(0, Math.min(media.duration, media.currentTime + (event.key === 'ArrowRight' ? 5 : -5)));
  draw();
});
icon('#player-previous', SkipBack, 'Morceau précédent');
icon('#player-next', SkipForward, 'Morceau suivant');
icon('#player-retry', RefreshCw, 'Relancer l’analyse');
$('#player-retry').addEventListener('click', () => fetchAnalysis(true));
$('#player-toggle').addEventListener('click', toggle);
$('#player-mute').addEventListener('click', () => { media.muted = !media.muted; });
$('#player-next').addEventListener('click', () => host().playNextTrack());
$('#player-previous').addEventListener('click', () => host().playPreviousTrack().catch(() => { $('#player-status').textContent = 'Lecture impossible.'; }));
for (const event of ['play', 'pause', 'ended', 'loadedmetadata', 'durationchange', 'seeked', 'volumechange', 'emptied']) media.addEventListener(event, update);
media.addEventListener('timeupdate', () => { if (track) { metadata(); draw(); } });
media.addEventListener('error', () => { if (track) $('#player-status').textContent = 'Fichier audio illisible.'; });
document.addEventListener('visibilitychange', tick);
new ResizeObserver(draw).observe(canvas);
window.addEventListener('pagehide', reset);
window.TuneSaverPlayer = {
  reset,
  setTrack(value) {
    reset(); track = value;
    $('#player-status').textContent = 'Analyse en cours…';
    update(); void fetchAnalysis();
  },
  refreshMetadata(value) { if (value && track?.job_id === value.job_id) { track = value; metadata(); } },
};
update();
