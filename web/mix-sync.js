import { gridPosition } from './mix-waveforms.js?v=20260910-14';

export function beatSyncRate(bpm, masterBpm, masterRate) {
  if (![bpm, masterBpm, masterRate].every(Number.isFinite) || bpm <= 0 || masterBpm <= 0 || masterRate <= 0) return null;
  const rate = masterBpm * masterRate / bpm;
  return rate >= .84 - 1e-10 && rate <= 1.16 + 1e-10 ? Math.max(.84, Math.min(1.16, rate)) : null;
}

export function beatSyncCorrection(own, master, previous, elapsed) {
  const source = gridPosition(own.position, own.bpm, own.offset);
  const target = gridPosition(master.position, master.bpm, master.offset);
  if (source === null || target === null || !(elapsed > 0)) return 0;
  const phase = target - source;
  const error = (phase - Math.round(phase)) * 60 / own.bpm;
  // Small, filtered speed corrections avoid repeated seeks and abrupt pitch changes.
  const desired = Math.abs(error / own.rate) <= .003 ? 0 : Math.max(-.04 * own.rate, Math.min(.04 * own.rate, error / .8));
  return previous + (desired - previous) * (1 - Math.exp(-Math.min(elapsed, .25) / .3));
}
