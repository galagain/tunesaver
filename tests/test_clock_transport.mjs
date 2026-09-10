import test from 'node:test';
import assert from 'node:assert/strict';
import { ClockMedia, audioDeadline } from '../web/mix-clock.js';

class Media extends EventTarget {
  currentTime = 0; duration = 12; paused = true; playbackRate = 1;
  play() { this.paused = false; this.dispatchEvent(new Event('play')); return Promise.resolve(); }
  pause() { this.paused = true; this.dispatchEvent(new Event('pause')); }
  load() {}
  removeAttribute() {}
}
const node = () => ({ gain: { value: 1, setValueAtTime() {}, cancelScheduledValues() {} }, playbackRate: { value: 1 },
  connect(target) { return target; }, disconnect() {}, start(at) { this.started = at; }, stop(at) { this.stopped = at; } });
function setup() {
  globalThis.Audio = Media;
  const context = { currentTime: 1, sampleRate: 44100, createGain: node, createMediaElementSource: node, createBufferSource: node };
  const stretch = { schedule() { return Promise.resolve(); }, dropBuffers() { return Promise.resolve(); }, addBuffers() { return Promise.resolve(); }, connect() {}, disconnect() {} };
  const player = new ClockMedia(context, async () => stretch);
  player.buffer = { duration: 12 };
  player.stretch = stretch;
  return { context, player, stretch };
}

test('paired commands share a future audio deadline within the same context', () => {
  const { context, player } = setup();
  try {
    const first = audioDeadline(context);
    context.currentTime += .008;
    assert.equal(audioDeadline(context), first);
    context.currentTime += .02;
    assert.ok(audioDeadline(context) > first);
  } finally { player.dispose(); }
});

test('rescheduling never extends an old source into its replacement', () => {
  const { context, player } = setup();
  try {
    player.seekAt(2, 1.04, true);
    const first = player.nodes.at(-1);
    player.seekAt(3, 1.05, true);
    player.seekAt(4, 1.06, true);
    assert.equal(first.stopped, 1.05);
    context.currentTime = 1.2;
    assert.ok(Math.abs(player.currentTime - 4.14) < 1e-9);
    player.pause();
    const paused = player.currentTime;
    context.currentTime += 2;
    assert.equal(player.currentTime, paused);
  } finally { player.dispose(); }
});

test('a jog hold cancels a pending seek and future playback without a stale UI position', () => {
  const { context, player } = setup();
  try {
    player.seekAt(2, 1.04, true);
    context.currentTime = 1.2;
    player.seekAt(6, 1.24, true);
    assert.equal(player.currentTime, 6);
    player.pause();
    const held = player.currentTime;
    context.currentTime += .5;
    assert.equal(player.currentTime, held);
    assert.ok(held < 3);
  } finally { player.dispose(); }
});

test('loops use the audio clock instead of a UI interval to restart playback', () => {
  const { context, player } = setup();
  try {
    player.setLoop(2, 3);
    player.seekAt(2, 1.04, true);
    assert.equal(player.nodes.at(-1).loop, true);
    context.currentTime = 101.54;
    assert.ok(Math.abs(player.currentTime - 2.5) < 1e-9);
  } finally { player.dispose(); }
});

test('an aborted preparation cannot append old audio to a reused worklet', async () => {
  const { context, player, stretch } = setup(), originalFetch = globalThis.fetch;
  try {
    player.buffer = null;
    globalThis.fetch = async () => new Response(new Uint8Array(4));
    context.decodeAudioData = async () => ({ duration: 12, length: 4, numberOfChannels: 1, getChannelData: () => new Float32Array(4) });
    let dropped, added = false;
    stretch.dropBuffers = () => new Promise(resolve => { dropped = resolve; });
    stretch.addBuffers = async () => { added = true; };
    const controller = new AbortController();
    const preparation = player.prepare('/test.wav', controller.signal);
    while (!dropped) await new Promise(resolve => setTimeout(resolve, 0));
    controller.abort(); dropped();
    await assert.rejects(preparation, { name: 'AbortError' });
    assert.equal(added, false);
  } finally { globalThis.fetch = originalFetch; player.dispose(); }
});

test('oversized files are rejected before audio allocation, keeping streaming available', async () => {
  const { player } = setup(), originalFetch = globalThis.fetch;
  try {
    player.buffer = null;
    globalThis.fetch = async () => new Response(new Uint8Array(4), { headers: { 'content-length': String(129 * 1024 * 1024) } });
    await assert.rejects(player.prepare('/large.wav', new AbortController().signal), /volumineux/);
    await player.play();
    assert.equal(player.precise, false);
    assert.equal(player.native.paused, false);
  } finally { globalThis.fetch = originalFetch; player.dispose(); }
});

test('disposing while decoding rejects preparation immediately', async () => {
  const { context, player } = setup(), originalFetch = globalThis.fetch;
  try {
    player.buffer = null;
    globalThis.fetch = async () => new Response(new Uint8Array(4));
    let decoding = false;
    context.decodeAudioData = () => { decoding = true; return new Promise(() => {}); };
    const preparation = player.prepare('/test.wav', new AbortController().signal);
    while (!decoding) await new Promise(resolve => setTimeout(resolve, 0));
    player.dispose();
    await assert.rejects(preparation, { name: 'AbortError' });
  } finally { globalThis.fetch = originalFetch; player.dispose(); }
});
