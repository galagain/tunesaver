export const cueColors = ['#ff665d', '#ffba55', '#efe36a', '#74da8e', '#54d2db', '#77a7ff', '#c692f1', '#ef8eb3'];

export function gridPosition(time, bpm, offset) {
  return Number.isFinite(bpm) && bpm > 0 && Number.isFinite(offset) ? (time - offset) * bpm / 60 : null;
}

export function detectedGrid(beats, time) {
  if (!beats || beats.length < 2) return null;
  let low = 0, high = beats.length;
  while (low < high) { const mid = (low + high) >>> 1; if (beats[mid] <= time) low = mid + 1; else high = mid; }
  const i = Math.max(0, Math.min(beats.length - 2, low - 1)), step = beats[i + 1] - beats[i];
  return step > 0 ? { bpm: 60 / step, offset: beats[i] } : null;
}

export function phaseAlignedTime(time, own, otherTime, other, duration = Infinity) {
  const source = gridPosition(time, own.bpm, own.offset), target = gridPosition(otherTime, other.bpm, other.offset);
  if (source === null || target === null) return time;
  const phase = ((target % 1) + 1) % 1, step = 60 / own.bpm;
  let aligned = own.offset + (Math.round(source - phase) + phase) * step;
  if (aligned < 0) aligned += Math.ceil(-aligned / step) * step;
  if (aligned > duration) aligned -= Math.ceil((aligned - duration) / step) * step;
  return aligned;
}

const decoded = new WeakMap();
export function unpackWaveform(waveform) {
  if (waveform?.encoding !== 'u8-lmhpn') return null;
  if (!decoded.has(waveform)) {
    const bytes = Uint8Array.from(atob(waveform.data), c => c.charCodeAt(0));
    if (bytes.length !== waveform.frames * 5) return null;
    decoded.set(waveform, bytes);
  }
  return decoded.get(waveform);
}

export function peakRange(bytes, first, last) {
  const peak = [0, 0, 0, 0, 0];
  for (let i = Math.max(0, first); i < Math.min(bytes.length / 5, Math.max(first + 1, last)); i++) {
    for (let band = 0; band < 5; band++) peak[band] = Math.max(peak[band], bytes[i * 5 + band]);
  }
  return peak;
}

export function interpolatedPeak(bytes, position) {
  const frames = bytes.length / 5;
  if (position < 0 || position >= frames) return [0, 0, 0, 0, 0];
  const first = Math.floor(position), last = Math.min(first + 1, frames - 1), fraction = position - first;
  return Array.from({ length: 5 }, (_, band) => bytes[first * 5 + band] * (1 - fraction) + bytes[last * 5 + band] * fraction);
}

function paint(ctx, analysis, raw, start, span, width, height) {
  const waveform = analysis?.waveform, bytes = unpackWaveform(waveform);
  if (!bytes && !raw) return;
  const points = [], gradient = ctx.createLinearGradient(0, 0, width, 0);
  for (let x = 0; x <= width; x++) {
    const from = start + x / width * span, to = start + (x + 1) / width * span;
    let peak;
    if (bytes) peak = span / width * waveform.rate < 1
      ? interpolatedPeak(bytes, from * waveform.rate)
      : peakRange(bytes, Math.floor(from * waveform.rate), Math.ceil(to * waveform.rate));
    else if (raw) {
      peak = [60, 110, 140, 0, 0];
      for (let channel = 0; channel < raw.numberOfChannels; channel++) {
        const samples = raw.getChannelData(channel);
        for (let i = Math.max(0, Math.floor(from * raw.sampleRate)); i < Math.min(samples.length, Math.ceil(to * raw.sampleRate)); i++) {
          peak[3] = Math.max(peak[3], samples[i] * 255); peak[4] = Math.max(peak[4], -samples[i] * 255);
        }
      }
    } else continue;
    const strongest = Math.max(peak[0], peak[1], peak[2], 1);
    gradient.addColorStop(x / width, `rgb(${Math.round(35 + peak[0] / strongest * 220)},${Math.round(20 + peak[1] / strongest * 185)},${Math.round(35 + peak[2] / strongest * 220)})`);
    points.push([height / 2 - peak[3] / 255 * height / 2, height / 2 + peak[4] / 255 * height / 2]);
  }
  ctx.beginPath();
  points.forEach((point, x) => x ? ctx.lineTo(x, point[0]) : ctx.moveTo(x, point[0]));
  for (let x = points.length - 1; x >= 0; x--) ctx.lineTo(x, points[x][1]);
  ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();
}

export function overviewRenderer(analysis) {
  if (!unpackWaveform(analysis?.waveform)) return undefined;
  return (_peaks, ctx) => paint(ctx, analysis, null, 0, analysis.duration, ctx.canvas.width, ctx.canvas.height);
}

export class Timeline {
  constructor(container, deck) {
    this.deck = deck; this.seconds = 4; this.start = 0; this.span = 4;
    this.tiles = new Map();
    this.lane = document.createElement('div'); this.lane.className = 'mix-timeline-lane';
    this.canvas = document.createElement('canvas');
    this.canvas.setAttribute('aria-label', `Forme d'onde et grille de la platine ${deck.letter}`); this.canvas.tabIndex = 0;
    this.cursor = document.createElement('div'); this.cursor.className = 'mix-timeline-cursor'; this.cursor.dataset.deck = deck.index;
    this.lane.append(this.canvas, this.cursor); container.append(this.lane);
    this.canvas.addEventListener('click', event => {
      const box = this.canvas.getBoundingClientRect();
      const at = this.start + (event.clientX - box.left) / box.width * this.span;
      const cue = deck.hotcues.find(t => t !== null && Math.abs(t - at) < this.span * 10 / box.width);
      deck.seek(Math.max(0, cue ?? at));
    });
    this.canvas.addEventListener('keydown', event => {
      if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {
        event.preventDefault(); deck.seek(deck.position() + (event.key === 'ArrowRight' ? 1 : -1) * 60 / (deck.grid().bpm || 120));
      }
    });
  }

  draw(seconds = 4) {
    this.seconds = seconds;
    const deck = this.deck, rect = this.canvas.getBoundingClientRect();
    if (!rect.width) return;
    const dpr = Math.min(devicePixelRatio || 1, 2), w = rect.width, h = rect.height;
    this.span = seconds * deck.rate;
    this.start = deck.position() - this.span / 2;
    this.cursor.style.left = '50%';
    this.cursor.hidden = !deck.ready;
    const grid = deck.grid(), analysis = deck.analysisData?.analysis, raw = deck.wave?.getDecodedData();
    const stamp = JSON.stringify([w, h, dpr, this.start, this.span, grid, deck.hotcues, deck.loopStart, deck.loopEnd, deck.looping]);
    if (stamp === this.stamp && this.analysis === analysis && this.raw === raw) return;
    const tileStamp = JSON.stringify([w, h, dpr, this.span]);
    if (tileStamp !== this.tileStamp || this.analysis !== analysis || this.raw !== raw) this.tiles.clear();
    this.tileStamp = tileStamp;
    this.stamp = stamp; this.analysis = analysis; this.raw = raw;
    if (this.canvas.width !== Math.round(w * dpr)) this.canvas.width = Math.round(w * dpr);
    if (this.canvas.height !== Math.round(h * dpr)) this.canvas.height = Math.round(h * dpr);
    const ctx = this.canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#101010'; ctx.fillRect(0, 0, w, h);
    // Rasterize fixed track coordinates once, then translate cached tiles every frame.
    const scale = w * dpr / this.span, tileWidth = 512, used = new Set();
    const duration = analysis?.duration ?? raw?.duration ?? 0;
    const first = Math.max(0, Math.floor(this.start * scale / tileWidth));
    const last = Math.ceil(Math.min(duration, this.start + this.span) * scale / tileWidth);
    for (let i = first; i < last; i++) {
      used.add(i);
      if (!this.tiles.has(i)) {
        const tile = document.createElement('canvas');
        tile.width = tileWidth; tile.height = Math.max(1, Math.round((h - 20) * dpr));
        paint(tile.getContext('2d'), analysis, raw, i * tileWidth / scale, tileWidth / scale, tile.width, tile.height);
        this.tiles.set(i, tile);
      }
      ctx.drawImage(this.tiles.get(i), (i * tileWidth - this.start * scale) / dpr, 18, tileWidth / dpr, h - 20);
    }
    for (const key of this.tiles.keys()) if (!used.has(key)) this.tiles.delete(key);
    const xFor = t => (t - this.start) / this.span * w;
    const line = (at, bar, label) => {
      if (at < 0 || at > duration) return;
      const x = xFor(at); ctx.strokeStyle = bar ? '#eee' : '#707070'; ctx.lineWidth = bar ? 1 : .5;
      ctx.beginPath(); ctx.moveTo(x, 18); ctx.lineTo(x, h); ctx.stroke();
      if (bar) { ctx.fillStyle = '#ddd'; ctx.font = '9px sans-serif'; ctx.fillText(label, x + 2, h - 3); }
    };
    const settings = deck.analysisData?.settings || {};
    if (analysis?.grid_quality === 'variable' && !('bpm' in settings || 'grid_offset' in settings)) {
      (analysis.beats || []).forEach((at, i) => { if (at >= this.start && at <= this.start + this.span) line(at, i % 4 === 0, String(Math.floor(i / 4) + 1)); });
    } else if (grid.bpm) {
      const step = 60 / grid.bpm;
      for (let beat = Math.ceil((this.start - grid.offset) / step); grid.offset + beat * step <= this.start + this.span; beat++) {
        line(grid.offset + beat * step, beat % 4 === 0, String(Math.floor(beat / 4) + 1));
      }
    }
    if (deck.loopStart !== null && deck.loopEnd !== null) { ctx.fillStyle = '#ffffff18'; ctx.fillRect(xFor(deck.loopStart), 18, xFor(deck.loopEnd) - xFor(deck.loopStart), h - 18); }
    const flags = deck.hotcues.map((at, i) => ({ at, i })).filter(f => f.at !== null && f.at >= this.start && f.at < this.start + this.span).sort((a, b) => a.at - b.at);
    let right = 18;
    flags.forEach((flag, i) => {
      const x = xFor(flag.at), label = Math.min(w - (flags.length - i) * 16, Math.max(x, right));
      ctx.fillStyle = cueColors[flag.i]; ctx.fillRect(x, 16, 1, h - 16); ctx.fillRect(label, 0, 15, 15);
      ctx.fillStyle = '#111'; ctx.font = 'bold 10px sans-serif'; ctx.fillText('ABCDEFGH'[flag.i], label + 3, 11); right = label + 16;
    });
    ctx.fillStyle = deck.index ? '#ef8eb3' : '#00d79d'; ctx.font = 'bold 11px sans-serif'; ctx.fillText(deck.letter, 3, 12);
  }

  destroy() { this.lane.remove(); this.tiles.clear(); this.raw = this.analysis = null; }
}
