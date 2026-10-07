const {chromium, expect} = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.route('**/api/**', route => {
      if (new URL(route.request().url()).pathname === '/api/config') return route.continue();
      return route.fulfill({json: {tracks: [], playlists: [], imports: [], tags: [], orders: {}, active: {}, counts: {}, recent: []}});
    });
    await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
    await page.waitForFunction(() => publicConfig !== null);
    const result = await page.evaluate(() => {
      selectedPlaylistId = 'set';
      selectedTagId = '';
      libraryState = {
        tracks: ['four', 'three', 'two', 'one'].map(job_id => ({job_id, state: 'done'})),
        playlists: [{id: 'set', track_ids: ['one', 'two', 'three', 'four']}],
        tags: [], imports: [], orders: {'playlist:set': ['two', 'one']},
      };
      const saved = manualLibraryTracks().map(t => t.job_id);
      libraryState.orders = {};
      const natural = manualLibraryTracks().map(t => t.job_id);
      libraryState.orders = {'playlist:set': []};
      const empty = manualLibraryTracks().map(t => t.job_id);
      libraryState.tracks.reverse();
      const refreshed = manualLibraryTracks().map(t => t.job_id);
      return {saved, natural, empty, refreshed};
    });
    expect(result.saved).toEqual(['two', 'one', 'three', 'four']);
    for (const key of ['natural', 'empty', 'refreshed']) expect(result[key]).toEqual(['one', 'two', 'three', 'four']);
    console.log('New tracks follow addition order after saved positions, independent of library order OK');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
