export const cueColors = ['#ff665d', '#ffba55', '#efe36a', '#74da8e', '#54d2db', '#77a7ff', '#c692f1', '#ef8eb3'];

export function gridPosition(time, bpm, offset) {
  return Number.isFinite(bpm) && bpm > 0 && Number.isFinite(offset) ? (time - offset) * bpm / 60 : null;
}

export function phaseAlignedTime(time, own, otherTime, other, duration = Infinity) {
  const source = gridPosition(time, own.bpm, own.offset);
  const target = gridPosition(otherTime, other.bpm, other.offset);
  if (source === null || target === null) return time;
  const phase = ((target % 1) + 1) % 1;
  const step = 60 / own.bpm;
  let aligned = own.offset + (Math.round(source - phase) + phase) * step;
  if (aligned < 0) aligned += Math.ceil(-aligned / step) * step;
  if (aligned > duration) aligned -= Math.ceil((aligned - duration) / step) * step;
  return aligned;
}

function drawBand(ctx, x, center, height, values, width = 2) {
  const [low, mid, high] = values;
  const sum = low + mid + high;
  if (!sum) return;
  const amplitude = Math.min(1, Math.max(low, mid, high) / 255);
  const red = Math.round((low * 250 + mid * 100 + high * 70) / sum);
  const green = Math.round((low * 80 + mid * 215 + high * 155) / sum);
  const blue = Math.round((low * 55 + mid * 110 + high * 255) / sum);
  ctx.fillStyle = `rgb(${red},${green},${blue})`;
  const size = Math.max(1, amplitude * height);
  ctx.fillRect(x, center - size / 2, width, size);
}

export function overviewRenderer(analysis) {
  if (!analysis?.waveform) return undefined;
  return (_peaks, ctx) => {
    const { bands } = analysis.waveform;
    const { width, height } = ctx.canvas;
    for (let x = 0; x < width; x += 2) {
      const index = Math.min(bands[0].length - 1, Math.floor(x / width * bands[0].length));
      drawBand(ctx, x, height / 2, height, bands.map(band => band[index] || 0));
    }
  };
}

export class Timeline {
  constructor(container, deck) {
    this.deck = deck;
    this.canvas = document.createElement('canvas');
    this.canvas.setAttribute('aria-label', `Forme d'onde et grille de la platine ${deck.letter}`);
    this.canvas.tabIndex = 0;
    container.append(this.canvas);
    this.seconds = 8;
    this.canvas.addEventListener('click', event => {
      const box = this.canvas.getBoundingClientRect();
      const at = deck.position() + ((event.clientX - box.left) / box.width - .35) * this.seconds * deck.rate;
      const cue = deck.hotcues.find(t => t !== null && Math.abs(t - at) / deck.rate < this.seconds * 12 / box.width);
      deck.seek(cue ?? at);
    });
    this.canvas.addEventListener('keydown', event => {
      if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {
        event.preventDefault();
        deck.seek(deck.position() + (event.key === 'ArrowRight' ? 1 : -1) * 60 / (deck.grid().bpm || 120));
      }
    });
  }

  draw(seconds = 8) {
    this.seconds = seconds;
    const deck = this.deck;
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width) return;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const width = Math.round(rect.width * dpr), height = Math.round(rect.height * dpr);
    if (this.canvas.width !== width || this.canvas.height !== height) { this.canvas.width = width; this.canvas.height = height; }
    const ctx = this.canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const w = rect.width, h = rect.height;
    ctx.fillStyle = '#111111'; ctx.fillRect(0, 0, w, h);
    const pps = w / seconds;
    const head = w * .35, position = deck.position();
    const start = position - head / pps * deck.rate;
    const end = start + seconds * deck.rate;
    const xFor = t => head + (t - position) / deck.rate * pps;
    const waveform = deck.analysisData?.analysis?.waveform;
    const raw = !waveform && deck.wave?.getDecodedData();
    for (let x = 0; x < w; x += 2) {
      const t = start + x / pps * deck.rate;
      if (t < 0 || t > (deck.wave?.getDuration() || 0)) continue;
      if (waveform) {
        const i = Math.floor(t * waveform.rate);
        drawBand(ctx, x, h / 2 + 8, h - 40, waveform.bands.map(band => band[i] || 0));
      } else if (raw) {
        const channel = raw.getChannelData(0);
        const i = Math.floor(t * raw.sampleRate);
        const amplitude = Math.abs(channel[Math.min(channel.length - 1, i)]) * 220;
        drawBand(ctx, x, h / 2 + 8, h - 40, [amplitude, amplitude, amplitude]);
      }
    }
    const grid = deck.grid();
    if (grid.bpm && Number.isFinite(grid.offset)) {
      const step = 60 / grid.bpm;
      const first = Math.ceil((Math.max(0, start) - grid.offset) / step);
      for (let beat = first; grid.offset + beat * step <= end; beat++) {
        const x = xFor(grid.offset + beat * step);
        const bar = ((beat % 4) + 4) % 4 === 0;
        ctx.strokeStyle = bar ? '#e7e7e7' : '#6b6b6b';
        ctx.lineWidth = bar ? 1.3 : .7;
        ctx.beginPath(); ctx.moveTo(x, 24); ctx.lineTo(x, h); ctx.stroke();
        if (bar) { ctx.fillStyle = '#d7d7d7'; ctx.font = '10px sans-serif'; ctx.fillText(String(Math.floor(beat / 4) + 1), x + 3, h - 5); }
      }
    }
    if (deck.loopStart !== null && deck.loopEnd !== null) {
      ctx.fillStyle = deck.looping ? '#ffffff22' : '#ffffff0a';
      ctx.fillRect(xFor(deck.loopStart), 24, xFor(deck.loopEnd) - xFor(deck.loopStart), h - 24);
    }
    const flags = deck.hotcues.map((t, i) => ({ t, i, x: xFor(t) })).filter(f => f.t !== null && f.x >= 0 && f.x <= w).sort((a, b) => a.x - b.x);
    let right = -1;
    for (const [index, flag] of flags.entries()) {
      ctx.strokeStyle = cueColors[flag.i]; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(flag.x, 20); ctx.lineTo(flag.x, h); ctx.stroke();
      const x = Math.min(w - (flags.length - index) * 16, Math.max(flag.x, right + 1));
      ctx.fillStyle = cueColors[flag.i]; ctx.fillRect(x, 3, 15, 16);
      ctx.fillStyle = '#111'; ctx.font = 'bold 11px sans-serif'; ctx.fillText('ABCDEFGH'[flag.i], x + 3, 15);
      right = x + 15;
    }
    ctx.strokeStyle = deck.index ? '#ef8eb3' : '#00d79d'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(head, 0); ctx.lineTo(head, h); ctx.stroke();
    ctx.fillStyle = '#ddd'; ctx.font = 'bold 12px sans-serif'; ctx.fillText(deck.letter, 8, 18);
  }

  destroy() { this.canvas.remove(); }
}
