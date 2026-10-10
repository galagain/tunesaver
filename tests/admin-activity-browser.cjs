const {chromium, expect} = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path.endsWith('/profiles')) return route.fulfill({json: {profiles: [
          {user_id: 'one', email: 'recent@example.com', approved: true, last_download_at: '2026-10-08T11:00:00Z'},
          {user_id: 'two', email: 'older@example.com', approved: true, last_download_at: '2026-10-07T11:00:00Z'},
        ]}});
        if (path.endsWith('/stats')) return route.fulfill({json: {daily: [
          {date: '2026-10-07', tracks: 0, savings_cents: 0, users: []},
          {date: '2026-10-08', tracks: 4, savings_cents: 600, users: [
            {user_id: 'one', email: 'recent@example.com', tracks: 3},
            {user_id: 'two', email: 'older@example.com', tracks: 1},
          ]},
        ]}});
        return route.fulfill({json: {history: []}});
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {adminSession = {access_token: 'fixture'}; showAdmin();});
      await expect(page.locator('.admin-user').first()).toContainText('recent@example.com');
      await page.locator('.chart-day-hit').last().click();
      await expect(page.locator('#admin-day-dialog')).toBeVisible();
      await expect(page.locator('#admin-day-users')).toContainText('75 %');
      await expect(page.locator('#admin-day-users')).toContainText('25 %');
      await expect(page.locator('#admin-day-total')).toHaveText('4 sons téléchargés');
      expect(await page.locator('#admin-day-pie').evaluate(el => getComputedStyle(el).backgroundImage)).toContain('conic-gradient');
      await page.screenshot({path: `/work/admin-activity-${width}.png`});
      await page.keyboard.press('Escape');
      await expect(page.locator('#admin-day-dialog')).not.toBeVisible();
      await page.locator('#admin-chart-savings').click();
      await page.locator('.chart-day-hit').first().focus();
      await page.keyboard.press('Enter');
      await expect(page.locator('#admin-day-users')).toHaveText('Aucun téléchargement ce jour.');
      await page.locator('#admin-day-close').click();
      await page.evaluate(() => renderAdminHistory([
        {job_id: 'ok', title: 'Done', state: 'done', provider: 'tiddl'},
        {job_id: 'failed', title: 'Failed song', artist: 'Artist', state: 'failed', provider: 'qobuz',
         error: 'Acces refuse', source_url: 'https://play.qobuz.com/track/123'},
        {job_id: 'missing', title: 'Missing', state: 'failed', provider: 'import', error: 'Aucune correspondance', source_url: 'javascript:alert(1)'},
      ]));
      await page.locator('#admin-history-filter').selectOption('failed');
      await expect(page.locator('.admin-track')).toHaveCount(2);
      await expect(page.locator('#admin-history')).toContainText('Acces refuse');
      await expect(page.locator('#admin-history a')).toHaveCount(1);
      await expect(page.locator('#admin-history a')).toHaveAttribute('href', 'https://play.qobuz.com/track/123');
      await page.locator('#admin-history').scrollIntoViewIfNeeded();
      await page.screenshot({path: `/work/admin-failures-${width}.png`});
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: daily proportions, empty day, keyboard, close, savings mode OK`);
    }
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
