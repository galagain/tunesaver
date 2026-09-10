export function beatSyncRate(bpm, masterBpm, masterRate) {
  if (![bpm, masterBpm, masterRate].every(Number.isFinite) || bpm <= 0 || masterBpm <= 0 || masterRate <= 0) return null;
  const rate = masterBpm * masterRate / bpm;
  return rate >= .84 - 1e-10 && rate <= 1.16 + 1e-10 ? Math.max(.84, Math.min(1.16, rate)) : null;
}
