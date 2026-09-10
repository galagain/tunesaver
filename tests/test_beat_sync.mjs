import test from 'node:test';
import assert from 'node:assert/strict';
import { beatSyncRate, beatSyncCorrection } from '../web/mix-sync.js';

test('tempo ratios keep full precision and reject unavailable or out-of-range tempos', () => {
  assert.equal(beatSyncRate(127.3, 128.7, 1.037), 128.7 * 1.037 / 127.3);
  assert.equal(beatSyncRate(100, 116, 1), 1.16);
  assert.equal(beatSyncRate(100, 84, 1), .84);
  for (const values of [[null, 120, 1], [0, 120, 1], [120, NaN, 1], [120, 128, Infinity], [100, 117, 1], [100, 83, 1]]) {
    assert.equal(beatSyncRate(...values), null);
  }
});

test('phase corrections take the nearest beat and stay bounded', () => {
  const own = { bpm: 120, offset: 0, position: .48, rate: 1 }, master = { bpm: 120, offset: 0, position: 0 };
  const forward = beatSyncCorrection(own, master, 0, .05);
  assert.ok(forward > 0 && forward < .04);
  assert.ok(beatSyncCorrection({ ...own, position: .02 }, master, 0, .05) < 0);
  assert.equal(beatSyncCorrection({ ...own, position: .001 }, master, 0, .05), 0);
  assert.ok(Math.abs(beatSyncCorrection({ ...own, position: .25 }, master, 0, 100)) <= .04);
});

test('a ten-minute mix follows tempo changes and corrects clock drift without seeking', () => {
  const own = { bpm: 127.3, offset: .13, position: .19, rate: 1 };
  const master = { bpm: 128.7, offset: .07, position: .07, rate: 1 };
  let correction = 0, maximumError = 0;
  for (let i = 0; i < 12000; i++) {
    const dt = i % 11 === 0 ? .08 : .05;
    master.rate = i < 4000 ? 1 : i < 8000 ? 1.06 : .95;
    own.rate = beatSyncRate(own.bpm, master.bpm, master.rate);
    correction = beatSyncCorrection(own, master, correction, dt);
    own.position += (own.rate + correction) * 1.001 * dt;
    master.position += master.rate * dt;
    const phase = (master.position - master.offset) * master.bpm / 60 - (own.position - own.offset) * own.bpm / 60;
    const error = Math.abs(phase - Math.round(phase)) * 60 / (master.bpm * master.rate);
    if (i > 150) maximumError = Math.max(maximumError, error);
    assert.ok(Math.abs(correction) <= own.rate * .04 + 1e-9);
  }
  assert.ok(maximumError < .006, `Drift reached ${maximumError * 1000} ms`);
});
