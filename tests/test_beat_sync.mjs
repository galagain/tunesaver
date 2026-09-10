import test from 'node:test';
import assert from 'node:assert/strict';
import { beatSyncRate } from '../web/mix-sync.js';

test('tempo ratios keep full precision and reject unavailable or out-of-range tempos', () => {
  assert.equal(beatSyncRate(127.3, 128.7, 1.037), 128.7 * 1.037 / 127.3);
  assert.equal(beatSyncRate(100, 116, 1), 1.16);
  assert.equal(beatSyncRate(100, 84, 1), .84);
  for (const values of [[null, 120, 1], [0, 120, 1], [120, NaN, 1], [120, 128, Infinity], [100, 117, 1], [100, 83, 1]]) {
    assert.equal(beatSyncRate(...values), null);
  }
});
