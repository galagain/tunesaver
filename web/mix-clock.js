import SignalsmithStretch from './vendor/SignalsmithStretch.mjs';

SignalsmithStretch.moduleUrl = new URL('./vendor/SignalsmithStretch.mjs', import.meta.url).href;
const clocks = new WeakMap();
const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

function abortable(operation, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
    Promise.resolve(operation).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

async function readAudio(response, signal) {
  const limit = 128 * 1024 * 1024;
  if (Number(response.headers.get('content-length')) > limit) {
    await response.body?.cancel();
    throw new Error('Fichier trop volumineux pour le mix en mémoire.');
  }
  const reader = response.body.getReader(), chunks = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      size += value.length;
      if (size > limit) throw new Error('Fichier trop volumineux pour le mix en mémoire.');
      chunks.push(value);
    }
  } catch (error) { await reader.cancel().catch(() => {}); throw error; }
  finally { reader.releaseLock(); }
  const data = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.length; }
  return data.buffer;
}

// Commands arriving together share a render deadline, not separate media clocks.
export function audioDeadline(context) {
  const now = context.currentTime, previous = clocks.get(context);
  if (previous && now - previous.created < .012 && previous.at > now + .008) return previous.at;
  const at = Math.ceil((now + .035) * context.sampleRate / 128) * 128 / context.sampleRate;
  clocks.set(context, { created: now, at });
  return at;
}

// Media-compatible transport for WaveSurfer; streaming is kept until PCM is ready.
export class ClockMedia extends EventTarget {
  constructor(context, stretchFactory) {
    super();
    this.context = context;
    this.stretchFactory = stretchFactory;
    this.native = new Audio();
    this.output = context.createGain();
    this.nativeGain = context.createGain();
    this.stretchGain = context.createGain();
    this.nativeSource = context.createMediaElementSource(this.native);
    this.nativeSource.connect(this.nativeGain).connect(this.output);
    this.stretchGain.gain.value = 0;
    this.stretchGain.connect(this.output);
    this.buffer = null;
    this.precise = false;
    this.disposed = false;
    this.nodes = [];
    this.segments = [];
    this.loop = null;
    this.rate = 1;
    this.keylock = true;
    this.isPaused = true;
    this.finished = false;
    this.level = 1;
    this.silent = false;
    this.listeners = new AbortController();
    for (const name of ['play', 'pause', 'ended', 'timeupdate', 'durationchange', 'loadedmetadata', 'canplay', 'canplaythrough', 'seeking', 'seeked', 'emptied', 'error', 'ratechange']) {
      this.native.addEventListener(name, () => {
        if (!this.precise && !this.disposed) this.emit(name);
      }, { signal: this.listeners.signal });
    }
    this.timer = setInterval(() => {
      if (!this.precise || this.isPaused) return;
      if (this.positionAt(context.currentTime) >= this.duration) {
        this.pause(); this.finished = true; this.emit('ended');
      } else this.emit('timeupdate');
    }, 40);
  }

  emit(name) { this.dispatchEvent(new Event(name)); }
  get src() { return this.native.src; }
  set src(value) { this.native.src = value; }
  get currentSrc() { return this.native.currentSrc; }
  set preload(value) { this.native.preload = value; }
  get duration() { return this.buffer?.duration || this.native.duration; }
  get readyState() { return this.buffer ? 4 : this.native.readyState; }
  get error() { return this.native.error; }
  get seeking() { return this.precise ? false : this.native.seeking; }
  get paused() { return this.precise ? this.isPaused : this.native.paused; }
  get ended() { return this.precise ? this.finished : this.native.ended; }
  get currentTime() {
    if (this.pendingSeek && this.context.currentTime < this.pendingSeek.at) return this.pendingSeek.position;
    return this.positionAt(this.context.currentTime);
  }
  set currentTime(value) { this.seekAt(value); }
  get playbackRate() { return this.rate; }
  set playbackRate(value) {
    value = clamp(Number(value) || 1, .5, 2);
    if (value === this.rate) return;
    const at = audioDeadline(this.context), position = this.positionAt(at);
    this.rate = value;
    if (this.precise) this.schedule(position, at, !this.isPaused);
    else this.native.playbackRate = value;
    this.emit('ratechange');
  }
  get preservesPitch() { return this.keylock; }
  set preservesPitch(value) {
    if (this.keylock === Boolean(value)) return;
    this.keylock = Boolean(value);
    if (this.precise) {
      const at = audioDeadline(this.context);
      this.schedule(this.positionAt(at), at, !this.isPaused);
    } else this.native.preservesPitch = this.keylock;
  }
  get muted() { return this.silent; }
  set muted(value) { this.silent = Boolean(value); this.updateGain(); }
  get volume() { return this.level; }
  set volume(value) { this.level = clamp(Number(value), 0, 1); this.updateGain(); }
  updateGain() { this.output.gain.value = this.silent ? 0 : this.level; this.emit('volumechange'); }
  canPlayType(type) { return this.native.canPlayType(type); }
  removeAttribute(name) { this.native.removeAttribute(name); }
  hasAttribute(name) { return this.native.hasAttribute(name); }
  load() { if (!this.disposed) this.native.load(); }

  async prepare(url, externalSignal) {
    if (this.preparing) return this.preparing;
    const controller = new AbortController(), signal = controller.signal;
    const abort = () => controller.abort();
    externalSignal.addEventListener('abort', abort, { once: true });
    this.listeners.signal.addEventListener('abort', abort, { once: true });
    if (externalSignal.aborted || this.disposed) abort();
    const timer = setTimeout(() => controller.abort(new DOMException('Préparation audio trop longue.', 'TimeoutError')), 60000);
    this.preparing = (async () => {
      const response = await fetch(url, { signal, credentials: 'same-origin' });
      if (!response.ok) throw new Error('Impossible de préparer le son pour le mix.');
      const data = await readAudio(response, signal);
      signal.throwIfAborted();
      const buffer = await abortable(this.context.decodeAudioData(data), signal);
      signal.throwIfAborted();
      if (buffer.length * buffer.numberOfChannels * 4 > 256 * 1024 * 1024) throw new Error('Morceau trop long pour le mix en mémoire.');
      const stretch = await abortable(this.stretchFactory(), signal);
      signal.throwIfAborted();
      await abortable(stretch.dropBuffers(), signal);
      signal.throwIfAborted();
      const channels = Array.from({ length: Math.min(2, buffer.numberOfChannels) }, (_, i) => new Float32Array(buffer.getChannelData(i)));
      await abortable(stretch.addBuffers(channels, channels.map(c => c.buffer)), signal);
      signal.throwIfAborted();
      if (this.disposed) throw new DOMException('Chargement annulé', 'AbortError');
      this.stretch = stretch;
      stretch.connect(this.stretchGain);
      this.buffer = buffer;
      this.emit('clockready');
      return this;
    })().catch(error => { this.preparationError = error; throw error; }).finally(() => {
      clearTimeout(timer);
      externalSignal.removeEventListener('abort', abort);
      this.listeners.signal.removeEventListener('abort', abort);
    });
    return this.preparing;
  }

  positionAt(at) {
    if (!this.precise) return this.native.currentTime + (this.native.paused ? 0 : Math.max(0, at - this.context.currentTime) * this.rate);
    let segment = this.segments[0];
    for (const next of this.segments) { if (next.at <= at) segment = next; else break; }
    if (!segment) return 0;
    let position = segment.position + (segment.playing ? Math.max(0, at - segment.at) * segment.rate : 0);
    if (this.loop && position >= this.loop.end) position = this.loop.start + (position - this.loop.start) % (this.loop.end - this.loop.start);
    return clamp(position, 0, this.duration);
  }

  setLoop(start, end) {
    const loop = Number.isFinite(start) && Number.isFinite(end) && end > start ? { start, end } : null;
    if (this.loop?.start === loop?.start && this.loop?.end === loop?.end) return;
    const at = audioDeadline(this.context), position = this.positionAt(at);
    this.loop = loop;
    if (this.precise) this.schedule(position, at, !this.isPaused);
  }

  activate(at = audioDeadline(this.context)) {
    if (this.precise || !this.buffer) return;
    const position = this.positionAt(at), playing = !this.native.paused;
    this.precise = true;
    this.nativeGain.gain.setValueAtTime(0, at);
    this.schedule(position, at, playing);
    this.nativePauseTimer = setTimeout(() => this.native.pause(), Math.max(0, at - this.context.currentTime) * 1000);
  }

  schedule(position, at, playing) {
    position = clamp(position, 0, this.duration);
    const segment = { at, position, rate: this.rate, playing };
    this.segments = this.segments.filter(s => s.at < at);
    this.segments.push(segment);
    while (this.segments.length > 2 && this.segments[1].at <= this.context.currentTime) this.segments.shift();
    for (const node of this.nodes) {
      node.stopAt = Math.min(node.stopAt ?? Infinity, at);
      try { node.stop(node.stopAt); } catch { /* Already ended. */ }
    }
    const stretch = this.keylock && Math.abs(this.rate - 1) > 1e-9;
    this.stretchGain.gain.cancelScheduledValues(at);
    this.stretchGain.gain.setValueAtTime(playing && stretch ? 1 : 0, at);
    void this.stretch.schedule({ output: at, outputTime: at, input: position, active: playing && stretch, rate: this.rate, semitones: 0, loopStart: this.loop?.start || 0, loopEnd: this.loop?.end || 0 });
    if (playing && !stretch && position < this.duration) {
      const node = this.context.createBufferSource();
      node.buffer = this.buffer;
      node.playbackRate.value = this.rate;
      node.loop = Boolean(this.loop);
      if (this.loop) { node.loopStart = this.loop.start; node.loopEnd = this.loop.end; }
      node.connect(this.output);
      node.onended = () => { node.disconnect(); this.nodes = this.nodes.filter(n => n !== node); };
      node.start(at, position);
      this.nodes.push(node);
    }
    this.isPaused = !playing;
    this.finished = false;
  }

  seekAt(position, at = audioDeadline(this.context), play = !this.paused) {
    if (this.disposed) return;
    if (!play) at = this.context.currentTime;
    this.activate(at);
    if (!this.precise) { this.native.currentTime = position; if (play) return this.native.play(); return; }
    this.pendingSeek = { position: clamp(position, 0, this.duration), at };
    this.schedule(position, at, play);
    this.emit('seeking'); this.emit('timeupdate'); this.emit('seeked');
    if (play) this.emit('play');
  }

  async play() {
    if (this.disposed) return;
    const at = audioDeadline(this.context);
    this.activate(at);
    if (!this.precise) return this.native.play();
    if (!this.isPaused) return;
    this.schedule(this.ended ? 0 : this.positionAt(at), at, true);
    this.emit('play');
  }

  pause() {
    if (this.disposed) return;
    if (!this.precise) { this.native.pause(); return; }
    if (this.isPaused) return;
    const at = this.context.currentTime;
    this.pendingSeek = null;
    this.schedule(this.positionAt(at), at, false);
    this.emit('pause');
  }

  dispose() {
    if (this.disposed) return;
    this.pause();
    this.disposed = true;
    this.listeners.abort();
    clearInterval(this.timer); clearTimeout(this.nativePauseTimer);
    this.native.pause(); this.native.removeAttribute('src'); this.native.load();
    this.nodes.forEach(n => { n.onended = null; n.disconnect(); });
    this.nodes = []; this.buffer = null;
    this.nativeSource.disconnect(); this.nativeGain.disconnect(); this.stretchGain.disconnect(); this.output.disconnect();
    if (this.stretch) { this.stretch.disconnect(this.stretchGain); void this.stretch.dropBuffers(); }
  }
}

export async function createStretch(context) {
  let timer;
  try {
    return await Promise.race([
      (async () => {
        const node = await SignalsmithStretch(context, { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2] });
        await node.configure({ blockMs: 40, intervalMs: 10 });
        return node;
      })(),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Le moteur audio ne démarre pas. Recharge la page.')), 15000); }),
    ]);
  } finally { clearTimeout(timer); }
}
