import test from 'node:test';
import assert from 'node:assert/strict';
import { FLX4Decoder, crossfade, isFLX4Port } from '../web/flx4.js';
import { gridPosition, phaseAlignedTime, peakRange, unpackWaveform, detectedGrid, interpolatedPeak, overviewEnvelope } from '../web/mix-waveforms.js';

test('overview pools fine detail but preserves silence and broad dynamic changes', () => {
  const frames = 4096, bytes = new Uint8Array(frames * 5);
  for (let i = 1024; i < frames; i++) {
    const amplitude = i < 2048 ? 40 : i % 2 ? 220 : 180;
    bytes[i * 5 + 3] = amplitude; bytes[i * 5 + 4] = amplitude;
  }
  const levels = overviewEnvelope({ waveform: { encoding: 'u8-lmhpn', frames, data: Buffer.from(bytes).toString('base64') } });
  assert.equal(levels.length, 256);
  assert.equal(levels[20], 0);
  assert.ok(levels[90] > 0 && levels[90] < levels[180] / 2);
  assert.ok(levels.every(n => Number.isFinite(n) && n >= 0 && n <= 1));
  assert.ok(Math.max(...levels.slice(150, 220)) - Math.min(...levels.slice(150, 220)) < .001);
});

test('overview has a decoded-audio fallback and does not invent energy in silence', () => {
  assert.deepEqual(overviewEnvelope(null), []);
  assert.deepEqual(overviewEnvelope(null, [new Float32Array(64)]), Array(64).fill(0));
  const samples = Float32Array.from({ length: 4096 }, (_, i) => i < 2048 ? 0 : Math.sin(i) * .5);
  const levels = overviewEnvelope(null, [samples]);
  assert.equal(levels[30], 0);
  assert.ok(levels[180] > .9);
});

test('variable grids use adjacent detected beats, not the average track tempo', () => {
  assert.deepEqual(detectedGrid([0, .5, 1.1, 1.8], .2), { bpm: 120, offset: 0 });
  assert.ok(Math.abs(detectedGrid([0, .5, 1.1, 1.8], .7).bpm - 100) < 1e-9);
  assert.equal(detectedGrid([0, .5, 1.1], 5).offset, .5);
  assert.equal(detectedGrid([], .2), null);
});

test('Firefox and OS MIDI name variants are recognized without guessing generic devices', () => {
  for (const name of ['DDJ-FLX4', 'Pioneer DDJ\u2011FLX4 MIDI 1', 'FLX4', 'DDJ FLX 4']) assert.ok(isFLX4Port({ name }));
  assert.equal(isFLX4Port({ name: 'USB MIDI Interface' }), false);
  assert.equal(isFLX4Port({ name: 'DDJ-FLX6' }), false);
});

test('waveform downsampling preserves narrow transients and signed extrema', () => {
  const bytes = new Uint8Array(100 * 5); bytes[53 * 5 + 3] = 255; bytes[54 * 5 + 4] = 180;
  assert.deepEqual(peakRange(bytes, 0, 100).slice(3), [255, 180]);
  const waveform = { encoding: 'u8-lmhpn', frames: 100, data: Buffer.from(bytes).toString('base64') };
  assert.deepEqual(unpackWaveform(waveform), bytes);
  assert.equal(unpackWaveform(waveform), unpackWaveform(waveform));
});

test('zoomed waveform contours interpolate signed extrema without overshoot or repeating outside the track', () => {
  const bytes = new Uint8Array([20, 50, 90, 100, 0, 40, 90, 50, 200, 100]);
  assert.deepEqual(interpolatedPeak(bytes, 0), [20, 50, 90, 100, 0]);
  assert.deepEqual(interpolatedPeak(bytes, .25), [25, 60, 80, 125, 25]);
  assert.deepEqual(interpolatedPeak(bytes, .5), [30, 70, 70, 150, 50]);
  assert.deepEqual(interpolatedPeak(bytes, 1.5), [40, 90, 50, 200, 100]);
  assert.deepEqual(interpolatedPeak(bytes, -.01), [0, 0, 0, 0, 0]);
  assert.deepEqual(interpolatedPeak(bytes, 2), [0, 0, 0, 0, 0]);
});

test('browser push selects collections then tracks, shift push goes back', () => {
  const d = new FLX4Decoder();
  assert.deepEqual(d.decode([0x96, 0x41, 127]), { action: 'browseFocus', back: false });
  assert.deepEqual(d.decode([0x96, 0x42, 127]), { action: 'browseFocus', back: true });
  assert.equal(d.decode([0x96, 0x41, 0]), null);
  assert.equal(d.decode([0x86, 0x41, 127]), null);
});

test('beat grids align the nearest beat phase, including before the anchor', () => {
  const own = { bpm: 120, offset: .25 }, other = { bpm: 150, offset: .1 };
  assert.equal(gridPosition(.75, 120, .25), 1);
  for (const [at, target] of [[10.2, 5.13], [.1, .05], [0, 0]]) {
    const aligned = phaseAlignedTime(at, own, target, other);
    const difference = gridPosition(aligned, own.bpm, own.offset) - gridPosition(target, other.bpm, other.offset);
    assert.ok(Math.abs(difference - Math.round(difference)) < 1e-9);
    assert.ok(Math.abs(aligned - at) <= .25);
  }
  assert.equal(phaseAlignedTime(2, { bpm: null, offset: 0 }, 3, other), 2);
  const grid = { bpm: 120, offset: 0 };
  assert.ok(Math.abs(phaseAlignedTime(0, grid, .4, grid, 10) - .4) < 1e-9);
  assert.ok(Math.abs(phaseAlignedTime(10, grid, .1, grid, 10) - 9.6) < 1e-9);
});

test('play, cue and load address the correct deck; releases never retrigger', () => {
  const d = new FLX4Decoder();
  assert.deepEqual(d.decode([0x90, 0x0b, 127]), { action: 'play', deck: 0 });
  assert.deepEqual(d.decode([0x91, 0x0c, 127]), { action: 'cue', deck: 1 });
  assert.deepEqual(d.decode([0x96, 0x47, 127]), { action: 'load', deck: 1 });
  assert.equal(d.decode([0x90, 0x0b, 0]), null);
  assert.equal(d.decode([0x80, 0x0b, 127]), null);
});

test('14-bit controls retain MSB per channel and ignore orphan LSB', () => {
  const d = new FLX4Decoder();
  assert.equal(d.decode([0xb0, 0x20, 40]), null);
  assert.equal(d.decode([0xb0, 0x00, 64]).raw, 8192);
  assert.equal(d.decode([0xb1, 0x00, 32]).raw, 4096);
  assert.equal(d.decode([0xb0, 0x20, 5]).raw, 8197);
  assert.equal(d.decode([0xb1, 0x20, 2]).raw, 4098);
  assert.equal(d.decode([0xb6, 0x1f, 127]).action, 'cross');
  assert.equal(d.decode([0xb6, 0x3f, 127]).value, 1);
  assert.equal(d.decode([0xb0, 0x13, 0]).action, 'volume');
  assert.equal(d.decode([0xb0, 0x33, 0]).value, 0);
});

test('jogs and browser encoders have different relative encodings', () => {
  const d = new FLX4Decoder();
  assert.deepEqual(d.decode([0xb1, 0x21, 63]), { action: 'jog', deck: 1, value: -1 });
  assert.deepEqual(d.decode([0xb0, 0x29, 65]), { action: 'seek', deck: 0, value: 1 });
  assert.deepEqual(d.decode([0xb6, 0x40, 127]), { action: 'browse', value: -1 });
  assert.equal(d.decode([0xb6, 0x40, 1]).value, 1);
});

test('hotcue pads, shift deletion, filter and unsupported messages', () => {
  const d = new FLX4Decoder();
  assert.deepEqual(d.decode([0x97, 7, 127]), { action: 'hotcue', deck: 0, pad: 7, clear: false });
  assert.deepEqual(d.decode([0x9a, 2, 127]), { action: 'hotcue', deck: 1, pad: 2, clear: true });
  assert.equal(d.decode([0xb6, 0x17, 64]).action, 'filterA');
  assert.equal(d.decode([0xb6, 0x18, 64]).action, 'filterB');
  for (const data of [[0x97, 0x30, 127], [0x92, 11, 127], [0xf8], [0x90, 11, 256], [0x90, 11, -1]]) assert.equal(d.decode(data), null);
});

test('equal-power crossfader has silent ends and constant squared gain', () => {
  for (const value of [0, 0.25, 0.5, 0.75, 1]) {
    const [a, b] = crossfade(value);
    assert.ok(Math.abs(a * a + b * b - 1) < 1e-10);
  }
  assert.equal(crossfade(0)[1], 0);
  assert.ok(crossfade(1)[0] < 1e-10);
});
