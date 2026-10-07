const { chromium } = require('@playwright/test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: width === 390 ? 844 : 960}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(pathToFileURL(process.argv[2]).href);
      const data = await page.locator('#viewer-data').textContent().then(JSON.parse);
      assert.equal(await page.locator('.scenario').count(), data.scenarios.length);
      for (let index = 0; index < data.scenarios.length; index++) {
        await page.locator(`.scenario[data-index="${index}"]`).click();
        assert.equal(await page.locator('#tracks tr').count(), data.scenarios[index].payload?.tracks.length || 0);
      }
      await page.locator('.scenario[data-index="0"]').click();
      await page.locator('#track-search').fill('no-such-track-xyz');
      assert.equal(await page.locator('#tracks tr').count(), 0);
      await page.locator('#track-search').fill('');
      assert.equal(await page.locator('#tracks tr').count(), data.scenarios[0].payload.tracks.length);
      await page.locator('[data-sort="number"]').click();
      assert.equal(await page.locator('#tracks tr:first-child td:first-child').textContent(), String(data.scenarios[0].payload.total));
      await page.locator('[data-sort="number"]').click();
      await page.locator('[data-filter="direct"]').click();
      assert.equal(await page.locator('#tracks tr').count(), data.scenarios[0].payload.tracks.filter(t => t.target && !t.requires_matching && !t.unavailable).length);
      await page.locator('[data-filter="pending"]').click();
      assert.equal(await page.locator('#tracks tr').count(), data.scenarios[0].payload.tracks.filter(t => !t.unavailable && (!t.target || t.requires_matching)).length);
      if (data.phase === 'matching') assert.match(await page.locator('#run-status').textContent(), /complets/);
      const nativeIndex = data.scenarios.findIndex(s => s.result.source === 'beatport' && s.result.kind === 'playlist');
      await page.locator(`.scenario[data-index="${nativeIndex}"]`).click();
      await page.locator('[data-filter="direct"]').click();
      assert.equal(await page.locator('#tracks tr').count(), data.scenarios[nativeIndex].payload.total);
      assert.match(await page.locator('#tracks a').first().getAttribute('href'), /^https:\/\/www.beatport.com\/track\//);
      const download = page.waitForEvent('download');
      await page.locator('#export').click();
      assert.equal((await download).suggestedFilename(), 'beatport-playlist.json');
      await page.locator('#scenario-search').fill('qobuz');
      assert.equal(await page.locator('.scenario').count(), 3);
      await page.locator('#scenario-search').fill('');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
      assert.equal(await page.evaluate(() => document.querySelector('main').getBoundingClientRect().right <= innerWidth), true);
      await page.screenshot({path: `/work/test-viewer-${width}.png`});
      await page.evaluate(() => {
        data.scenarios.push({result:{source:'qobuz',kind:'album',status:'failed',url:'https://qobuz.com',error:'Indisponible'},payload:null});
        select(data.scenarios.length - 1);
      });
      assert.equal(await page.locator('#empty').textContent(), 'Indisponible');
      assert.equal(await page.locator('#export').isDisabled(), true);
      assert.deepEqual(errors, []);
      console.log(`OK ${width}px: ${data.scenarios.length} scenarios, filters, sort, source links, JSON export`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
